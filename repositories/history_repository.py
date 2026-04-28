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

_NOT_NULL_TEXT_COLS = frozenset({"year", "genres", "language", "rating", "title"})

# C-3 FIX: whitelist of columns that exist in the history Supabase table.
# Any extra keys (description, poster, trailer, streaming_info, reason, etc.)
# would cause PostgREST to reject the upsert with an "unknown column" error.
# Mirrors the Fix #11 pattern used in watchlist_repository.
_HISTORY_COLUMNS = frozenset(
    {"chat_id", "movie_id", "title", "year", "genres", "language",
     "rating", "recommended_at", "watched", "watched_at"}
)


def _sanitise_for_db(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *row* containing only the history schema columns."""
    return {k: v for k, v in row.items() if k in _HISTORY_COLUMNS}


def _cache_key(chat_id: str, page: int) -> str:
    return f"history:{chat_id}:p{page}"


def _coerce_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *row* with all NOT NULL text columns coerced to ''."""
    coerced = dict(row)
    for col in _NOT_NULL_TEXT_COLS:
        if col in coerced and coerced[col] is None:
            coerced[col] = ""
    return coerced


class HistoryRepository:
    """CRUD + pagination for the history table."""

    def __init__(self) -> None:
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
            row = _coerce_row(row)
            enriched.append(row)

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
                # C-3 FIX: strip to schema columns before DB write
                db_rows = [_sanitise_for_db(r) for r in enriched]
                sb.insert_rows(
                    TABLE,
                    db_rows,
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
        return True

    def clear_history(self, chat_id: str) -> None:
        """ISSUE 5 FIX: delete all history rows for a user.

        Removes the in-memory store entry, deletes from Supabase, and
        invalidates all Redis cache pages for this user.
        """
        chat_id = str(chat_id)
        self._store.pop(chat_id, None)
        if sb.is_configured():
            try:
                sb.delete_rows(TABLE, filters={"chat_id": chat_id})
                delete_prefix(f"history:{chat_id}:")
            except Exception as exc:
                logger.warning("[HistoryRepo] clear_history failed: %s", exc)
                raise

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_history(
        self, chat_id: str, page: int = 1
    ) -> List[Dict[str, Any]]:
        """Return PAGE_SIZE rows for the given 1-indexed page, newest first."""
        chat_id = str(chat_id)
        page = max(1, page)

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

        all_rows = self._store.get(chat_id, [])
        offset = (page - 1) * PAGE_SIZE
        return all_rows[offset : offset + PAGE_SIZE]

    def get_total_count(self, chat_id: str) -> int:
        """Return total number of history rows for this user."""
        chat_id = str(chat_id)
        if sb.is_configured():
            try:
                # H-2 FIX: use efficient PostgREST count instead of fetching all rows
                return sb.count_rows(TABLE, filters={"chat_id": chat_id})
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
