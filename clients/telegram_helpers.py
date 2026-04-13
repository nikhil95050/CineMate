"""Telegram send helpers.

BUG #8 FIX: every send_message / edit_message call now stores its final
text in the worker_service context var so log_interaction is populated.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("telegram_helpers")


def _record_response(text: str) -> None:
    """Store the last bot response text in the worker context var (best-effort)."""
    try:
        from services.worker_service import set_bot_response
        set_bot_response(str(text))
    except Exception:
        pass


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
    from clients.telegram_client import TelegramClient
    client = TelegramClient.get_instance()
    await client.answer_callback_query(
        callback_query_id=callback_query_id,
        text=text,
        show_alert=show_alert,
        **kwargs,
    )
