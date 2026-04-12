"""RecommendationService: dedup, filter, enrich, and persist movie recommendations."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

from models.domain import MovieModel, SessionModel, UserModel
from services.discovery_service import DiscoveryService
from services.enrichment_service import EnrichmentService
from services.logging_service import get_logger, error_batcher  # noqa: F401 — imported so tests can patch it here

logger = get_logger("rec_service")

BATCH_SIZE = 5


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
    """Orchestrates discovery, dedup, filtering, enrichment, and session persistence."""

    def __init__(self, discovery: Optional[DiscoveryService] = None) -> None:
        self._discovery = discovery or DiscoveryService()
        self._enrichment = EnrichmentService()

    async def get_recommendations(
        self,
        session: SessionModel = None,
        user: UserModel = None,
        mode: str = "question_engine",
        chat_id: str = "",
        seed_title: str = "",
        request_id: str = "N/A",
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """Return up to BATCH_SIZE enriched movie dicts, with overflow buffered in session."""

        last_recs = _parse_json_list(session.last_recs_json) if session else []
        excluded_ids = {str(r.get("movie_id", "")) for r in last_recs if r.get("movie_id")}
        excluded_titles = {str(r.get("title", "")).lower() for r in last_recs if r.get("title")}

        min_rating = self._resolve_min_rating(session, user) if session else None

        seen_titles = list(excluded_titles)
        # Pass chat_id and request_id so discovery can log errors with real context
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

        # Pass chat_id to enrichment so Watchmode errors carry a real chat_id
        enriched = await self._enrichment.enrich_movies(
            to_show,
            chat_id=chat_id or "system",
        )

        # Only touch session_service when a real session object is present
        if chat_id and session:
            from services.container import session_service  # deferred to avoid import cycles
            session_model = session_service.get_session(chat_id)
            session_model.last_recs_json = json.dumps([m.model_dump() for m in enriched])
            session_model.overflow_buffer_json = json.dumps([m.model_dump() for m in overflow])
            session_service.upsert_session(session_model)

        if overflow:
            asyncio.create_task(
                self._enrichment.enrich_movies(overflow, chat_id=chat_id or "system")
            )

        return [m.model_dump() for m in enriched]

    async def get_more_suggestions(
        self,
        session: SessionModel,
        user: UserModel,
        chat_id: str = "",
        request_id: str = "N/A",
    ) -> List[Dict[str, Any]]:
        """Return movies from the overflow buffer; if empty, re-discover."""
        from services.container import session_service  # deferred to avoid import cycles

        overflow = _parse_json_list(session.overflow_buffer_json)
        if overflow:
            batch = overflow[:BATCH_SIZE]
            rest = overflow[BATCH_SIZE:]
            enriched = await self._enrichment.enrich_movies(
                [MovieModel(**m) for m in batch],
                chat_id=chat_id or "system",
            )

            session_model = session_service.get_session(chat_id)
            new_last = _parse_json_list(session_model.last_recs_json) + [
                m.model_dump() for m in enriched
            ]
            session_model.last_recs_json = json.dumps(new_last[-20:])
            session_model.overflow_buffer_json = json.dumps(rest)
            session_service.upsert_session(session_model)

            return [m.model_dump() for m in enriched]

        return await self.get_recommendations(
            session, user, mode="trending", chat_id=chat_id, request_id=request_id
        )

    def _resolve_min_rating(
        self, session: SessionModel, user: Optional[UserModel]
    ) -> Optional[float]:
        raw = session.answers_rating or ""
        mapping = {"6+": 6.0, "7+": 7.0, "8+": 8.0, "9+": 9.0, "any": None}
        val = mapping.get(raw.lower())
        if val is not None:
            return val
        if user and user.avg_rating_preference:
            return user.avg_rating_preference
        return None
