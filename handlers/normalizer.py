"""Input normalization and intent detection for CineMate."""
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
    """Map raw input text to a logical bot intent."""
    text = (input_text or "").lower().strip()
    if not text:
        return "fallback"

    cmd = text.split()[0].split('@')[0]

    # Simple commands
    if cmd == "/start":
        return "start"
    if cmd == "/reset":
        return "reset"
    if cmd == "/help":
        return "help"
    if cmd in ("/rating", "/min_rating"):
        return "min_rating"
    if cmd == "/movie":
        return "movie"
    if cmd == "/search":
        return "search"
    if cmd == "/star":
        return "star"
    if cmd == "/share":
        return "share"

    # ISSUE 5 FIX: route /clear_history to the (now implemented) handler.
    if cmd in ("/clear_history", "/clearhistory"):
        return "clear_history"

    # ISSUE 12 FIX: route /recommend to handle_recommend so it resets answers
    # and starts the question flow cleanly, instead of falling to fallback.
    if cmd == "/recommend":
        return "recommend"

    if cmd == "/trending" or text == "trending":
        return "trending"
    if cmd == "/surprise" or text == "surprise":
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

    if text in ("/more_suggestions", "more_suggestions_action", "more_suggestions"):
        return "more_suggestions"

    # Questionnaire flow callbacks
    if text.startswith("q_"):
        if text == "q_more_recs":
            return "more_suggestions"
        if text == "q_reset":
            return "reset"
        return "questioning"

    if text.startswith("admin_"):
        return text.split()[0]
    if text.startswith("/admin_"):
        return text.split()[0].replace("/", "", 1)

    if (session or {}).get("session_state") == "questioning":
        return "questioning"

    return "fallback"
