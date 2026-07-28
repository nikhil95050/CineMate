"""Unified movie data provider with TMDB primary + OMDB fallback.

Usage:
    provider = MovieDataProvider()
    data = await provider.get_by_title("Inception")

Returns a canonical dict shape regardless of which API served the data.
The `source` field indicates origin ("tmdb" or "omdb").
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from clients.tmdb_client import TmdbClient
from clients import omdb_client

logger = logging.getLogger("movie_data_provider")

OMDB_SOURCE_FIELDS = {
    "imdbID": "imdb_id",
    "Title": "title",
    "Year": "year",
    "imdbRating": "rating",
    "Genre": "genres",
    "Language": "language",
    "Plot": "description",
    "Poster": "poster_url",
}


def _normalize_omdb_response(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"source": "omdb"}
    for omdb_key, canonical_key in OMDB_SOURCE_FIELDS.items():
        val = raw.get(omdb_key)
        if val and val != "N/A":
            result[canonical_key] = val
    if "imdb_id" in result:
        result["movie_id"] = result["imdb_id"]
    if "rating" in result:
        try:
            result["rating"] = float(result["rating"])
        except (ValueError, TypeError):
            result.pop("rating")
    return result


def _normalize_tmdb_response(raw: dict[str, Any]) -> dict[str, Any]:
    raw["source"] = "tmdb"
    return raw


class MovieDataProvider:
    """TMDB-first movie data lookup with OMDB fallback.

    On every call:
      1. Try TMDB. If successful, return normalized TMDB data.
      2. If TMDB fails (no key, circuit open, network error, 404), try OMDB.
      3. If both fail, return None.
    """

    def __init__(
        self,
        tmdb_client: Optional[TmdbClient] = None,
    ) -> None:
        self._tmdb = tmdb_client or TmdbClient()
        self._tmdb_available = bool(self._tmdb._api_key())

    async def get_by_title(
        self,
        title: str,
        year: Optional[str] = None,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        if not title or not title.strip():
            return None

        if self._tmdb_available:
            try:
                data = await self._tmdb.get_by_title(
                    title=title, year=year, chat_id=chat_id, timeout=timeout
                )
                if data:
                    return _normalize_tmdb_response(data)
                logger.debug("TMDB returned no data for %r, falling back to OMDb", title)
            except Exception as exc:
                logger.warning("TMDB get_by_title failed for %r: %s -- falling back to OMDb", title, exc)

        omdb_data = await omdb_client.get_by_title(
            title=title, year=year, chat_id=chat_id, timeout=timeout
        )
        if omdb_data:
            return _normalize_omdb_response(omdb_data)

        return None

    async def get_by_id(
        self,
        movie_id: str,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        if not movie_id:
            return None

        if movie_id.startswith("tt"):
            if self._tmdb_available:
                try:
                    data = await self._tmdb.get_by_id(
                        movie_id=movie_id, chat_id=chat_id, timeout=timeout
                    )
                    if data:
                        return _normalize_tmdb_response(data)
                except Exception as exc:
                    logger.warning("TMDB get_by_id failed for %r: %s", movie_id, exc)

            omdb_data = await omdb_client.get_by_title(
                title=movie_id, chat_id=chat_id, timeout=timeout
            )
            if omdb_data:
                return _normalize_omdb_response(omdb_data)

        elif self._tmdb_available:
            try:
                data = await self._tmdb.get_by_id(
                    movie_id=movie_id, chat_id=chat_id, timeout=timeout
                )
                if data:
                    return _normalize_tmdb_response(data)
            except Exception as exc:
                logger.warning("TMDB get_by_id failed for %r: %s", movie_id, exc)

        return None

    async def search(
        self,
        query: str,
        chat_id: str = "system",
        limit: int = 5,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        if not query or not query.strip():
            return []

        if self._tmdb_available:
            try:
                results = await self._tmdb.search(
                    query=query, chat_id=chat_id, limit=limit, timeout=timeout
                )
                if results:
                    return [r for r in results]
            except Exception as exc:
                logger.warning("TMDB search failed for %r: %s", query, exc)

        return []

    async def get_trending(
        self,
        chat_id: str = "system",
        limit: int = 14,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        if self._tmdb_available:
            try:
                return await self._tmdb.get_trending(
                    chat_id=chat_id, limit=limit, timeout=timeout
                )
            except Exception as exc:
                logger.warning("TMDB get_trending failed: %s", exc)
        return []

    async def get_similar(
        self,
        movie_id: str,
        chat_id: str = "system",
        limit: int = 14,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        if self._tmdb_available:
            try:
                return await self._tmdb.get_similar(
                    movie_id=movie_id, chat_id=chat_id, limit=limit, timeout=timeout
                )
            except Exception as exc:
                logger.warning("TMDB get_similar failed for %r: %s", movie_id, exc)
        return []

    async def get_credits(
        self,
        movie_id: str,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        if self._tmdb_available:
            try:
                return await self._tmdb.get_credits(
                    movie_id=movie_id, chat_id=chat_id, timeout=timeout
                )
            except Exception as exc:
                logger.warning("TMDB get_credits failed for %r: %s", movie_id, exc)
        return {"cast": [], "director": None, "tmdb_id": movie_id}

    async def get_trailers(
        self,
        movie_id: str,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> Optional[str]:
        if self._tmdb_available:
            try:
                return await self._tmdb.get_trailers(
                    movie_id=movie_id, chat_id=chat_id, timeout=timeout
                )
            except Exception as exc:
                logger.warning("TMDB get_trailers failed for %r: %s", movie_id, exc)
        return None
