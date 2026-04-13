"""ApiUsageRepository — BUG #2 FIX.

The api_usage table existed in the schema but had NO write path anywhere in the
codebase. This module adds a lightweight, non-blocking write path that is called
by LoggingService.log_api_usage() after every successful external provider call.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from utils.time_utils import utc_now_iso
import config.supabase_client as sb

logger = logging.getLogger("api_usage_repo")

TABLE = "api_usage"

# Thread-safe in-memory fallback for when Supabase is not configured
_lock = threading.Lock()
_store: List[Dict[str, Any]] = []


class ApiUsageRepository:
    """Write and read from the api_usage table."""

    def log(
        self,
        provider: str,
        action: str,
        chat_id: str = "system",
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> None:
        """Insert a single api_usage row. Non-blocking; failures are logged, not raised."""
        row: Dict[str, Any] = {
            "chat_id": str(chat_id),
            "provider": provider,
            "action": action,
            "timestamp": utc_now_iso(),
        }
        if prompt_tokens is not None:
            row["prompt_tokens"] = prompt_tokens
        if completion_tokens is not None:
            row["completion_tokens"] = completion_tokens
        if total_tokens is not None:
            row["total_tokens"] = total_tokens

        if sb.is_configured():
            try:
                sb.insert_rows(TABLE, [row])
            except Exception as exc:
                logger.warning("[ApiUsageRepo] insert failed: %s", exc)
        else:
            with _lock:
                _store.append(row)

    def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        if sb.is_configured():
            try:
                res, err = sb.select_rows(
                    TABLE,
                    order_by="timestamp",
                    order_desc=True,
                    limit=limit,
                )
                return res or []
            except Exception as exc:
                logger.warning("[ApiUsageRepo] get_recent failed: %s", exc)
                return []
        with _lock:
            return list(reversed(_store[-limit:]))


# Module-level singleton — import and call api_usage_repo.log(...) from clients
api_usage_repo = ApiUsageRepository()
