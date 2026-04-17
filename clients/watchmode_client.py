"""Thin async wrapper around the Watchmode streaming-availability API.

Integrates with HealthService for circuit-breaker protection:
  - Checks is_healthy() before making a call.
  - Calls report_failure() on network errors.
  - Calls report_success() + increment_daily_calls() on success.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from services.logging_service import LoggingService, get_logger

logger = get_logger("watchmode")

WATCHMODE_URL  = "https://api.watchmode.com/v1"
PROVIDER_NAME  = "watchmode"


def _api_key() -> str:
    """Read lazily so load_dotenv() always runs first."""
    return os.environ.get("WATCHMODE_API_KEY", "").strip()


def _health():
    try:
        from services.container import health_service
        return health_service
    except Exception:
        return None


async def get_streaming_sources(imdb_id: str, chat_id: str = "system") -> List[Dict[str, Any]]:
    """Return a list of streaming source dicts for a given IMDb ID.

    Returns an empty list when the key is absent, the circuit is open, or
    the call fails.
    """
    api_key = _api_key()
    if not api_key or not imdb_id:
        return []

    hs = _health()
    if hs is not None and not hs.is_healthy(PROVIDER_NAME):
        logger.warning("[watchmode_client] circuit OPEN – call skipped")
        return []

    try:
        search_url = f"{WATCHMODE_URL}/search/"
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Step 1: resolve IMDb ID → Watchmode title ID
            resp = await client.get(
                search_url,
                params={
                    "apiKey": api_key,
                    "search_field": "imdb_id",
                    "search_value": imdb_id,
                },
            )
            data = resp.json()
            results = data.get("title_results", [])
            if not results:
                return []
            title_id = results[0].get("id")
            if not title_id:
                return []

            # Step 2: fetch streaming sources
            src_resp = await client.get(
                f"{WATCHMODE_URL}/title/{title_id}/sources/",
                params={"apiKey": api_key},
            )
            sources = src_resp.json() if isinstance(src_resp.json(), list) else []

        # ── Success path ─────────────────────────────────────────────────
        if hs is not None:
            hs.report_success(PROVIDER_NAME)
            hs.increment_daily_calls(PROVIDER_NAME)
        LoggingService.log_api_usage(
            provider=PROVIDER_NAME,
            action="get_streaming_sources",
            chat_id=chat_id,
        )
        return sources

    except Exception as exc:
        logger.error("Watchmode request failed for %r: %s", imdb_id, exc)
        if hs is not None:
            hs.report_failure(PROVIDER_NAME)
        return []


def format_streaming_summary(sources: List[Dict[str, Any]]) -> str:
    """Convert raw Watchmode sources into a brief human-readable string."""
    seen: Dict[str, str] = {}
    for src in sources:
        name  = src.get("name", "")
        stype = src.get("type", "")  # 'sub', 'rent', 'buy', 'free'
        if name and stype in ("sub", "free") and name not in seen:
            seen[name] = stype
    if not seen:
        return ""
    parts = [f"{n}" for n in list(seen.keys())[:4]]
    return "📺 " + " · ".join(parts)
