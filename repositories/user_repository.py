"""Supabase-backed user repository.

Falls back gracefully to in-memory storage when Supabase is not configured
or unavailable.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from config import supabase_client as sb

logger = logging.getLogger("user_repo")

TABLE = "users"


def _normalise_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a raw Supabase users row so that numeric fields are not
    accidentally falsy.

    ``avg_rating_preference`` is stored as a Supabase ``numeric`` column.
    Supabase returns the value 0 as Python int ``0`` (or float ``0.0``).
    Both are falsy, which breaks any caller that uses the idiom
    ``row.get('avg_rating_preference') or <default>``.

    To keep compatibility with that read pattern we normalise the value to
    a non-empty string whenever it is a real zero, mirroring how the rest
    of the codebase stores numeric fields (rating, year) as strings.
    ``UserModel.from_row`` already handles string values via ``float()``
    conversion so this is safe end-to-end.
    """
    raw = row.get("avg_rating_preference")
    if raw is not None and raw != "":
        # Convert to float first to normalise int 0 → float 0.0, then to str
        # so the value is truthy even when it represents zero.
        try:
            row["avg_rating_preference"] = str(float(raw))
        except (ValueError, TypeError):
            pass  # leave unchanged if not numeric
    return row


class UserRepository:
    """Repository for user rows keyed by chat_id.

    Implements the same interface as InMemoryUserRepo in container.py.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_user(self, chat_id: str) -> Dict[str, Any]:
        chat_id = str(chat_id)
        if sb.is_configured():
            try:
                rows, error = sb.select_rows(TABLE, filters={"chat_id": chat_id}, limit=1)
                if not error and rows:
                    normalised = _normalise_row(rows[0])
                    self._store[chat_id] = normalised
                    return normalised
            except Exception as e:
                logger.warning("[UserRepo] Supabase get_user failed: %s", e)
        return self._store.get(chat_id) or {"chat_id": chat_id}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert_user(
        self,
        chat_id: str,
        username: Optional[str] = None,
        patch: Optional[Dict[str, Any]] = None,
    ) -> None:
        chat_id = str(chat_id)
        row = self._store.get(chat_id, {"chat_id": chat_id})
        if username is not None:
            row["username"] = username
        if patch:
            row.update(patch)
        row["chat_id"] = chat_id
        self._store[chat_id] = row

        if sb.is_configured():
            try:
                sb.insert_rows(
                    TABLE,
                    [row],
                    upsert=True,
                    on_conflict="chat_id",
                )
            except Exception as e:
                logger.warning("[UserRepo] Supabase upsert_user failed: %s", e)
