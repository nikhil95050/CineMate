"""Thin async wrapper around the OMDb (Open Movie Database) API.

Integrates with HealthService for circuit-breaker protection:
  - Checks is_healthy() before making a call.
  - Calls report_failure() on HTTP errors / exceptions.
  - Calls report_success() + increment_daily_calls() on success.

BUG FIX: The previous version of this file was an accidental copy of
perplexity_client.py.  It contained the wrong URL, wrong function
signatures, and no get_by_title() function — which caused all OMDb
enrichment in discovery_service.py to fail silently.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

import httpx

from services.logging_service import LoggingService, get_logger, error_batcher
from utils.time_utils import utc_now_iso

logger = get_logger("omdb")

OMDB_URL      = "https://www.omdbapi.com/"
PROVIDER_NAME = "omdb"


def _api_key() -> str:
    """Read lazily so load_dotenv() always runs first."""
    return os.environ.get("OMDB_API_KEY", "").strip()


def _health():
    """Return the HealthService singleton, or None if DI not yet ready."""
    try:
        from services.container import health_service
        return health_service
    except Exception:
        return None


async def get_by_title(
    title: str,
    year: Optional[str] = None,
    chat_id: str = "system",
    timeout: float = 10.0,
) -> Optional[Dict[str, Any]]:
    """Query OMDb by title (and optional year) and return the full response dict.

    Returns None when the API key is missing, the circuit is open, the movie
    is not found, or any network/parse error occurs.
    """
    api_key = _api_key()
    if not api_key:
        logger.warning("OMDB_API_KEY not set — skipping OMDb call")
        return None

    if not title or not title.strip():
        return None

    # ── Circuit-breaker / feature-flag guard ─────────────────────────────
    hs = _health()
    if hs is not None:
        is_healthy = await asyncio.to_thread(hs.is_healthy, PROVIDER_NAME)
        if not is_healthy:
            logger.warning("[omdb_client] circuit OPEN – call skipped")
            return None

    params: Dict[str, str] = {
        "apikey": api_key,
        "t": title.strip(),
        "plot": "short",
    }
    if year:
        # OMDb accepts a 4-digit year string
        clean_year = str(year).strip()[:4]
        if clean_year.isdigit():
            params["y"] = clean_year

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(OMDB_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        # OMDb returns {"Response": "False", "Error": "Movie not found!"}
        # on a miss — treat as a successful API call but return None.
        if data.get("Response") == "False":
            logger.debug(
                "OMDb miss for %r (year=%s): %s",
                title, year, data.get("Error", "unknown"),
            )
            # Still a successful API call — count it and keep circuit healthy
            if hs is not None:
                asyncio.create_task(asyncio.to_thread(hs.report_success, PROVIDER_NAME))
                asyncio.create_task(asyncio.to_thread(hs.increment_daily_calls, PROVIDER_NAME))
            LoggingService.log_api_usage(
                provider=PROVIDER_NAME,
                action="get_by_title:miss",
                chat_id=chat_id,
            )
            return None

        # ── Success path ─────────────────────────────────────────────────
        if hs is not None:
            asyncio.create_task(asyncio.to_thread(hs.report_success, PROVIDER_NAME))
            asyncio.create_task(asyncio.to_thread(hs.increment_daily_calls, PROVIDER_NAME))
        LoggingService.log_api_usage(
            provider=PROVIDER_NAME,
            action="get_by_title:hit",
            chat_id=chat_id,
        )
        return data

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        body = exc.response.text[:500]
        logger.error(
            "OMDb HTTP %s error: %s", status_code, body,
            extra={"status_code": status_code, "body": body},
        )
        error_batcher.emit({
            "chat_id": str(chat_id),
            "error_type": f"omdb_http_{status_code}",
            "error_message": f"HTTP {status_code}: {body}",
            "workflow_step": "omdb_client.get_by_title",
            "intent": "enrichment",
            "request_id": "N/A",
            "raw_payload": "{}",
            "timestamp": utc_now_iso(),
        })
        if hs is not None:
            asyncio.create_task(asyncio.to_thread(hs.report_failure, PROVIDER_NAME))
        return None

    except Exception as exc:
        logger.error("OMDb request failed for %r: %s", title, exc)
        error_batcher.emit({
            "chat_id": str(chat_id),
            "error_type": "omdb_request_failed",
            "error_message": str(exc),
            "workflow_step": "omdb_client.get_by_title",
            "intent": "enrichment",
            "request_id": "N/A",
            "raw_payload": "{}",
            "timestamp": utc_now_iso(),
        })
        if hs is not None:
            asyncio.create_task(asyncio.to_thread(hs.report_failure, PROVIDER_NAME))
        return None
