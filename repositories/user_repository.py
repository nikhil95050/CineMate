"""Supabase-backed user repository.

Falls back gracefully to in-memory storage when Supabase is not configured
or unavailable.

BUG #6 FIX
----------
The ``preferred_genres``, ``disliked_genres``, and ``subscriptions`` columns
are declared as ``jsonb NOT NULL DEFAULT '[]'`` in the database schema.

The Supabase PostgREST client expects **native Python lists** for jsonb
columns — it serialises them to JSON automatically.  If these fields arrive
from the in-memory cache as JSON strings (e.g. ``'["Action"]'``) rather
than Python lists, PostgREST will insert the string as a JSON string *inside*
the jsonb field (double-serialisation), producing the corrupt value
``"\"[\\\"Action\\\"]\""``.  This breaks all downstream reads that call
``json.loads()`` on the value.

Similarly, ``user_taste_vector`` is ``jsonb`` and must be a Python dict (or
``None``), never a JSON string.

``_coerce_jsonb_fields()`` normalises all four columns to native Python types
before every Supabase upsert, regardless of how they arrived in the store.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from config import supabase_client as sb

logger = logging.getLogger("user_repo")

TABLE = "users"

# jsonb list columns — must be Python list when sent to Supabase REST.
_JSONB_LIST_COLS: tuple[str, ...] = (
    "preferred_genres",
    "disliked_genres",
    "subscriptions",
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_list(value: Any) -> List[Any]:
    """Coerce *value* to a Python list.

    Accepts: list (returned as-is), JSON string, None, or any other
    iterable.  Returns [] on failure.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, ValueError):
                pass
        return [s.strip() for s in stripped.split(",") if s.strip()]
    try:
        return list(value)
    except TypeError:
        return []


def _parse_dict(value: Any) -> Optional[Dict[str, Any]]:
    """Coerce *value* to a Python dict or None.

    Accepts: dict (returned as-is), JSON string, or None.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _coerce_jsonb_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *row* with all jsonb columns as native Python types.

    BUG #6 FIX: PostgREST serialises Python list/dict → JSON automatically.
    Passing a JSON *string* causes double-serialisation and corrupt DB data.
    """
    result = dict(row)
    for col in _JSONB_LIST_COLS:
        result[col] = _parse_list(result.get(col))
    result["user_taste_vector"] = _parse_dict(result.get("user_taste_vector"))
    return result


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
        try:
            row["avg_rating_preference"] = str(float(raw))
        except (ValueError, TypeError):
            pass
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
                # BUG #6 FIX: coerce jsonb columns to native Python types
                # before the Supabase REST upsert to prevent double-serialisation.
                db_row = _coerce_jsonb_fields(row)
                sb.insert_rows(
                    TABLE,
                    [db_row],
                    upsert=True,
                    on_conflict="chat_id",
                )
            except Exception as e:
                logger.warning("[UserRepo] Supabase upsert_user failed: %s", e)
