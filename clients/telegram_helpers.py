"""Thin async wrappers around the Telegram Bot API.

All functions are fire-and-forget friendly and swallow network errors
so a failed Telegram call never crashes the bot logic.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("telegram_helpers")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}" if BOT_TOKEN else ""

_client = httpx.AsyncClient(timeout=10.0)


async def _post(method: str, payload: Dict[str, Any]) -> Optional[Dict]:
    if not BASE_URL:
        logger.debug("[TG] BOT_TOKEN not set — skipping %s", method)
        return None
    try:
        r = await _client.post(f"{BASE_URL}/{method}", json=payload)
        return r.json()
    except Exception as exc:
        logger.warning("[TG] %s failed: %s", method, exc)
        return None


async def send_message(
    chat_id: Any,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    parse_mode: str = "HTML",
) -> Optional[Dict]:
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await _post("sendMessage", payload)


async def send_message_safely(
    chat_id: Any, text: str, **kwargs
) -> None:
    """send_message that silently swallows all exceptions."""
    try:
        await send_message(chat_id, text, **kwargs)
    except Exception:
        pass


async def edit_message_text(
    chat_id: Any,
    message_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    parse_mode: str = "HTML",
) -> Optional[Dict]:
    """Edit an existing message (used for in-place pagination updates)."""
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await _post("editMessageText", payload)


async def answer_callback_query(
    callback_query_id: str,
    text: str = "",
    show_alert: bool = False,
) -> Optional[Dict]:
    """Acknowledge a Telegram callback query (clears the loading spinner)."""
    return await _post(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
        },
    )


async def show_typing(chat_id: Any) -> None:
    """Send a typing action to indicate the bot is working."""
    await _post("sendChatAction", {"chat_id": chat_id, "action": "typing"})
