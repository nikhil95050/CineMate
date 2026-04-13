"""Supabase-backed session repository.

Falls back gracefully to in-memory storage when Supabase is not configured
or unavailable, so local development works without a DB.

BUG #5 FIX
----------
The ``last_recs_json`` and ``overflow_buffer_json`` columns are declared
as ``text NOT NULL DEFAULT '[]'``.  They store JSON strings.  If a caller
puts a raw Python list into the session dict, ``str()`` serialisation
(Python repr) would produce ``"[{'id': 'tt123'}]"`` — invalid JSON that
crashes on ``json.loads()`` at next read.

This module now guarantees:
  - **Write**: ``_ensure_json_str()`` converts any list/dict to a valid
    JSON string before the Supabase upsert.
  - **Read**: ``_load_json_list()`` converts the stored string back to a
    Python list so callers receive the expected type.

Both helpers are idempotent and safe to call multiple times.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from config import supabase_client as sb

logger = logging.getLogger("session_repo")

TABLE = "sessions"

# Columns that must be stored as valid JSON strings in the DB.
_JSON_TEXT_COLS = ("last_recs_json", "overflow_buffer_json")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_json_str(value: Any) -> str:
    """Return *value* as a valid JSON string.

    - If *value* is already a ``str``, validate it with ``json.loads``;
      return as-is when valid, fall back to ``'[]'`` when invalid.
    - If *value* is a ``list`` or ``dict``, serialise with ``json.dumps``.
    - Any other type (``None``, etc.) falls back to ``'[]'``.
    """
    if isinstance(value, str):
        try:
            json.loads(value)  # validate only
            return value
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "[SessionRepo] invalid JSON string for JSON text column, "
                "resetting to '[]': %r", value[:120]
            )
            return "[]"
    if isinstance(value, (list, dict)):
        try:
            return json.dumps(value)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "[SessionRepo] could not serialise value to JSON, "
                "resetting to '[]': %s", exc
            )
            return "[]"
    return "[]"


def _load_json_list(value: Any) -> List[Any]:
    """Deserialise a JSON text column value back to a Python list.

    - If *value* is already a ``list``, return as-is.
    - If *value* is a ``str``, parse with ``json.loads``.
    - Returns ``[]`` on any error or when *value* is ``None``.
    """
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _prepare_for_db(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *row* with JSON text columns properly serialised.

    Ensures ``last_recs_json`` and ``overflow_buffer_json`` are always
    written as valid JSON strings, never as Python repr or None.
    """
    result = dict(row)
    for col in _JSON_TEXT_COLS:
        if col in result:
            result[col] = _ensure_json_str(result[col])
        else:
            result[col] = "[]"
    return result


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
                    row = rows[0]
                    # BUG #5 FIX: ensure JSON text columns are proper lists
                    # when surfacing to callers (keeps in-memory cache consistent).
                    for col in _JSON_TEXT_COLS:
                        row[col] = _ensure_json_str(row.get(col, "[]"))
                    self._store[chat_id] = row  # update local cache
                    return row
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
                # BUG #5 FIX: serialise JSON text columns before DB write
                db_row = _prepare_for_db(current)
                sb.insert_rows(
                    TABLE,
                    [db_row],
                    upsert=True,
                    on_conflict="chat_id",
                )
            except Exception as e:
                logger.warning("[SessionRepo] Supabase upsert_session failed: %s", e)
