"""Handlers for /history, /watchlist, watched_* and save_* callbacks."""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from clients.telegram_helpers import answer_callback_query, edit_message_text, send_message
from models.domain import MovieModel
from services.movie_service import format_history_list, format_watchlist_list

PAGE_SIZE = 10


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def _history_keyboard(
    page: int, total_pages: int
) -> Optional[Dict[str, Any]]:
    """Inline keyboard with Prev / Next navigation for history."""
    buttons: List[Dict[str, str]] = []
    if page > 1:
        buttons.append(
            {"text": "\u25c0 Prev", "callback_data": f"history_p{page - 1}"}
        )
    if page < total_pages:
        buttons.append(
            {"text": "Next \u25b6", "callback_data": f"history_p{page + 1}"}
        )
    if not buttons:
        return None
    return {"inline_keyboard": [buttons]}


def _watchlist_keyboard(
    page: int, total_pages: int
) -> Optional[Dict[str, Any]]:
    """Inline keyboard with Prev / Next navigation for watchlist."""
    buttons: List[Dict[str, str]] = []
    if page > 1:
        buttons.append(
            {
                "text": "\u25c0 Prev",
                "callback_data": f"watchlist_p{page - 1}",
            }
        )
    if page < total_pages:
        buttons.append(
            {
                "text": "Next \u25b6",
                "callback_data": f"watchlist_p{page + 1}",
            }
        )
    if not buttons:
        return None
    return {"inline_keyboard": [buttons]}


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

async def handle_history(
    chat_id: Any,
    input_text: str = "",
    message_id: Optional[int] = None,
    callback_query_id: Optional[str] = None,
    **kwargs,
) -> None:
    """Handle /history command and history_pN pagination callbacks."""
    from services.container import movie_service

    # Parse page number from callback data like "history_p3" or command "/history"
    page = 1
    text_lower = (input_text or "").lower().strip()
    if text_lower.startswith("history_p"):
        try:
            page = int(text_lower.replace("history_p", ""))
        except ValueError:
            page = 1

    rows = movie_service.get_history(str(chat_id), page=page)
    total_pages = movie_service.get_history_page_count(str(chat_id))
    total_pages = max(total_pages, 1)
    page = min(page, total_pages)

    text = format_history_list(rows, page, total_pages)
    keyboard = _history_keyboard(page, total_pages)

    is_pagination = callback_query_id is not None and message_id is not None

    if is_pagination:
        # Edit the existing message in place — avoid flooding the chat
        await edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
        )
        await answer_callback_query(callback_query_id)
    else:
        await send_message(chat_id, text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# /watchlist
# ---------------------------------------------------------------------------

async def handle_watchlist(
    chat_id: Any,
    input_text: str = "",
    message_id: Optional[int] = None,
    callback_query_id: Optional[str] = None,
    **kwargs,
) -> None:
    """Handle /watchlist command and watchlist_pN pagination callbacks."""
    from services.container import movie_service

    page = 1
    text_lower = (input_text or "").lower().strip()
    if text_lower.startswith("watchlist_p"):
        try:
            page = int(text_lower.replace("watchlist_p", ""))
        except ValueError:
            page = 1

    rows = movie_service.get_watchlist(str(chat_id), page=page)
    total_pages = movie_service.get_watchlist_page_count(str(chat_id))
    total_pages = max(total_pages, 1)
    page = min(page, total_pages)

    text = format_watchlist_list(rows, page, total_pages)
    keyboard = _watchlist_keyboard(page, total_pages)

    is_pagination = callback_query_id is not None and message_id is not None

    if is_pagination:
        await edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=text,
            reply_markup=keyboard,
        )
        await answer_callback_query(callback_query_id)
    else:
        await send_message(chat_id, text, reply_markup=keyboard)


# ---------------------------------------------------------------------------
# watched_* callback
# ---------------------------------------------------------------------------

async def handle_watched(
    chat_id: Any,
    input_text: str = "",
    callback_query_id: Optional[str] = None,
    **kwargs,
) -> None:
    """Mark a movie as watched. callback_data = 'watched_{movie_id}'."""
    from services.container import movie_service

    movie_id = (input_text or "").replace("watched_", "", 1).strip()
    if not movie_id:
        if callback_query_id:
            await answer_callback_query(
                callback_query_id, text="\u26a0\ufe0f Invalid movie ID."
            )
        return

    success = movie_service.mark_watched(str(chat_id), movie_id)

    # Friendly feedback — resolve title from history if possible
    row = movie_service.get_movie_from_history(str(chat_id), movie_id)
    title = (row or {}).get("title", "That movie") if row else "That movie"

    if success:
        msg = f"\u2714\ufe0f <b>{title}</b> marked as watched!"
    else:
        msg = f"\u26a0\ufe0f Couldn\u2019t update <b>{title}</b> \u2014 please try again."

    if callback_query_id:
        await answer_callback_query(callback_query_id, text=msg, show_alert=False)
    await send_message(chat_id, msg)


# ---------------------------------------------------------------------------
# save_* callback
# ---------------------------------------------------------------------------

async def handle_save(
    chat_id: Any,
    input_text: str = "",
    callback_query_id: Optional[str] = None,
    session: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Save a movie to the watchlist.

    callback_data = 'save_{movie_id}'

    Resolution order:
      1. last_recs in session
      2. recommendation_history table (via MovieService)
    If the movie cannot be found in either place, send a friendly stale-state
    message rather than crashing.
    """
    from services.container import movie_service

    movie_id = (input_text or "").replace("save_", "", 1).strip()
    if not movie_id:
        if callback_query_id:
            await answer_callback_query(
                callback_query_id, text="\u26a0\ufe0f Invalid movie ID."
            )
        return

    # --- 1. Try last_recs from session ---
    movie_dict: Optional[Dict[str, Any]] = None
    try:
        last_recs = json.loads((session or {}).get("last_recs_json") or "[]")
        for rec in last_recs:
            if str(rec.get("movie_id", "")) == movie_id:
                movie_dict = rec
                break
    except Exception:
        pass

    # --- 2. Fallback: recommendation history ---
    if movie_dict is None:
        movie_dict = movie_service.get_movie_from_history(
            str(chat_id), movie_id
        )

    if movie_dict is None:
        msg = (
            "\u26a0\ufe0f I couldn\u2019t find that movie in your recent recommendations. "
            "It may have been cleared. Try getting new recommendations first!"
        )
        if callback_query_id:
            await answer_callback_query(
                callback_query_id, text=msg, show_alert=True
            )
        await send_message(chat_id, msg)
        return

    movie = MovieModel.from_history_row(movie_dict)
    title = movie.title

    already_saved = (
        movie_service.watchlist_repo is not None
        and movie_service.watchlist_repo.is_in_watchlist(
            str(chat_id), movie_id
        )
    )

    if already_saved:
        msg = f"\U0001f4cc <b>{title}</b> is already in your watchlist!"
        if callback_query_id:
            await answer_callback_query(
                callback_query_id, text=msg, show_alert=False
            )
        await send_message(chat_id, msg)
        return

    success = movie_service.add_to_watchlist(str(chat_id), movie)

    if success:
        msg = f"\U0001f4cc <b>{title}</b> saved to your watchlist! View it with /watchlist."
    else:
        msg = f"\u26a0\ufe0f Couldn\u2019t save <b>{title}</b> \u2014 please try again."

    if callback_query_id:
        await answer_callback_query(
            callback_query_id, text=msg, show_alert=False
        )
    await send_message(chat_id, msg)
