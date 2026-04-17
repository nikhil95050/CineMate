"""Thin async wrapper around the OMDb API.

Integrates with HealthService for circuit-breaker protection:
  - Checks is_healthy() before making a call.
  - Calls report_failure() on network/HTTP errors.
  - Calls report_success() + increment_daily_calls() on success.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import httpx

from services.logging_service import LoggingService, get_logger, error_batcher
from utils.time_utils import utc_now_iso

logger = get_logger("omdb")

OMDB_URL      = "https://www.omdbapi.com/"
PROVIDER_NAME = "omdb"


def _api_key() -> str:
    return os.environ.get("OMDB_API_KEY", "").strip()


def _health():
    try:
        from services.container import health_service
        return health_service
    except Exception:
        return None


async def get_by_title(
    title: str,
    year: Optional[str] = None,
    chat_id: str = "system",
) -> Optional[Dict[str, Any]]:
    """Fetch a single movie by title (and optional year) from OMDb."""
    api_key = _api_key()
    if not api_key:
        logger.warning("OMDB_API_KEY not set — skipping OMDb call")
        return None

    hs = _health()
    if hs is not None and not hs.is_healthy(PROVIDER_NAME):
        logger.warning("[omdb_client] circuit OPEN – call skipped")
        return None

    params: Dict[str, str] = {"apikey": api_key, "t": title, "type": "movie"}
    if year:
        params["y"] = str(year)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(OMDB_URL, params=params)
            data = resp.json()
            if data.get("Response") == "True":
                if hs is not None:
                    hs.report_success(PROVIDER_NAME)
                    hs.increment_daily_calls(PROVIDER_NAME)
                LoggingService.log_api_usage(
                    provider=PROVIDER_NAME,
                    action="get_by_title",
                    chat_id=chat_id,
                )
                return data
            logger.debug("OMDb no result for %r: %s", title, data.get("Error"))
            # A "False" response is a data-not-found, not a provider failure
            return None
    except Exception as exc:
        logger.error("OMDb request failed for %r: %s", title, exc)
        error_batcher.emit({
            "chat_id": "system",
            "error_type": "omdb_request_failed",
            "error_message": str(exc),
            "workflow_step": "omdb_client.get_by_title",
            "intent": "discovery",
            "request_id": "N/A",
            "raw_payload": "{}",
            "timestamp": utc_now_iso(),
        })
        if hs is not None:
            hs.report_failure(PROVIDER_NAME)
        return None


async def get_by_imdb_id(imdb_id: str, chat_id: str = "system") -> Optional[Dict[str, Any]]:
    """Fetch a single movie by IMDb ID from OMDb."""
    api_key = _api_key()
    if not api_key or not imdb_id:
        return None

    hs = _health()
    if hs is not None and not hs.is_healthy(PROVIDER_NAME):
        logger.warning("[omdb_client] circuit OPEN – call skipped")
        return None

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(OMDB_URL, params={"apikey": api_key, "i": imdb_id})
            data = resp.json()
            if data.get("Response") == "True":
                if hs is not None:
                    hs.report_success(PROVIDER_NAME)
                    hs.increment_daily_calls(PROVIDER_NAME)
                LoggingService.log_api_usage(
                    provider=PROVIDER_NAME,
                    action="get_by_imdb_id",
                    chat_id=chat_id,
                )
                return data
    except Exception as exc:
        logger.error("OMDb request failed for %r: %s", imdb_id, exc)
        error_batcher.emit({
            "chat_id": "system",
            "error_type": "omdb_request_failed",
            "error_message": str(exc),
            "workflow_step": "omdb_client.get_by_imdb_id",
            "intent": "discovery",
            "request_id": "N/A",
            "raw_payload": "{}",
            "timestamp": utc_now_iso(),
        })
        if hs is not None:
            hs.report_failure(PROVIDER_NAME)
    return None
