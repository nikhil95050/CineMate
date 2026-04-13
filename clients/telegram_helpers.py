"""Thin async wrappers around the Telegram Bot API.

All functions are fire-and-forget friendly and swallow network errors
so a failed Telegram call never crashes the bot logic.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

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


def build_question_keyboard(
    q_key: str,
    options: List[str],
    selected: Optional[List[str]] = None,
    show_skip: bool = True,
    show_done: bool = False,
) -> Dict[str, Any]:
    """Build a Telegram InlineKeyboardMarkup for a question-engine question.

    Args:
        q_key:    The question key, e.g. ``"genre"``.  Used to build callback
                  data strings of the form ``q_{q_key}_{option}``.
        options:  List of choice labels to display as buttons.
        selected: For multi-select questions (genre), the list of already-
                  chosen options.  Selected options get a leading ✓ checkmark.
        show_skip: Whether to append a Skip button (callback: ``q_skip_{q_key}``).
        show_done: Whether to append a Done button (callback: ``q_done_{q_key}``).
                   Used for multi-select questions where the user confirms their
                   selection explicitly.

    Returns:
        A ``reply_markup`` dict ready to pass to ``send_message``.

    Example output (genre question, Action already selected)::

        {
            "inline_keyboard": [
                [{"text": "✓ Action", "callback_data": "q_genre_Action"},
                 {"text": "Comedy",   "callback_data": "q_genre_Comedy"}],
                ...
                [{"text": "✓ Done",   "callback_data": "q_done_genre"},
                 {"text": "Skip",     "callback_data": "q_skip_genre"}],
            ]
        }
    """
    selected_set: set[str] = set(s.strip() for s in (selected or []) if s.strip())

    # Layout: 2 buttons per row for option buttons
    rows: List[List[Dict[str, str]]] = []
    row: List[Dict[str, str]] = []
    for opt in options:
        label = f"\u2713 {opt}" if opt in selected_set else opt
        row.append({"text": label, "callback_data": f"q_{q_key}_{opt}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:  # leftover odd button
        rows.append(row)

    # Control row: Done and/or Skip
    control_row: List[Dict[str, str]] = []
    if show_done:
        done_label = "\u2713 Done" if selected_set else "Done"
        control_row.append({"text": done_label, "callback_data": f"q_done_{q_key}"})
    if show_skip:
        control_row.append({"text": "Skip", "callback_data": f"q_skip_{q_key}"})
    if control_row:
        rows.append(control_row)

    return {"inline_keyboard": rows}
