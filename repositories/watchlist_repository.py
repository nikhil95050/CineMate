"""Supabase-backed watchlist repository.

Table: watchlist
Expected columns:
    chat_id   text  (PK part)
    movie_id  text  (PK part)  — conflict key together with chat_id
    title     text
    year      text  NOT NULL DEFAULT ''
    language  text  NOT NULL DEFAULT ''
    rating    text  NOT NULL DEFAULT ''
    genres    text  NOT NULL DEFAULT ''
    added_at  timestamptz
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import config.supabase_client as sb
from utils.time_utils import utc_now_iso

logger = logging.getLogger("watchlist_repo")

TABLE = "watchlist"
PAGE_SIZE = 10

# Fix #11 — whitelist of columns that exist in the watchlist Supabase table.
# Any extra keys that arrive from MovieModel.model_dump() (description, poster,
# trailer, streaming_info, reason, etc.) would cause Supabase to reject the
# upsert with an "unknown column" error.  We strip to this set before every
# DB write while keeping the full row in the in-memory store.
_WATCHLIST_COLUMNS = frozenset(
    {"chat_id", "movie_id", "title", "year", "language", "rating", "genres", "added_at"}
)

# BUG #4 FIX — columns declared NOT NULL DEFAULT '' in the DB schema.
# The DEFAULT only applies when the column is *omitted*; an explicit None
# overrides the default and causes a NOT NULL constraint violation.
# _coerce_nulls() converts None → "" for these columns before every DB write.
_NOT_NULL_TEXT_COLS = frozenset({"title", "year", "language", "rating", "genres"})


def _sanitise_for_db(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *row* containing only the watchlist schema columns."""
    return {k: v for k, v in row.items() if k in _WATCHLIST_COLUMNS}


def _coerce_nulls(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of *row* with None → '' for NOT NULL text columns.

    Must be called *after* _sanitise_for_db so only known columns remain.
    """
    coerced = dict(row)
    for col in _NOT_NULL_TEXT_COLS:
        if col in coerced and coerced[col] is None:
            coerced[col] = ""
    return coerced


class WatchlistRepository:
    """CRUD + pagination for the watchlist table."""

    def __init__(self) -> None:
        self._store: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add_to_watchlist(
        self, chat_id: str, row: Dict[str, Any]
    ) -> bool:
        """Upsert a single watchlist row. Returns True on success.

        The in-memory store keeps the full row for local queries; only the
        whitelisted schema columns are sent to Supabase (fix #11).
        None values for NOT NULL text columns are coerced to '' (fix #4).
        """
        chat_id = str(chat_id)
        full_row = dict(row)
        full_row["chat_id"] = chat_id
        full_row.setdefault("added_at", utc_now_iso())

        # In-memory store — keep full row so local get_by_movie_id works
        existing = {r["movie_id"]: r for r in self._store.get(chat_id, [])}
        existing[full_row["movie_id"]] = full_row
        self._store[chat_id] = sorted(
            existing.values(),
            key=lambda r: r.get("added_at", ""),
            reverse=True,
        )

        if sb.is_configured():
            try:
                # 1. Strip to schema columns only (fix #11)
                db_row = _sanitise_for_db(full_row)
                # 2. Coerce None → '' for NOT NULL text columns (fix #4)
                db_row = _coerce_nulls(db_row)
                sb.insert_rows(
                    TABLE,
                    [db_row],
                    upsert=True,
                    on_conflict="chat_id,movie_id",
                )
                return True
            except Exception as exc:
                logger.warning("[WatchlistRepo] add failed: %s", exc)
                return False
        return True

    def remove_from_watchlist(
        self, chat_id: str, movie_id: str
    ) -> bool:
        """Delete a watchlist entry. Returns True on success."""
        chat_id = str(chat_id)
        store = self._store.get(chat_id, [])
        self._store[chat_id] = [
            r for r in store if r.get("movie_id") != movie_id
        ]

        if sb.is_configured():
            try:
                sb.delete_rows(
                    TABLE,
                    filters={"chat_id": chat_id, "movie_id": movie_id},
                )
                return True
            except Exception as exc:
                logger.warning(
                    "[WatchlistRepo] remove failed: %s", exc
                )
                return False
        return True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_watchlist(
        self, chat_id: str, page: int = 1
    ) -> List[Dict[str, Any]]:
        """Return PAGE_SIZE watchlist rows for the given 1-indexed page."""
        chat_id = str(chat_id)
        page = max(1, page)

        if sb.is_configured():
            try:
                offset = (page - 1) * PAGE_SIZE
                rows, error = sb.select_rows(
                    TABLE,
                    filters={"chat_id": chat_id},
                    order="added_at.desc",
                    limit=PAGE_SIZE,
                    offset=offset,
                )
                if not error and rows:
                    return rows
                if not error:
                    return []
            except Exception as exc:
                logger.warning(
                    "[WatchlistRepo] get_watchlist failed: %s", exc
                )

        all_rows = self._store.get(chat_id, [])
        offset = (page - 1) * PAGE_SIZE
        return all_rows[offset : offset + PAGE_SIZE]

    def get_total_count(self, chat_id: str) -> int:
        chat_id = str(chat_id)
        if sb.is_configured():
            try:
                rows, error = sb.select_rows(
                    TABLE, filters={"chat_id": chat_id}
                )
                if not error and rows is not None:
                    return len(rows)
            except Exception as exc:
                logger.warning(
                    "[WatchlistRepo] get_total_count failed: %s", exc
                )
        return len(self._store.get(chat_id, []))

    def get_by_movie_id(
        self, chat_id: str, movie_id: str
    ) -> Optional[Dict[str, Any]]:
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
                logger.warning(
                    "[WatchlistRepo] get_by_movie_id failed: %s", exc
                )
        for row in self._store.get(chat_id, []):
            if row.get("movie_id") == movie_id:
                return row
        return None

    def is_in_watchlist(self, chat_id: str, movie_id: str) -> bool:
        return self.get_by_movie_id(chat_id, movie_id) is not None
