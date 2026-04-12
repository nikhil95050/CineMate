"""Supabase-backed session repository.

Falls back gracefully to in-memory storage when Supabase is not configured
or unavailable, so local development works without a DB.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from config import supabase_client as sb

logger = logging.getLogger("session_repo")

TABLE = "sessions"


class SessionRepository:
    """Repository for session rows keyed by chat_id.

    Implements the same interface as InMemorySessionRepo in container.py so it
    can be dropped in as a replacement without changing any service code.
    """

    def __init__(self) -> None:
        # In-memory fallback used when Supabase is unavailable
        self._store: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_session(self, chat_id: str) -> Dict[str, Any]:
        chat_id = str(chat_id)
        if sb.is_configured():
            try:
                rows, error = sb.select_rows(TABLE, filters={"chat_id": chat_id}, limit=1)
                if not error and rows:
                    self._store[chat_id] = rows[0]  # update local cache
                    return rows[0]
            except Exception as e:
                logger.warning("[SessionRepo] Supabase get_session failed: %s", e)
        # Fallback to in-memory
        return self._store.get(chat_id) or {"chat_id": chat_id}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert_session(self, chat_id: str, row: Dict[str, Any]) -> None:
        chat_id = str(chat_id)
        # Always update local cache first so reads are consistent within process
        current = self._store.get(chat_id, {"chat_id": chat_id})
        current.update(row)
        current["chat_id"] = chat_id
        self._store[chat_id] = current

        if sb.is_configured():
            try:
                sb.insert_rows(
                    TABLE,
                    [current],
                    upsert=True,
                    on_conflict="chat_id",
                )
            except Exception as e:
                logger.warning("[SessionRepo] Supabase upsert_session failed: %s", e)
