"""User-facing handlers: /start, /help, /reset, /watchlist, /history, and fallback.

/history and /watchlist now delegate to history_handlers which wires the full
paginated, repository-backed implementation. The stubs here are kept as thin
forwarding calls so worker_service.py requires no changes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from clients.telegram_helpers import send_message
from services.container import session_service, user_service
from services.recommendation_engine import QUESTIONS


async def handle_start(
    chat_id: Any,
    username: str | None,
    session: Dict[str, Any] | None,
    user: Dict[str, Any] | None,
    **kwargs,
) -> None:
    """Entry point for /start. Resets session and begins the question flow."""
    session_service.reset_session(str(chat_id))

    user_model = user_service.get_user(str(chat_id))
    user_model.username = username or user_model.username
    user_service.upsert_user(user_model)

    new_session = session_service.get_session(str(chat_id))
    new_session.session_state = "questioning"
    new_session.question_index = 0
    session_service.upsert_session(new_session)

    display_name = username or "Movie Fan"
    welcome = (
        f"<b>Hey there, {display_name}! \U0001f44b</b>\n\n"
        "I\u2019m CineMate, your personal guide to the world of cinema. "
        "I live and breathe movies, and I\u2019d love to help you find your next favorite film.\n\n"
        "To get started, I\u2019ve got a few quick questions to help me understand your vibe today. Ready?"
    )
    await send_message(chat_id, welcome)

    from .rec_handlers import _send_current_question
    await _send_current_question(chat_id, new_session.to_row())


async def handle_reset(
    chat_id: Any, username: str | None = None, **kwargs
) -> None:
    session_service.reset_session(str(chat_id))
    display_name = username or "friend"
    await send_message(
        chat_id,
        f"No worries, {display_name}! I\u2019ve cleared the slate. "
        "Whenever you\u2019re ready for a fresh start, just type /start and we\u2019ll dive back in. \U0001f37f",
    )


async def handle_help(chat_id: Any, **kwargs) -> None:
    help_text = (
        "<b>\U0001f3ac CineMate\u2019s Guide: How to Find Your Next Favorite Movie</b>\n\n"
        "I\u2019m here to make discovery fun! Here\u2019s how you can talk to me:\n\n"
        "\U0001f31f /start - Let\u2019s go on a personalised movie journey\n"
        "\U0001f50d /search - Tell me anything! (e.g. <i>\u2018gritty 90s thrillers\u2019</i>)\n"
        "\U0001f3ac /movie - Found something you liked? I\u2019ll find its cinematic twins\n"
        "\U0001f525 /trending - What the world is raving about right now\n"
        "\U0001f3b2 /surprise - Feeling brave? Let me pick a hidden gem for you\n\n"
        "\U0001f5c2 /history - Revisit our past discoveries\n"
        "\U0001f4c2 /watchlist - Your private collection of \u2018must-see\u2019 titles\n"
        "\U0001f504 /reset - Start with a clean slate"
    )
    await send_message(chat_id, help_text)


async def handle_watchlist(
    chat_id: Any,
    input_text: str = "",
    message_id: Optional[int] = None,
    callback_query_id: Optional[str] = None,
    **kwargs,
) -> None:
    """Delegate to history_handlers.handle_watchlist (full implementation)."""
    from handlers.history_handlers import handle_watchlist as _hw
    await _hw(
        chat_id=chat_id,
        input_text=input_text,
        message_id=message_id,
        callback_query_id=callback_query_id,
        **kwargs,
    )


async def handle_history(
    chat_id: Any,
    input_text: str = "",
    message_id: Optional[int] = None,
    callback_query_id: Optional[str] = None,
    **kwargs,
) -> None:
    """Delegate to history_handlers.handle_history (full implementation)."""
    from handlers.history_handlers import handle_history as _hh
    await _hh(
        chat_id=chat_id,
        input_text=input_text,
        message_id=message_id,
        callback_query_id=callback_query_id,
        **kwargs,
    )


async def handle_fallback(chat_id: Any, **kwargs) -> None:
    """Catch-all for unrecognised commands or idle free text."""
    await send_message(
        chat_id,
        "\U0001f914 Not sure what you mean! Here\u2019s what I can do:\n\n"
        "/start \u2013 Get personalised recommendations\n"
        "/help \u2013 See all commands\n"
        "/trending \u2013 What\u2019s popular right now\n"
        "/surprise \u2013 Random hidden gem\n"
        "/reset \u2013 Start over",
    )
