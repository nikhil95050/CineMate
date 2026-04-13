"""Input normalization and intent detection for CineMate.

This is a trimmed, dependency-light version of Antigravity's handlers.normalizer,
kept focused on:
  - normalizing Telegram updates into a consistent internal dict shape
  - mapping raw text into a small set of high-level intents

It is intentionally conservative so it stays compatible with future expansions
(question engine, admin commands, etc.).
"""
from __future__ import annotations

from typing import Any, Dict, Optional
import datetime as _dt


def normalize_input(update: Dict[str, Any]) -> Dict[str, Any]:
    """Extract core fields from a Telegram update object.

    Returns a dict containing at least:
      - update_id
      - chat_id
      - username
      - input_text
      - action_type ('message' or 'callback')
      - callback_query_id
      - message_id
      - sent_at (ISO8601) when available

    Unsupported update types will result in chat_id=None so callers can ignore.
    """
    result: Dict[str, Any] = {
        "update_id": update.get("update_id"),
        "chat_id": None,
        "username": "",
        "input_text": "",
        "action_type": "unknown",
        "callback_query_id": None,
        "message_id": None,
        "sent_at": None,
    }

    if "message" in update:
        msg = update["message"] or {}
        chat = msg.get("chat", {})
        result["chat_id"] = chat.get("id")
        result["username"] = (msg.get("from") or {}).get("username", "")
        result["input_text"] = (msg.get("text") or "").strip()
        result["action_type"] = "message"
        result["message_id"] = msg.get("message_id")
        ts = msg.get("date")
        if ts:
            result["sent_at"] = _dt.datetime.fromtimestamp(
                ts, _dt.timezone.utc
            ).isoformat()

    elif "callback_query" in update:
        cq = update["callback_query"] or {}
        msg = cq.get("message", {})
        chat = msg.get("chat", {})
        result["chat_id"] = chat.get("id")
        result["username"] = (cq.get("from") or {}).get("username", "")
        result["input_text"] = (cq.get("data") or "").strip()
        result["action_type"] = "callback"
        result["callback_query_id"] = cq.get("id")
        result["message_id"] = msg.get("message_id")
        ts = msg.get("date")
        if ts:
            result["sent_at"] = _dt.datetime.fromtimestamp(
                ts, _dt.timezone.utc
            ).isoformat()

    return result


def detect_intent(input_text: str, session: Optional[Dict[str, Any]] = None) -> str:
    """Map raw input text to a logical bot intent.

    This mirrors the intent mapping of Antigravity at a high level but keeps
    only the core commands needed for early development.
    """
    text = (input_text or "").lower().strip()

    # Simple commands
    if text.startswith("/start"):
        return "start"
    if text.startswith("/reset"):
        return "reset"
    if text.startswith("/help"):
        return "help"
    if text.startswith("/rating") or text.startswith("/min_rating"):
        return "min_rating"
    if text.startswith("/movie"):
        return "movie"
    if text.startswith("/search"):
        return "search"

    # BUG FIX #2: use startswith so /trending@BotName and /trending <args>
    # are handled correctly, not just the bare exact string.
    if text.startswith("/trending") or text == "trending":
        return "trending"
    if text.startswith("/surprise") or text == "surprise":
        return "surprise"

    # Repository-like views
    if text.startswith("/history") or text.startswith("history_p"):
        return "history"
    if text.startswith("/watchlist") or text.startswith("watchlist_p"):
        return "watchlist"

    # Callback-style actions
    if text.startswith("watched_"):
        return "watched"
    if text.startswith("save_"):
        return "save"
    if text.startswith("more_like_"):
        return "more_like"
    if text.startswith("like_"):
        return "like"
    if text.startswith("dislike_"):
        return "dislike"

    # BUG FIX #3: also match plain "more_suggestions" callback data sent by
    # Telegram inline buttons (no slash prefix, no _action suffix).
    if text in ("/more_suggestions", "more_suggestions_action", "more_suggestions"):
        return "more_suggestions"

    # Questionnaire flow callbacks
    if text.startswith("q_"):
        if text == "q_more_recs":
            return "more_suggestions"
        if text == "q_reset":
            return "reset"
        return "questioning"

    # BUG FIX #1: admin commands — strip only the leading slash and take the
    # first word so "/admin_broadcast hello world" → "admin_broadcast", not
    # the full remaining string which never matches any intent in worker_service.
    if text.startswith("/admin_"):
        return text.split()[0].replace("/", "", 1)

    # If the session says we are in questioning mode, keep sending to that flow
    if (session or {}).get("session_state") == "questioning":
        return "questioning"

    return "fallback"
