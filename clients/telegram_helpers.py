"""Telegram send helpers.

All helpers route through TelegramClient so webhook mode and local polling use
the same Bot API surface.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from clients.telegram_client import TelegramClient

logger = logging.getLogger("telegram_helpers")


def _normalise_reply_markup(reply_markup: Optional[Any]) -> Optional[Any]:
    if reply_markup is None:
        return None
    if isinstance(reply_markup, str):
        try:
            return json.loads(reply_markup)
        except json.JSONDecodeError:
            logger.warning("Invalid reply_markup JSON string; sending without keyboard")
            return None
    return reply_markup


def _record_response(text: str) -> None:
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
    client = TelegramClient.get_instance()
    result = await client.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        reply_markup=_normalise_reply_markup(reply_markup),
        **kwargs,
    )
    _record_response(text)
    return result


async def send_message_safely(
    chat_id: Any,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[Any] = None,
    **kwargs,
) -> Optional[Any]:
    try:
        return await send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            **kwargs,
        )
    except Exception as exc:
        logger.warning("send_message_safely failed for %s: %s", chat_id, exc)
        return None


async def edit_message(
    chat_id: Any,
    message_id: Any,
    text: str,
    parse_mode: str = "HTML",
    reply_markup: Optional[Any] = None,
    **kwargs,
) -> Optional[Any]:
    client = TelegramClient.get_instance()
    try:
        result = await client.edit_message(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            parse_mode=parse_mode,
            reply_markup=_normalise_reply_markup(reply_markup),
            **kwargs,
        )
        _record_response(text)
        return result
    except Exception as exc:
        message = str(exc).lower()
        if "message is not modified" in message:
            return None
        raise


async def answer_callback_query(
    callback_query_id: str,
    text: str = "",
    show_alert: bool = False,
    **kwargs,
) -> None:
    client = TelegramClient.get_instance()
    await client.answer_callback_query(
        callback_query_id=callback_query_id,
        text=text,
        show_alert=show_alert,
        **kwargs,
    )


async def show_typing(chat_id: Any) -> None:
    try:
        client = TelegramClient.get_instance()
        await client.send_chat_action(chat_id=chat_id, action="typing")
    except Exception as exc:
        logger.debug("show_typing failed for %s: %s", chat_id, exc)
