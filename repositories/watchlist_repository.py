"""Supabase-backed watchlist repository.

Table: watchlist
Expected columns:
    chat_id   text  (PK part)
    movie_id  text  (PK part)  — conflict key together with chat_id
    title     text
    year      text
    language  text
    rating    text
    genres    text
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
        """Upsert a single watchlist row. Returns True on success."""
        chat_id = str(chat_id)
        full_row = dict(row)
        full_row["chat_id"] = chat_id
        full_row.setdefault("added_at", utc_now_iso())

        # In-memory store
        existing = {r["movie_id"]: r for r in self._store.get(chat_id, [])}
        existing[full_row["movie_id"]] = full_row
        self._store[chat_id] = sorted(
            existing.values(),
            key=lambda r: r.get("added_at", ""),
            reverse=True,
        )

        if sb.is_configured():
            try:
                sb.insert_rows(
                    TABLE,
                    [full_row],
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
