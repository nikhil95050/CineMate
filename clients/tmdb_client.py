"""TMDB (The Movie Database) API v3 client.

Implements MovieMetadataProvider with full circuit-breaker and health integration.
Provides richer data than OMDb: cast, crew, trailers, backdrops, popularity scores.

Environment:
    TMDB_API_KEY -- API v3 key from https://www.themoviedb.org/settings/api
    TMDB_API_BASE_URL -- optional override (default: https://api.themoviedb.org/3)
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx

from clients.provider_base import MovieMetadataProvider
from services.logging_service import LoggingService, error_batcher
from utils.time_utils import utc_now_iso

TMDB_BASE_URL = os.environ.get("TMDB_API_BASE_URL", "https://api.themoviedb.org/3").rstrip("/")
IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
POSTER_SIZE = "w500"
BACKDROP_SIZE = "w1280"


def poster_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return f"{IMAGE_BASE_URL}/{POSTER_SIZE}{path}"


def backdrop_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return f"{IMAGE_BASE_URL}/{BACKDROP_SIZE}{path}"


class TmdbClient(MovieMetadataProvider):
    provider_name = "tmdb"
    daily_budget = 1000

    def _api_key(self) -> str:
        return os.environ.get("TMDB_API_KEY", "").strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key()}",
            "accept": "application/json",
        }

    def _normalize_movie(self, data: dict[str, Any]) -> dict[str, Any]:
        genres = ", ".join(g.get("name", "") for g in data.get("genres", []) or [])
        production_countries = data.get("production_countries", []) or []
        language = (
            data.get("original_language", "en").upper()
            if not production_countries
            else ", ".join(pc.get("iso_3166_1", "").upper() for pc in production_countries)
        )

        return {
            "imdb_id": data.get("imdb_id") or "",
            "tmdb_id": str(data.get("id", "")),
            "title": data.get("title") or data.get("original_title", ""),
            "year": (data.get("release_date") or "")[:4] or None,
            "rating": float(data["vote_average"]) if data.get("vote_average") else None,
            "vote_count": data.get("vote_count", 0),
            "genres": genres or None,
            "language": language,
            "description": data.get("overview") or None,
            "poster_url": poster_url(data.get("poster_path")),
            "backdrop_url": backdrop_url(data.get("backdrop_path")),
            "trailer_url": None,
            "cast": [],
            "director": None,
            "runtime": data.get("runtime"),
            "popularity": data.get("popularity"),
            "source": "tmdb",
        }

    async def _request(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        url = f"{TMDB_BASE_URL}/{endpoint.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=self._headers(), params=params or {})
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                self.logger.debug("TMDB 404 for %s", endpoint)
                return None
            self._report_failure()
            self._emit_error(
                chat_id="system",
                error_type=f"tmdb_http_{exc.response.status_code}",
                message=exc.response.text[:500],
                step=f"tmdb_client._request:{endpoint}",
            )
            return None
        except Exception as exc:
            self._report_failure()
            self._emit_error(
                chat_id="system",
                error_type="tmdb_request_failed",
                message=str(exc),
                step=f"tmdb_client._request:{endpoint}",
            )
            return None

    async def get_by_title(
        self,
        title: str,
        year: Optional[str] = None,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        api_key = self._api_key()
        if not api_key or not title or not title.strip():
            return None
        if not await self._check_health():
            return None

        params: dict[str, Any] = {"query": title.strip()}
        if year:
            params["year"] = str(year)[:4]

        data = await self._request("search/movie", params=params, timeout=timeout)
        if not data or not data.get("results"):
            self._report_success()
            self._log_usage("get_by_title:miss", chat_id=chat_id)
            return None

        movie_id = data["results"][0]["id"]
        details = await self._request(f"movie/{movie_id}", params={
            "append_to_response": "videos"
        }, timeout=timeout)

        if not details:
            self._report_success()
            return self._normalize_movie(data["results"][0])

        result = self._normalize_movie(details)

        videos = details.get("videos", {}).get("results", [])
        for v in videos:
            if v.get("type") == "Trailer" and v.get("site") == "YouTube":
                result["trailer_url"] = f"https://www.youtube.com/watch?v={v['key']}"
                break

        self._report_success()
        self._log_usage("get_by_title:hit", chat_id=chat_id)
        return result

    async def get_by_id(
        self,
        movie_id: str,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        api_key = self._api_key()
        if not api_key or not movie_id:
            return None
        if not await self._check_health():
            return None

        endpoint = f"movie/{movie_id}"
        data = await self._request(endpoint, params={
            "append_to_response": "videos,credits"
        }, timeout=timeout)

        if not data:
            self._report_success()
            self._log_usage("get_by_id:miss", chat_id=chat_id)
            return None

        result = self._normalize_movie(data)

        credits = data.get("credits", {})
        cast_list = credits.get("cast", [])[:5]
        result["cast"] = [c.get("name", "") for c in cast_list if c.get("name")]

        crew = credits.get("crew", [])
        for c in crew:
            if c.get("job") == "Director":
                result["director"] = c.get("name")
                break

        videos = data.get("videos", {}).get("results", [])
        for v in videos:
            if v.get("type") == "Trailer" and v.get("site") == "YouTube":
                result["trailer_url"] = f"https://www.youtube.com/watch?v={v['key']}"
                break

        self._report_success()
        self._log_usage("get_by_id:hit", chat_id=chat_id)
        return result

    async def search(
        self,
        query: str,
        chat_id: str = "system",
        limit: int = 5,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        api_key = self._api_key()
        if not api_key or not query or not query.strip():
            return []
        if not await self._check_health():
            return []

        data = await self._request("search/movie", params={
            "query": query.strip(),
        }, timeout=timeout)

        if not data or not data.get("results"):
            self._report_success()
            self._log_usage("search:miss", chat_id=chat_id)
            return []

        results = [_normalize_search_result(r) for r in data["results"][:limit]]

        self._report_success()
        self._log_usage("search:hit", chat_id=chat_id)
        return results

    async def get_trending(
        self,
        chat_id: str = "system",
        limit: int = 14,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        api_key = self._api_key()
        if not api_key:
            return []
        if not await self._check_health():
            return []

        data = await self._request("trending/movie/week", timeout=timeout)

        if not data or not data.get("results"):
            self._report_success()
            return []

        results = [_normalize_search_result(r) for r in data["results"][:limit]]

        self._report_success()
        self._log_usage("get_trending", chat_id=chat_id)
        return results

    async def get_similar(
        self,
        movie_id: str,
        chat_id: str = "system",
        limit: int = 14,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        api_key = self._api_key()
        if not api_key or not movie_id:
            return []
        if not await self._check_health():
            return []

        data = await self._request(f"movie/{movie_id}/similar", timeout=timeout)

        if not data or not data.get("results"):
            self._report_success()
            return []

        results = [_normalize_search_result(r) for r in data["results"][:limit]]

        self._report_success()
        self._log_usage("get_similar", chat_id=chat_id)
        return results

    async def get_credits(
        self,
        movie_id: str,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        api_key = self._api_key()
        if not api_key or not movie_id:
            return {"cast": [], "director": None, "tmdb_id": movie_id}
        if not await self._check_health():
            return {"cast": [], "director": None, "tmdb_id": movie_id}

        data = await self._request(f"movie/{movie_id}/credits", timeout=timeout)
        if not data:
            return {"cast": [], "director": None, "tmdb_id": movie_id}

        cast = [c.get("name", "") for c in (data.get("cast", []) or [])[:5] if c.get("name")]
        director = None
        for c in data.get("crew", []) or []:
            if c.get("job") == "Director":
                director = c.get("name")
                break

        self._report_success()
        self._log_usage("get_credits", chat_id=chat_id)
        return {"cast": cast, "director": director, "tmdb_id": movie_id}

    async def get_trailers(
        self,
        movie_id: str,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> Optional[str]:
        api_key = self._api_key()
        if not api_key or not movie_id:
            return None
        if not await self._check_health():
            return None

        data = await self._request(f"movie/{movie_id}/videos", timeout=timeout)
        if not data or not data.get("results"):
            return None

        for v in data["results"]:
            if v.get("type") == "Trailer" and v.get("site") == "YouTube":
                self._report_success()
                self._log_usage("get_trailers", chat_id=chat_id)
                return f"https://www.youtube.com/watch?v={v['key']}"

        self._report_success()
        return None

    async def search_person(
        self,
        name: str,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        api_key = self._api_key()
        if not api_key or not name or not name.strip():
            return None
        if not await self._check_health():
            return None

        data = await self._request("search/person", params={"query": name.strip()}, timeout=timeout)
        if not data or not data.get("results"):
            return None

        self._report_success()
        return data["results"][0]

    async def get_person_movies(
        self,
        person_id: int,
        chat_id: str = "system",
        limit: int = 14,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        api_key = self._api_key()
        if not api_key or not person_id:
            return []
        if not await self._check_health():
            return []

        data = await self._request(f"person/{person_id}/movie_credits", timeout=timeout)
        if not data or not data.get("cast"):
            return []

        cast = data.get("cast", []) or []
        cast.sort(key=lambda x: x.get("popularity", 0) or 0, reverse=True)

        results = [_normalize_search_result(r) for r in cast[:limit]]

        self._report_success()
        self._log_usage("get_person_movies", chat_id=chat_id)
        return results


def _normalize_search_result(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "tmdb_id": str(r.get("id", "")),
        "title": r.get("title") or r.get("original_title", ""),
        "year": (r.get("release_date") or "")[:4] or None,
        "rating": float(r["vote_average"]) if r.get("vote_average") else None,
        "vote_count": r.get("vote_count", 0),
        "description": r.get("overview") or None,
        "poster_url": poster_url(r.get("poster_path")),
        "backdrop_url": backdrop_url(r.get("backdrop_path")),
        "genre_ids": r.get("genre_ids", []),
        "popularity": r.get("popularity"),
        "source": "tmdb",
    }
