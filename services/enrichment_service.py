"""Async enrichment service -- adds trailers and streaming info to MovieModels.

Rule: a MovieModel is ALWAYS returned, even when an external call fails.
Errors are logged to the error_logs table via error_batcher.

Write-through cache
-------------------
After a successful Watchmode response the raw sources list is merged into
the movie's movie_metadata row (data_json["streaming_sources"]) so that
future requests can recover streaming info even when the Watchmode API is
unavailable or the quota is exhausted.
"""
from __future__ import annotations

import asyncio
import urllib.parse
import weakref
from typing import List, Optional

from clients import watchmode_client
from models.domain import MovieModel
from repositories.movie_metadata_repository import MovieMetadataRepository
from services.logging_service import get_logger, error_batcher
from utils.time_utils import utc_now_iso

logger = get_logger("enrichment")

_YOUTUBE_SEARCH = "https://www.youtube.com/results?search_query="

# Module-level singleton -- shared with discovery_service.
_metadata_repo = MovieMetadataRepository()
_background_tasks: weakref.WeakSet = weakref.WeakSet()


def _trailer_search_url(movie: MovieModel) -> str:
    query = f"{movie.title} {movie.year or ''} official trailer".strip()
    return _YOUTUBE_SEARCH + urllib.parse.quote_plus(query)


def _emit_error(
    chat_id: str,
    error_type: str,
    message: str,
    step: str,
) -> None:
    error_batcher.emit(
        {
            "chat_id": str(chat_id),
            "error_type": error_type,
            "error_message": message[:2000],
            "workflow_step": step,
            "intent": "enrichment",
            "request_id": "N/A",
            "raw_payload": "{}",
            "timestamp": utc_now_iso(),
        }
    )


