"""Supabase-backed feedback repository.

Table: feedback
Schema:
    chat_id       text  (PK part)
    movie_id      text  (PK part)
    reaction_type text  CHECK reaction_type IN ('like','dislike')
    timestamp     timestamptz  DEFAULT now()

On duplicate (chat_id, movie_id) the row is upserted so a user can flip
their reaction from like → dislike or vice-versa.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal

import config.supabase_client as sb
from utils.time_utils import utc_now_iso

logger = logging.getLogger("feedback_repo")

TABLE = "feedback"
ReactionType = Literal["like", "dislike"]


class FeedbackRepository:
    """CRUD for the feedback table."""

    def __init__(self) -> None:
        # in-memory fallback: chat_id -> {movie_id -> row}
        self._store: Dict[str, Dict[str, Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_reaction(
        self,
        chat_id: str,
        movie_id: str,
        reaction_type: ReactionType,
    ) -> None:
        """Upsert a like or dislike reaction for (chat_id, movie_id)."""
        chat_id = str(chat_id)
        movie_id = str(movie_id)
        if reaction_type not in ("like", "dislike"):
            logger.warning(
                "[FeedbackRepo] invalid reaction_type %r for movie %s",
                reaction_type,
                movie_id,
            )
            return

        row: Dict[str, Any] = {
            "chat_id": chat_id,
            "movie_id": movie_id,
            "reaction_type": reaction_type,
            "timestamp": utc_now_iso(),
        }

        # Update in-memory store
        user_store = self._store.setdefault(chat_id, {})
        user_store[movie_id] = row

        if sb.is_configured():
            try:
                sb.insert_rows(
                    TABLE,
                    [row],
                    upsert=True,
                    on_conflict="chat_id,movie_id",
                )
            except Exception as exc:
                logger.warning(
                    "[FeedbackRepo] log_reaction failed for %s/%s: %s",
                    chat_id,
                    movie_id,
                    exc,
                )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_likes(self, chat_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Return up to *limit* 'like' rows for this user, newest first."""
        chat_id = str(chat_id)
        if sb.is_configured():
            try:
                rows, error = sb.select_rows(
                    TABLE,
                    filters={"chat_id": chat_id, "reaction_type": "like"},
                    order="timestamp.desc",
                    limit=limit,
                )
                if not error and rows is not None:
                    return rows
            except Exception as exc:
                logger.warning("[FeedbackRepo] get_likes failed: %s", exc)

        # In-memory fallback
        return [
            row
            for row in self._store.get(chat_id, {}).values()
            if row.get("reaction_type") == "like"
        ][:limit]

    def get_dislikes(self, chat_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Return up to *limit* 'dislike' rows for this user, newest first."""
        chat_id = str(chat_id)
        if sb.is_configured():
            try:
                rows, error = sb.select_rows(
                    TABLE,
                    filters={"chat_id": chat_id, "reaction_type": "dislike"},
                    order="timestamp.desc",
                    limit=limit,
                )
                if not error and rows is not None:
                    return rows
            except Exception as exc:
                logger.warning("[FeedbackRepo] get_dislikes failed: %s", exc)

        return [
            row
            for row in self._store.get(chat_id, {}).values()
            if row.get("reaction_type") == "dislike"
        ][:limit]

    def get_reaction(
        self, chat_id: str, movie_id: str
    ) -> str | None:
        """Return the current reaction ('like' | 'dislike' | None) for a movie."""
        chat_id = str(chat_id)
        movie_id = str(movie_id)
        if sb.is_configured():
            try:
                rows, error = sb.select_rows(
                    TABLE,
                    filters={"chat_id": chat_id, "movie_id": movie_id},
                    limit=1,
                )
                if not error and rows:
                    return rows[0].get("reaction_type")
            except Exception as exc:
                logger.warning("[FeedbackRepo] get_reaction failed: %s", exc)

        row = self._store.get(chat_id, {}).get(movie_id)
        return row.get("reaction_type") if row else None
