"""Telegram send helpers.

BUG #8 FIX: every send_message / edit_message call now stores its final
text in the worker_service context var so log_interaction is populated.

Also exposes:
  - BASE_URL   : the raw Telegram Bot API base URL used by telegram_card.py
                 for direct sendPhoto calls.
  - show_typing: convenience wrapper around sendChatAction used by handlers.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger("telegram_helpers")

# ---------------------------------------------------------------------------
# BASE_URL — used by telegram_card.py for sendPhoto requests
# ---------------------------------------------------------------------------

def _get_base_url() -> str:
    """Return the Telegram Bot API base URL, derived from the bot token."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return f"https://api.telegram.org/bot{token}"


# Lazy property: evaluated once on first import so tests can mock the env var.
BASE_URL: str = _get_base_url()


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _record_response(text: str) -> None:
    """Store the last bot response text in the worker context var (best-effort)."""
    try:
        from services.worker_service import set_bot_response
        set_bot_response(str(text))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public send helpers
# ---------------------------------------------------------------------------

async def send_message(
    chat_id: Any,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[Any] = None,
    **kwargs,
) -> Optional[Any]:
    """Send a Telegram message and record the response text."""
    from clients.telegram_client import TelegramClient
    client = TelegramClient.get_instance()
    result = await client.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        **kwargs,
    )
    _record_response(text)
    return result


async def edit_message(
    chat_id: Any,
    message_id: Any,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[Any] = None,
    **kwargs,
) -> Optional[Any]:
    """Edit an existing Telegram message and record the new text."""
    from clients.telegram_client import TelegramClient
    client = TelegramClient.get_instance()
    result = await client.edit_message(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
        **kwargs,
    )
    _record_response(text)
    return result


async def answer_callback_query(
    callback_query_id: str,
    text: str = "",
    show_alert: bool = False,
    **kwargs,
) -> None:
    """Answer a Telegram callback query (dismisses the loading spinner)."""
    from clients.telegram_client import TelegramClient
    client = TelegramClient.get_instance()
    await client.answer_callback_query(
        callback_query_id=callback_query_id,
        text=text,
        show_alert=show_alert,
        **kwargs,
    )


async def show_typing(chat_id: Any) -> None:
    """Send a 'typing…' chat action so the user sees a visual indicator.

    Errors are silently swallowed — a missing typing indicator is never
    worth crashing a handler over.
    """
    try:
        from clients.telegram_client import TelegramClient
        client = TelegramClient.get_instance()
        await client.send_chat_action(chat_id=chat_id, action="typing")
    except Exception as exc:
        logger.debug("[show_typing] suppressed error: %s", exc)
