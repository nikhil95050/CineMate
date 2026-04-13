"""Supabase-backed history repository with Redis slice caching.

Table: history
Expected columns:
    chat_id         text  (PK part)
    movie_id        text  (PK part)  — conflict key together with chat_id
    title           text
    year            text  NOT NULL DEFAULT ''
    genres          text  NOT NULL DEFAULT ''
    language        text  NOT NULL DEFAULT ''
    rating          text  NOT NULL DEFAULT ''
    recommended_at  timestamptz
    watched         boolean  default false
    watched_at      timestamptz nullable
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import config.supabase_client as sb
from config.redis_cache import delete_prefix, get_json, set_json
from utils.time_utils import utc_now_iso

logger = logging.getLogger("history_repo")

TABLE = "history"
PAGE_SIZE = 10
CACHE_TTL = 120  # seconds

# Columns that are NOT NULL DEFAULT '' in the DB schema.
# If the caller passes None for any of these, Supabase will attempt to
# insert null and raise a constraint violation.  _coerce_row() converts
# every None value in this set to an empty string before any DB write.
_NOT_NULL_TEXT_COLS = frozenset({"year", "genres", "language", "rating", "title"})


def _cache_key(chat_id: str, page: int) -> str:
    return f"history:{chat_id}:p{page}"


def _coerce_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *row* with all NOT NULL text columns coerced to ''.

    OMDB often returns "N/A" or omits fields entirely.  Down-stream code
    sometimes normalises those to None before building the insert payload.
    This helper ensures we never send null for a NOT NULL column.
    """
    coerced = dict(row)
    for col in _NOT_NULL_TEXT_COLS:
        if col in coerced and coerced[col] is None:
            coerced[col] = ""
    return coerced


class HistoryRepository:
    """CRUD + pagination for the history table."""

    def __init__(self) -> None:
        # In-memory fallback (chat_id → list of rows, newest-first)
        self._store: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def log_recommendations(
        self, chat_id: str, rows: List[Dict[str, Any]]
    ) -> None:
        """Bulk-upsert recommendation rows. Conflict key: (chat_id, movie_id)."""
        chat_id = str(chat_id)
        now = utc_now_iso()
        enriched: List[Dict[str, Any]] = []
        for r in rows:
            row = dict(r)
            row["chat_id"] = chat_id
            row.setdefault("recommended_at", now)
            row.setdefault("watched", False)
            row.setdefault("watched_at", None)
            # BUG #3 FIX: coerce None → "" for NOT NULL text columns so
            # Supabase never receives an explicit null for a NOT NULL field.
            row = _coerce_row(row)
            enriched.append(row)

        # Update in-memory store (keyed by movie_id, newest first)
        existing = {r["movie_id"]: r for r in self._store.get(chat_id, [])}
        for row in enriched:
            existing[row["movie_id"]] = row
        self._store[chat_id] = sorted(
            existing.values(),
            key=lambda r: r.get("recommended_at", ""),
            reverse=True,
        )

        if sb.is_configured():
            try:
                sb.insert_rows(
                    TABLE,
                    enriched,
                    upsert=True,
                    on_conflict="chat_id,movie_id",
                )
                delete_prefix(f"history:{chat_id}:")
            except Exception as exc:
                logger.warning("[HistoryRepo] bulk upsert failed: %s", exc)

    def mark_watched(self, chat_id: str, movie_id: str) -> bool:
        """Set watched=True and watched_at=now. Returns True on success."""
        chat_id = str(chat_id)
        now = utc_now_iso()

        # Update in-memory fallback
        for row in self._store.get(chat_id, []):
            if row.get("movie_id") == movie_id:
                row["watched"] = True
                row["watched_at"] = now

        if sb.is_configured():
            try:
                sb.update_rows(
                    TABLE,
                    patch={"watched": True, "watched_at": now},
                    filters={"chat_id": chat_id, "movie_id": movie_id},
                )
                delete_prefix(f"history:{chat_id}:")
                return True
            except Exception as exc:
                logger.warning("[HistoryRepo] mark_watched failed: %s", exc)
                return False
        return True  # in-memory update succeeded

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_history(
        self, chat_id: str, page: int = 1
    ) -> List[Dict[str, Any]]:
        """Return PAGE_SIZE rows for the given 1-indexed page, newest first."""
        chat_id = str(chat_id)
        page = max(1, page)

        # Try Redis / local cache
        cached = get_json(_cache_key(chat_id, page))
        if cached is not None:
            return cached

        if sb.is_configured():
            try:
                offset = (page - 1) * PAGE_SIZE
                rows, error = sb.select_rows(
                    TABLE,
                    filters={"chat_id": chat_id},
                    order="recommended_at.desc",
                    limit=PAGE_SIZE,
                    offset=offset,
                )
                if not error and rows:
                    set_json(_cache_key(chat_id, page), rows, ttl=CACHE_TTL)
                    return rows
                if not error:
                    return []
            except Exception as exc:
                logger.warning("[HistoryRepo] get_history failed: %s", exc)

        # In-memory fallback
        all_rows = self._store.get(chat_id, [])
        offset = (page - 1) * PAGE_SIZE
        return all_rows[offset : offset + PAGE_SIZE]

    def get_total_count(self, chat_id: str) -> int:
        """Return total number of history rows for this user."""
        chat_id = str(chat_id)
        if sb.is_configured():
            try:
                rows, error = sb.select_rows(
                    TABLE, filters={"chat_id": chat_id}
                )
                if not error and rows is not None:
                    return len(rows)
            except Exception as exc:
                logger.warning("[HistoryRepo] get_total_count failed: %s", exc)
        return len(self._store.get(chat_id, []))

    def get_by_movie_id(
        self, chat_id: str, movie_id: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch a single history row by movie_id."""
        chat_id = str(chat_id)
        if sb.is_configured():
            try:
                rows, error = sb.select_rows(
                    TABLE,
                    filters={"chat_id": chat_id, "movie_id": movie_id},
                    limit=1,
                )
                if not error and rows:
                    return rows[0]
            except Exception as exc:
                logger.warning("[HistoryRepo] get_by_movie_id failed: %s", exc)

        for row in self._store.get(chat_id, []):
            if row.get("movie_id") == movie_id:
                return row
        return None
