"""Thin async wrapper around the Perplexity chat-completions API.

Integrates with HealthService for circuit-breaker protection:
  - Checks is_healthy() before making a call.
  - Calls report_failure() on HTTP errors / exceptions.
  - Calls report_success() + increment_daily_calls() on success.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional

import httpx

from services.logging_service import LoggingService, get_logger, error_batcher
from utils.time_utils import utc_now_iso

logger = get_logger("perplexity")

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
DEFAULT_MODEL  = "sonar"
PROVIDER_NAME  = "perplexity"


def _api_key() -> str:
    return os.environ.get("PERPLEXITY_API_KEY", "").strip()


def _health():
    """Return the HealthService singleton, or None if DI not yet ready."""
    try:
        from services.container import health_service
        return health_service
    except Exception:
        return None


async def chat(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    timeout: float = 30.0,
    chat_id: str = "system",
) -> Optional[str]:
    """Call Perplexity and return the assistant message content, or None on failure."""
    api_key = _api_key()
    if not api_key:
        logger.warning("PERPLEXITY_API_KEY not set — skipping Perplexity call")
        error_batcher.emit({
            "chat_id": str(chat_id),
            "error_type": "missing_api_key",
            "error_message": "PERPLEXITY_API_KEY is not set",
            "workflow_step": "perplexity_client.chat",
            "intent": "discovery",
            "request_id": "N/A",
            "raw_payload": "{}",
            "timestamp": utc_now_iso(),
        })
        return None

    # ── Circuit-breaker / feature-flag guard ─────────────────────────────────
    hs = _health()
    if hs is not None:
        is_healthy = await asyncio.to_thread(hs.is_healthy, PROVIDER_NAME)
        if not is_healthy:
            logger.warning("[perplexity_client] circuit OPEN – call skipped")
            return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(PERPLEXITY_URL, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            # ── Success path ─────────────────────────────────────────────────
            if hs is not None:
                asyncio.create_task(asyncio.to_thread(hs.report_success, PROVIDER_NAME))
                asyncio.create_task(asyncio.to_thread(hs.increment_daily_calls, PROVIDER_NAME))
            LoggingService.log_api_usage(
                provider=PROVIDER_NAME,
                action=f"chat:{model}",
                chat_id=chat_id,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
            )
            return content

    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        body = exc.response.text[:500]
        logger.error(
            "Perplexity HTTP %s error: %s", status_code, body,
            extra={"status_code": status_code, "body": body},
        )
        error_batcher.emit({
            "chat_id": "system",
            "error_type": f"perplexity_http_{status_code}",
            "error_message": f"HTTP {status_code}: {body}",
            "workflow_step": "perplexity_client.chat",
            "intent": "discovery",
            "request_id": "N/A",
            "raw_payload": "{}",
            "timestamp": utc_now_iso(),
        })
        if hs is not None:
            asyncio.create_task(asyncio.to_thread(hs.report_failure, PROVIDER_NAME))
        return None

    except Exception as exc:
        logger.error("Perplexity request failed: %s", exc)
        error_batcher.emit({
            "chat_id": "system",
            "error_type": "perplexity_request_failed",
            "error_message": str(exc),
            "workflow_step": "perplexity_client.chat",
            "intent": "discovery",
            "request_id": "N/A",
            "raw_payload": "{}",
            "timestamp": utc_now_iso(),
        })
        if hs is not None:
            asyncio.create_task(asyncio.to_thread(hs.report_failure, PROVIDER_NAME))
        return None
