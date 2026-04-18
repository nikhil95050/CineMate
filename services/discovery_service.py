"""DiscoveryService: turns intents into LLM prompts, parses responses, fetches metadata.

Fallback chain:
  1. Perplexity LLM -> parse titles -> OMDb enrichment
     * Every successful OMDb response is UPSERTED into movie_metadata (write-through cache)
  2. If Perplexity fails / returns empty -> query movie_metadata table directly
  3. If OMDb fails per-movie -> keep LLM stub (title + year + reason)

All failures are logged to the error_logs table via error_batcher.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from clients import omdb_client, perplexity_client
import config as _config

# Expose supabase_client as a module-level name so that unit tests can patch
# it via patch("services.discovery_service.supabase_client").
supabase_client = _config.supabase_client

from models.domain import MovieModel, SessionModel, UserModel
from repositories.movie_metadata_repository import MovieMetadataRepository
from services.container import movie_metadata_repo
from services.logging_service import get_logger, error_batcher
from utils.time_utils import utc_now_iso

logger = get_logger("discovery")

_LLM_CANDIDATE_COUNT = 14

_metadata_repo = movie_metadata_repo


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------

def _build_system_prompt() -> str:
    return (
        "You are a world-class movie curator. "
        "Respond ONLY with a valid JSON array of movie objects. "
        "Each object must have exactly these keys: "
        '"title" (string), "year" (string), "reason" (string <= 25 words). '
        "No markdown, no commentary, no trailing commas."
    )


def _build_question_engine_prompt(session: SessionModel) -> str:
    parts = [
        f"Mood: {session.answers_mood or 'any'}",
        f"Genres: {session.answers_genre or 'any'}",
        f"Language: {session.answers_language or 'any'}",
        f"Era: {session.answers_era or 'any'}",
        f"Context: {session.answers_context or 'any'}",
        f"Duration: {session.answers_time or 'any'}",
        f"Avoid: {session.answers_avoid or 'none'}",
        f"Favorites: {session.answers_favorites or 'not specified'}",
        f"Min rating: {session.answers_rating or 'any'}",
    ]
    prefs = "\n".join(parts)
    return (
        f"Based on these viewer preferences:\n{prefs}\n\n"
        f"Recommend exactly {_LLM_CANDIDATE_COUNT} movies. "
        "Return only the JSON array as instructed."
    )


def _build_similarity_prompt(seed_title: str) -> str:
    return (
        f"Recommend exactly {_LLM_CANDIDATE_COUNT} movies similar to '{seed_title}'. "
        "Consider tone, themes, director style, and audience. "
        "Return only the JSON array as instructed."
    )


def _build_trending_prompt() -> str:
    """Build a trending prompt anchored to a concrete, verifiable year range.

    Fix #18: the previous prompt used the vague phrase 'last 12 months'.
    LLMs have a training cutoff and cannot know what was *actually* released
    in a rolling recent window — they confidently hallucinate titles or
    return films from their training window regardless of the bot's run date.

    The fix injects the *current* calendar year and the previous year at
    call-time so the LLM is asked about a concrete range it was trained on,
    producing real, verifiable titles instead of hallucinated ones.
    """
    current_year = datetime.now(tz=timezone.utc).year
    prev_year = current_year - 1
    return (
        f"List exactly {_LLM_CANDIDATE_COUNT} highly acclaimed or widely watched films "
        f"released in {current_year} or {prev_year}. "
        "Mix genres. Prioritise films with strong critical or audience reception. "
        "Return only the JSON array as instructed."
    )


def _build_surprise_prompt(user: Optional[UserModel] = None) -> str:
    avoid_genres = ""
    if user and user.disliked_genres:
        avoid_genres = f" Avoid these genres: {', '.join(user.disliked_genres)}."
    return (
        f"Pick exactly {_LLM_CANDIDATE_COUNT} surprising, underrated, or hidden-gem movies.{avoid_genres} "
        "Be bold and eclectic. Return only the JSON array as instructed."
    )


def _build_more_like_prompt(seed_title: str, seen_titles: List[str]) -> str:
    exclusions = ", ".join(seen_titles[:20]) if seen_titles else "none"
    return (
        f"Recommend exactly {_LLM_CANDIDATE_COUNT} more movies similar to '{seed_title}'. "
        f"Do NOT include any of: {exclusions}. "
        "Return only the JSON array as instructed."
    )


def _build_star_prompt(star_name: str) -> str:
    """Prompt for /star <name> — notable filmography of an actor or director."""
    return (
        f"List exactly {_LLM_CANDIDATE_COUNT} notable movies featuring or directed by "
        f"'{star_name}'. Focus on their best-known, critically acclaimed, or fan-favourite "
        "works. Include the year each movie was released. "
        "Return only the JSON array as instructed."
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _extract_json_array(raw: str) -> List[Dict[str, Any]]:
    """Robustly extract the first JSON array from a string that may contain prose."""
    try:
        parsed = json.loads(raw.strip())
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end <= start:
        logger.warning("No JSON array found in LLM response")
        return []
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        cleaned = re.sub(r",\s*([\]}])", r"\1", raw[start : end + 1])
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error("Failed to parse LLM JSON after cleanup: %s", exc)
            return []


def _llm_item_to_movie(item: Dict[str, Any]) -> Optional[MovieModel]:
    """Convert one LLM-returned dict into a MovieModel stub."""
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    year = str(item.get("year") or "").strip() or None
    reason = str(item.get("reason") or "").strip() or None
    movie_id = re.sub(r"[^a-z0-9]", "_", title.lower())[:40]
    return MovieModel(movie_id=movie_id, title=title, year=year, reason=reason)


# ---------------------------------------------------------------------------
# DB fallback
# ---------------------------------------------------------------------------

async def _fetch_from_metadata_db(
    mode: str,
    session: Optional[SessionModel],
    user: Optional[UserModel],
    seed_title: str,
    chat_id: str,
    repo: Optional[MovieMetadataRepository] = None,
) -> List[MovieModel]:
    # Only derive genre/language for the structured repo.search() path.
    genre: Optional[str] = None
    language: Optional[str] = None
    if repo is not None:
        if session:
            genre = session.answers_genre or None
            language = session.answers_language or None
        if user and not genre and user.preferred_genres:
            genre = user.preferred_genres[0] if user.preferred_genres else None

    try:
        if repo is None:
            rows_raw, err = await supabase_client.select_rows_async(
                "movie_metadata",
                limit=_LLM_CANDIDATE_COUNT,
            )
            if err or not rows_raw:
                if err:
                    logger.warning("movie_metadata fallback supabase error: %s", err)
                else:
                    logger.warning(
                        "movie_metadata fallback returned nothing for mode=%s", mode
                    )
                return []
            rows = rows_raw
        else:
            rows = await repo.search(
                limit=_LLM_CANDIDATE_COUNT,
                genre=genre,
                language=language,
            )
            if not rows:
                logger.warning(
                    "movie_metadata fallback returned nothing for mode=%s", mode
                )
                return []

        movies: List[MovieModel] = []
        for row in rows:
            data = row.get("data_json") or {}
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    continue
            movie_id = row.get("movie_id") or data.get("imdbID") or ""
            title = data.get("Title") or data.get("title") or ""
            if not title:
                continue
            rating_raw = data.get("imdbRating") or data.get("rating", "")
            try:
                rating = float(rating_raw) if rating_raw and rating_raw != "N/A" else None
            except (ValueError, TypeError):
                rating = None
            movies.append(
                MovieModel(
                    movie_id=movie_id,
                    title=title,
                    year=str(data.get("Year") or data.get("year") or "") or None,
                    rating=rating,
                    genres=data.get("Genre") or data.get("genres") or None,
                    language=data.get("Language") or data.get("language") or "English",
                    description=data.get("Plot") or data.get("description") or None,
                    poster=(
                        data.get("Poster") if data.get("Poster") not in (None, "N/A") else None
                    ) or data.get("poster") or None,
                    reason="Curated from our local library",
                )
            )
        logger.info(
            "movie_metadata fallback returned %d movies for mode=%s", len(movies), mode
        )
        return movies
    except Exception as exc:
        _emit_error(
            chat_id=chat_id,
            error_type="metadata_db_fallback_error",
            message=str(exc),
            step="discovery._fetch_from_metadata_db",
            intent=mode,
        )
        logger.error("movie_metadata fallback raised: %s", exc)
        return []


# ---------------------------------------------------------------------------
# OMDb enrichment
# ---------------------------------------------------------------------------

async def _enrich_with_omdb(
    movie: MovieModel,
    chat_id: str = "system",
    repo: Optional[MovieMetadataRepository] = None,
) -> MovieModel:
    _repo = repo or _metadata_repo
    try:
        data = await omdb_client.get_by_title(movie.title, movie.year, chat_id=chat_id)
        if not data:
            return movie

        imdb_id = data.get("imdbID") or movie.movie_id
        rating_raw = data.get("imdbRating", "")
        try:
            rating = float(rating_raw) if rating_raw and rating_raw != "N/A" else None
        except ValueError:
            rating = None

        enriched = movie.model_copy(
            update={
                "movie_id": imdb_id,
                "year": data.get("Year") or movie.year,
                "rating": rating,
                "genres": data.get("Genre") or movie.genres,
                "language": data.get("Language") or movie.language,
                "description": data.get("Plot") or movie.description,
                "poster": (
                    data.get("Poster") if data.get("Poster") != "N/A" else None
                ),
            }
        )

        task = asyncio.create_task(_repo.upsert(imdb_id, data))
        task.add_done_callback(
            lambda t: t.exception() and logger.error("Upsert failed for %s: %s", imdb_id, t.exception())
        )

        return enriched

    except Exception as exc:
        _emit_error(
            chat_id=chat_id,
            error_type="omdb_enrichment_error",
            message=f"{movie.title}: {exc}",
            step="discovery._enrich_with_omdb",
            intent="enrichment",
        )
        logger.warning("OMDb enrichment failed for %r -- keeping stub: %s", movie.title, exc)
        return movie


# ---------------------------------------------------------------------------
# Error helper
# ---------------------------------------------------------------------------

def _emit_error(
    chat_id: str,
    error_type: str,
    message: str,
    step: str,
    intent: str,
    request_id: str = "N/A",
    raw_payload: str = "{}",
) -> None:
    error_batcher.emit(
        {
            "chat_id": str(chat_id),
            "error_type": error_type,
            "error_message": message[:2000],
            "workflow_step": step,
            "intent": intent,
            "request_id": request_id,
            "raw_payload": raw_payload,
            "timestamp": utc_now_iso(),
        }
    )


# ---------------------------------------------------------------------------
# DiscoveryService
# ---------------------------------------------------------------------------

class DiscoveryService:
    """Converts intents -> Perplexity prompts -> OMDb-enriched MovieModels.

    Fallback chain:
      1. Perplexity LLM -> OMDb per movie (stub kept on OMDb failure)
         Each successful OMDb hit is upserted into movie_metadata.
      2. movie_metadata DB when Perplexity returns nothing or cannot be parsed.
    """

    def __init__(
        self,
        metadata_repo: Optional[MovieMetadataRepository] = None,
    ) -> None:
        self._metadata_repo = metadata_repo or _metadata_repo

    # ------------------------------------------------------------------
    # Star filmography
    # ------------------------------------------------------------------

    async def get_star_movies(
        self,
        star_name: str,
        chat_id: str = "system",
        request_id: str = "N/A",
    ) -> List[MovieModel]:
        """Return notable movies for *star_name* (actor or director)."""
        star_name = star_name.strip()
        if not star_name:
            return []

        prompt = _build_star_prompt(star_name)
        raw = await perplexity_client.chat(
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            chat_id=chat_id,
        )

        if not raw:
            _emit_error(
                chat_id=chat_id,
                error_type="perplexity_empty_response",
                message=f"Perplexity returned no content for star={star_name!r}",
                step="discovery.get_star_movies",
                intent="star",
                request_id=request_id,
            )
            logger.warning("Perplexity returned nothing for star=%r", star_name)
            return []

        items = _extract_json_array(raw)
        if not items:
            _emit_error(
                chat_id=chat_id,
                error_type="perplexity_parse_failed",
                message=f"Could not parse JSON for star={star_name!r}",
                step="discovery.get_star_movies",
                intent="star",
                request_id=request_id,
                raw_payload=raw[:500],
            )
            logger.warning("LLM response for star=%r could not be parsed", star_name)
            return []

        stubs: List[MovieModel] = [m for item in items if (m := _llm_item_to_movie(item))]

        enriched_results = await asyncio.gather(
            *[
                _enrich_with_omdb(m, chat_id=chat_id, repo=self._metadata_repo)
                for m in stubs
            ],
            return_exceptions=True,
        )

        result: List[MovieModel] = []
        for item in enriched_results:
            if isinstance(item, MovieModel):
                result.append(item)
            elif isinstance(item, Exception):
                logger.warning("OMDb enrich exception for star=%r: %s", star_name, item)

        return result

    # ------------------------------------------------------------------
    # General discovery
    # ------------------------------------------------------------------

    async def discover(
        self,
        mode: str,
        session: Optional[SessionModel] = None,
        user: Optional[UserModel] = None,
        seed_title: str = "",
        seen_titles: Optional[List[str]] = None,
        chat_id: str = "system",
        request_id: str = "N/A",
    ) -> List[MovieModel]:
        prompt = self._build_prompt(mode, session, user, seed_title, seen_titles or [])

        raw = await perplexity_client.chat(
            messages=[
                {"role": "system", "content": _build_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            chat_id=chat_id,
        )

        if not raw:
            _emit_error(
                chat_id=chat_id,
                error_type="perplexity_empty_response",
                message=f"Perplexity returned no content for mode={mode}",
                step="discovery.discover",
                intent=mode,
                request_id=request_id,
            )
            logger.warning(
                "Perplexity returned no content for mode=%s -- falling back to movie_metadata",
                mode,
            )
            return await _fetch_from_metadata_db(
                mode=mode, session=session, user=user,
                seed_title=seed_title, chat_id=chat_id,
                repo=self._metadata_repo if self._metadata_repo is not _metadata_repo else None,
            )

        items = _extract_json_array(raw)
        if not items:
            _emit_error(
                chat_id=chat_id,
                error_type="perplexity_parse_failed",
                message=f"Could not parse JSON array from LLM for mode={mode}",
                step="discovery.discover",
                intent=mode,
                request_id=request_id,
                raw_payload=raw[:500],
            )
            logger.warning(
                "LLM response for mode=%s could not be parsed -- falling back to movie_metadata",
                mode,
            )
            return await _fetch_from_metadata_db(
                mode=mode, session=session, user=user,
                seed_title=seed_title, chat_id=chat_id,
                repo=self._metadata_repo if self._metadata_repo is not _metadata_repo else None,
            )

        stubs: List[MovieModel] = [m for item in items if (m := _llm_item_to_movie(item))]

        enriched_results = await asyncio.gather(
            *[
                _enrich_with_omdb(m, chat_id=chat_id, repo=self._metadata_repo)
                for m in stubs
            ],
            return_exceptions=True,
        )

        result: List[MovieModel] = []
        for item in enriched_results:
            if isinstance(item, MovieModel):
                result.append(item)
            elif isinstance(item, Exception):
                _emit_error(
                    chat_id=chat_id,
                    error_type="omdb_gather_exception",
                    message=str(item),
                    step="discovery.discover",
                    intent=mode,
                    request_id=request_id,
                )
                logger.error("Unexpected OMDb gather exception: %s", item)

        if not result:
            _emit_error(
                chat_id=chat_id,
                error_type="discovery_empty_after_omdb",
                message=f"All OMDb enrichments failed for mode={mode}; trying movie_metadata",
                step="discovery.discover",
                intent=mode,
                request_id=request_id,
            )
            return await _fetch_from_metadata_db(
                mode=mode, session=session, user=user,
                seed_title=seed_title, chat_id=chat_id,
                repo=self._metadata_repo if self._metadata_repo is not _metadata_repo else None,
            )

        return result

    def _build_prompt(
        self,
        mode: str,
        session: Optional[SessionModel],
        user: Optional[UserModel],
        seed_title: str,
        seen_titles: List[str],
    ) -> str:
        if mode == "question_engine" and session:
            return _build_question_engine_prompt(session)
        if mode == "movie":
            return _build_similarity_prompt(seed_title)
        if mode == "trending":
            return _build_trending_prompt()
        if mode == "surprise":
            return _build_surprise_prompt(user)
        if mode == "more_like":
            return _build_more_like_prompt(seed_title, seen_titles)
        return _build_trending_prompt()
