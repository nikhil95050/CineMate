"""Repository for the movie_metadata table.

Schema:
    movie_id    text PRIMARY KEY
    data_json   jsonb NOT NULL          -- raw OMDb API response dict
    last_updated timestamptz DEFAULT now()

Responsibilities
----------------
* upsert(movie_id, data_json)  -- called after every successful OMDb response
* get(movie_id)                -- point lookup by IMDb ID
* search(limit, genre, language) -- best-effort fallback query when all APIs fail

Test-isolation contract
-----------------------
All three public methods go through Supabase exclusively.
Unit tests patch ``supabase_client.insert_rows_async`` and
``supabase_client.select_rows_async`` directly -- there is no in-memory
``_store`` fallback on read paths so mocked return values are always used.

The ``_store`` dict is kept as a write-only mirror for offline/debug purposes
but is never consulted by get() or search(); only upsert() writes to it.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from config import supabase_client

logger = logging.getLogger("movie_metadata_repo")

_TABLE = "movie_metadata"


class MovieMetadataRepository:
    """Thin async wrapper around the movie_metadata Supabase table."""

    def __init__(self) -> None:
        # Write-only mirror used for offline/debug inspection only.
        # Never read from in get() or search() -- tests rely on this.
        self._store: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def upsert(self, movie_id: str, data_json: Dict[str, Any]) -> bool:
        """Upsert one OMDb record.  Returns True on success, False on error."""
        if not movie_id or not data_json:
            return False
        row = {
            "movie_id": movie_id,
            "data_json": data_json,
        }
        try:
            _, err = await supabase_client.insert_rows_async(
                _TABLE,
                [row],
                upsert=True,
                on_conflict="movie_id",
            )
            if err:
                logger.warning("movie_metadata upsert failed for %s: %s", movie_id, err)
                self._store[movie_id] = data_json
                return False
            self._store[movie_id] = data_json
            logger.debug("movie_metadata upserted %s", movie_id)
            return True
        except Exception as exc:
            logger.error("movie_metadata upsert raised for %s: %s", movie_id, exc)
            self._store[movie_id] = data_json
            return False

    # ------------------------------------------------------------------
    # Read — point lookup
    # ------------------------------------------------------------------

    async def get(self, movie_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single row by movie_id.  Returns data_json dict or None.

        Returns None on any error or empty result -- never falls back to
        _store so that unit-test mocks are always authoritative.
        """
        if not movie_id:
            return None
        try:
            rows, err = await supabase_client.select_rows_async(
                _TABLE,
                filters={"movie_id": movie_id},
                limit=1,
            )
            if err or not rows:
                logger.debug("movie_metadata get miss for %s: %s", movie_id, err)
                return None
            data = rows[0].get("data_json") or {}
            if isinstance(data, str):
                data = json.loads(data)
            return data
        except Exception as exc:
            logger.error("movie_metadata get raised for %s: %s", movie_id, exc)
            return None

    # ------------------------------------------------------------------
    # Read -- fallback search (most recent records)
    # ------------------------------------------------------------------

    async def search(
        self,
        limit: int = 14,
        genre: Optional[str] = None,
        language: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return up to `limit` rows ordered by last_updated DESC.

        genre / language are advisory: Supabase REST does not support
        JSON-path filtering without RPC, so we fetch `limit * 3` rows and
        apply Python-level filtering, then trim to `limit`.

        Returns [] on any error -- never falls back to _store so that
        unit-test mocks are always authoritative.
        """
        if limit == 0:
            return []

        fetch_limit = limit * 3 if (genre or language) else limit
        try:
            rows, err = await supabase_client.select_rows_async(
                _TABLE,
                limit=fetch_limit,
                order="last_updated.desc",
            )
            if err or not rows:
                logger.warning("movie_metadata search returned nothing: %s", err)
                return []
        except Exception as exc:
            logger.error("movie_metadata search raised: %s", exc)
            return []

        results: List[Dict[str, Any]] = []
        for row in rows:
            data = row.get("data_json") or {}
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except Exception:
                    continue
            # Advisory genre / language filter
            if genre and genre.lower() not in (data.get("Genre") or "").lower():
                continue
            if language and language.lower() not in (data.get("Language") or "").lower():
                continue
            row_copy = dict(row)
            row_copy["data_json"] = data
            results.append(row_copy)
            if len(results) >= limit:
                break

        return results
