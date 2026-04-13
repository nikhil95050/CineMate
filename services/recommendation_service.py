"""RecommendationService: dedup, filter, enrich, and persist movie recommendations."""
from __future__ import annotations

import asyncio
import json
import weakref
from typing import Any, Dict, List, Optional

from models.domain import MovieModel, SessionModel, UserModel
from services.discovery_service import DiscoveryService
from services.enrichment_service import EnrichmentService, enrich_movies
from services.logging_service import get_logger, error_batcher  # noqa: F401

logger = get_logger("rec_service")

BATCH_SIZE = 5

# Hold strong references so Python 3.11+ cannot GC-cancel background tasks.
_background_tasks: weakref.WeakSet = weakref.WeakSet()


def _parse_json_list(raw: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(raw or "[]")
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def _movie_passes_filters(
    movie: MovieModel,
    excluded_ids: set,
    min_rating: Optional[float],
    disliked_genres: List[str],
) -> bool:
    if movie.movie_id in excluded_ids:
        return False
    if min_rating and movie.rating and movie.rating < min_rating:
        return False
    if disliked_genres and movie.genres:
        movie_genre_set = {g.strip().lower() for g in movie.genres.split(",")}
        if any(dg.lower() in movie_genre_set for dg in disliked_genres):
            return False
    return True


class RecommendationService:
    """Orchestrates discovery, dedup, filtering, enrichment, and session + history persistence."""

    def __init__(self, discovery: Optional[DiscoveryService] = None) -> None:
        self._discovery = discovery or DiscoveryService()
        self._enrichment = EnrichmentService()

    async def get_recommendations(
        self,
        session: Optional[SessionModel] = None,
        user: Optional[UserModel] = None,
        mode: str = "question_engine",
        chat_id: str = "",
        seed_title: str = "",
        request_id: str = "N/A",
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Return up to BATCH_SIZE enriched movie dicts, persist history, buffer overflow."""

        last_recs = _parse_json_list(session.last_recs_json) if session is not None else []
        excluded_ids = {str(r.get("movie_id", "")) for r in last_recs if r.get("movie_id")}
        excluded_titles = {str(r.get("title", "")).lower() for r in last_recs if r.get("title")}

        min_rating = self._resolve_min_rating(session, user) if session is not None else None

        seen_titles = list(excluded_titles)
        candidates = await self._discovery.discover(
            mode=mode,
            session=session,
            user=user,
            seed_title=seed_title,
            seen_titles=seen_titles,
            chat_id=chat_id or "system",
            request_id=request_id,
        )

        disliked = user.disliked_genres if user else []
        filtered = [
            m for m in candidates
            if _movie_passes_filters(m, excluded_ids, min_rating, disliked)
        ]

        seen_lower: set = set()
        deduped: List[MovieModel] = []
        for m in filtered:
            key = m.title.lower()
            if key not in seen_lower:
                seen_lower.add(key)
                deduped.append(m)

        to_show = deduped[:BATCH_SIZE]
        overflow = deduped[BATCH_SIZE:]

        # Enrich BEFORE writing history so the canonical movie_id (OMDb imdbID) is used
        enriched = await self._enrichment.enrich_movies(
            to_show,
            chat_id=chat_id or "system",
        )

        # Write history rows after enrichment
        if chat_id and enriched:
            try:
                from services.container import movie_service  # deferred to avoid import cycles
                history_rows = [m.to_history_row(chat_id) for m in enriched]
                movie_service.history_repo.log_recommendations(chat_id, history_rows)
            except Exception as hist_exc:
                logger.warning("[RecService] history write failed: %s", hist_exc)

        # Fix #12: mutate the *session* passed in rather than fetching a fresh
        # one from the DB. A fresh fetch loses any sim_depth increment (or other
        # in-flight state changes) the caller applied before invoking this method.
        if chat_id and session is not None:
            from services.container import session_service  # deferred to avoid import cycles
            # Fix #16: use model_dump(mode='json') so nested Pydantic objects
            # (e.g. StreamingInfo) are serialised to plain dicts/scalars, making
            # the JSON string safe to round-trip through MovieModel(**m) later.
            session.last_recs_json = json.dumps([m.model_dump(mode="json") for m in enriched])
            session.overflow_buffer_json = json.dumps([m.model_dump(mode="json") for m in overflow])
            session_service.upsert_session(session)

        if overflow:
            task = asyncio.create_task(
                self._enrichment.enrich_movies(overflow, chat_id=chat_id or "system")
            )
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        return [m.model_dump(mode="json") for m in enriched]

    async def get_more_suggestions(
        self,
        session: Optional[SessionModel] = None,
        user: Optional[UserModel] = None,
        chat_id: str = "",
        request_id: str = "N/A",
    ) -> List[Dict[str, Any]]:
        """Return movies from the overflow buffer; if empty, re-discover."""
        from services.container import session_service  # deferred to avoid import cycles

        overflow_raw = session.overflow_buffer_json if session is not None else None
        overflow = _parse_json_list(overflow_raw) if overflow_raw else []

        if overflow:
            batch = overflow[:BATCH_SIZE]
            rest = overflow[BATCH_SIZE:]

            # Fix #16: overflow dicts were stored via model_dump(mode='json') so
            # all values are plain JSON scalars. MovieModel(**m) rehydrates safely.
            enriched = await self._enrichment.enrich_movies(
                [MovieModel(**m) for m in batch],
                chat_id=chat_id or "system",
            )

            # Write history for overflow batch
            if chat_id and enriched:
                try:
                    from services.container import movie_service
                    movie_service.history_repo.log_recommendations(
                        chat_id, [m.to_history_row(chat_id) for m in enriched]
                    )
                except Exception as hist_exc:
                    logger.warning("[RecService] overflow history write failed: %s", hist_exc)

            # Fix #12: same pattern — mutate the passed-in session, do not re-fetch.
            if chat_id and session is not None:
                new_last = _parse_json_list(session.last_recs_json) + [
                    m.model_dump(mode="json") for m in enriched
                ]
                session.last_recs_json = json.dumps(new_last[-20:])
                session.overflow_buffer_json = json.dumps(rest)
                session_service.upsert_session(session)

            return [m.model_dump(mode="json") for m in enriched]

        return await self.get_recommendations(
            session, user, mode="trending", chat_id=chat_id, request_id=request_id
        )

    def _resolve_min_rating(
        self, session: Optional[SessionModel], user: Optional[UserModel]
    ) -> Optional[float]:
        if session is None:
            return None
        raw = session.answers_rating or ""
        mapping = {"6+": 6.0, "7+": 7.0, "8+": 8.0, "9+": 9.0, "any": None}
        val = mapping.get(raw.lower())
        if val is not None:
            return val
        if user and user.avg_rating_preference:
            return user.avg_rating_preference
        return None