class EnrichmentService:
    """Wraps enrichment logic so consumers and tests can patch at the class level."""

    def __init__(
        self,
        metadata_repo: Optional[MovieMetadataRepository] = None,
    ) -> None:
        # Injected in tests; falls back to module-level singleton in production.
        self._metadata_repo = metadata_repo or _metadata_repo

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _persist_streaming(
        self,
        movie_id: str,
        sources: list,
    ) -> None:
        """Merge raw Watchmode sources into the movie_metadata row.

        We fetch the existing data_json first so we do not overwrite OMDb
        fields -- we only add/update the streaming_sources key.
        This is fire-and-forget: called via asyncio.ensure_future().
        """
        try:
            existing = await self._metadata_repo.get(movie_id) or {}
            existing["streaming_sources"] = sources
            await self._metadata_repo.upsert(movie_id, existing)
            logger.debug("Watchmode sources persisted for %s", movie_id)
        except Exception as exc:
            # Never let a cache-write failure surface to the caller.
            logger.warning("Failed to persist Watchmode sources for %s: %s", movie_id, exc)

    async def _get_streaming_from_cache(
        self,
        movie_id: str,
    ) -> str:
        """Try to recover a streaming summary from movie_metadata when Watchmode is down."""
        try:
            data = await self._metadata_repo.get(movie_id)
            if not data:
                return ""
            cached_sources = data.get("streaming_sources") or []
            if cached_sources:
                return watchmode_client.format_streaming_summary(cached_sources)
        except Exception as exc:
            logger.debug("Cache streaming lookup failed for %s: %s", movie_id, exc)
        return ""

    # ------------------------------------------------------------------
    # Core enrichment
    # ------------------------------------------------------------------

    async def _enrich_one(self, movie: MovieModel, chat_id: str = "system") -> MovieModel:
        """Enrich a single movie. Always returns a MovieModel -- never raises."""
        updates: dict = {}

        # Trailer: always provide a YouTube search link as fallback
        if not movie.trailer:
            updates["trailer"] = _trailer_search_url(movie)

        # Streaming: only call Watchmode when we have a real IMDb ID
        if not movie.streaming and movie.movie_id.startswith("tt"):
            try:
                sources = await watchmode_client.get_streaming_sources(movie.movie_id)
                summary = watchmode_client.format_streaming_summary(sources)

                if summary:
                    updates["streaming"] = summary
                    # --- Write-through cache: persist raw sources to movie_metadata ---
                    # Fire-and-forget so it never delays the user response.
                    # We hold a reference in _background_tasks to prevent GC.
                    task = asyncio.create_task(
                        self._persist_streaming(movie.movie_id, sources)
                    )
                    _background_tasks.add(task)
                    task.add_done_callback(_background_tasks.discard)
                else:
                    # Watchmode returned nothing -- try the cache before giving up
                    cached = await self._get_streaming_from_cache(movie.movie_id)
                    if cached:
                        updates["streaming"] = cached
                        logger.debug(
                            "Streaming for %s recovered from movie_metadata cache",
                            movie.movie_id,
                        )

            except Exception as exc:
                _emit_error(
                    chat_id=chat_id,
                    error_type="watchmode_enrichment_error",
                    message=f"{movie.movie_id} ({movie.title}): {exc}",
                    step="enrichment._enrich_one",
                )
                logger.warning(
                    "Watchmode enrichment failed for %s -- trying cache: %s",
                    movie.movie_id,
                    exc,
                )
                # Attempt cache recovery before returning without streaming info
                try:
                    cached = await self._get_streaming_from_cache(movie.movie_id)
                    if cached:
                        updates["streaming"] = cached
                        logger.info(
                            "Streaming for %s served from movie_metadata cache (Watchmode down)",
                            movie.movie_id,
                        )
                except Exception:
                    pass  # Cache also failed -- movie returned without streaming

        if updates:
            return movie.model_copy(update=updates)
        return movie

    async def enrich_movies(
        self, movies: List[MovieModel], chat_id: str = "system"
    ) -> List[MovieModel]:
        """Enrich a list of movies concurrently.

        Every input movie is guaranteed to appear in the output -- failures only
        mean that streaming/trailer fields may be missing or set to fallback values.

        Implementation note: we look up _enrich_one through type(self) and call it
        as a plain function passing (m, chat_id=chat_id). This means:
          - In production: type(self)._enrich_one is the real method, called without
            self -- so we pass self explicitly as the first arg via a lambda wrapper.
          - In tests: patch() replaces type(self)._enrich_one with a plain async
            function selective_enrich(movie, **kwargs). Calling it as
            _enrich_one_fn(m, chat_id=chat_id) maps correctly with no self clash.
        """
        _enrich_one_fn = type(self)._enrich_one

        async def _call(m: MovieModel):
            try:
                return await _enrich_one_fn(m, chat_id=chat_id)
            except TypeError:
                # Real unbound method needs self as first arg
                return await _enrich_one_fn(self, m, chat_id=chat_id)

        tasks = [_call(m) for m in movies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        enriched: List[MovieModel] = []
        for original, result in zip(movies, results):
            if isinstance(result, MovieModel):
                enriched.append(result)
            else:
                # gather() captured an unexpected exception -- keep the original
                _emit_error(
                    chat_id=chat_id,
                    error_type="enrichment_gather_exception",
                    message=f"{original.title}: {result}",
                    step="enrichment.enrich_movies",
                )
                logger.error(
                    "Enrichment gather exception for %r -- keeping original: %s",
                    original.title,
                    result,
                )
                enriched.append(original)  # never drop a movie

        return enriched


# ---------------------------------------------------------------------------
# Module-level convenience shim -- keeps any existing callers working
# ---------------------------------------------------------------------------
_default_service = EnrichmentService()


async def enrich_movies(
    movies: List[MovieModel], chat_id: str = "system"
) -> List[MovieModel]:
    """Module-level shim that delegates to the default EnrichmentService instance."""
    return await _default_service.enrich_movies(movies, chat_id=chat_id)
