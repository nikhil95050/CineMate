"""History, watchlist, save, and watched handlers."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from clients.telegram_helpers import (
    answer_callback_query,
    edit_message as edit_message_text,
    send_message,
)
from models.domain import MovieModel
import services.container as container
from services.logging_service import get_logger
from utils.formatters import format_history_list, format_watchlist_list

logger = get_logger("history_handlers")


def _pagination_keyboard(prefix: str, page: int, total_pages: int) -> Optional[Dict[str, Any]]:
    if total_pages <= 1:
        return None

    row: List[Dict[str, str]] = []
    if page > 1:
        row.append({"text": "◀ Prev", "callback_data": f"{prefix}_p{page - 1}"})
    row.append({"text": f"{page}/{total_pages}", "callback_data": f"{prefix}_p{page}"})
    if page < total_pages:
        row.append({"text": "Next ▶", "callback_data": f"{prefix}_p{page + 1}"})
    return {"inline_keyboard": [row]}


def _parse_page(input_text: str, prefix: str) -> int:
    text = (input_text or "").strip().lower()
    token = f"{prefix}_p"
    if text.startswith(token):
        try:
            return max(1, int(text.split("_p", 1)[1]))
        except (IndexError, ValueError):
            return 1
    return 1


def _movie_from_last_recs(
    chat_id: str,
    movie_id: str,
    session_row: Optional[Dict[str, Any]] = None,
) -> Optional[MovieModel]:
    try:
        if session_row is not None:
            recs = json.loads((session_row or {}).get("last_recs_json") or "[]")
        else:
            session_model = container.session_service.get_session(chat_id)
            recs = json.loads(session_model.last_recs_json or "[]")
        for rec in recs:
            if str(rec.get("movie_id", "")) == movie_id:
                return MovieModel(**rec)
    except Exception as exc:
        logger.debug("last_recs lookup failed for %s/%s: %s", chat_id, movie_id, exc)
    return None


def _resolve_movie(
    chat_id: str,
    movie_id: str,
    session_row: Optional[Dict[str, Any]] = None,
) -> Optional[MovieModel]:
    movie = _movie_from_last_recs(chat_id, movie_id, session_row=session_row)
    if movie is not None:
        return movie
    row = container.movie_service.get_movie_from_history(chat_id, movie_id)
    if row is None:
        return None
    if isinstance(row, MovieModel):
        return row
    if isinstance(row, dict):
        try:
            return MovieModel(**row)
        except Exception:
            return MovieModel.from_history_row(row)
    return None


async def _render_list(
    chat_id: Any,
    text: str,
    keyboard: Optional[Dict[str, Any]],
    callback_query_id: Optional[str] = None,
    message_id: Optional[int] = None,
) -> None:
    if callback_query_id:
        await answer_callback_query(callback_query_id)

    if message_id is not None and callback_query_id:
        try:
            await edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
            )
            return
        except Exception as exc:
            logger.debug("edit_message fallback to send_message: %s", exc)

    await send_message(chat_id, text, reply_markup=keyboard)


async def handle_history(
    chat_id: Any,
    input_text: str = "",
    message_id: Optional[int] = None,
    callback_query_id: Optional[str] = None,
    **kwargs,
) -> None:
    page = _parse_page(input_text, "history")
    rows = container.movie_service.get_history(str(chat_id), page=page)
    total_pages = container.movie_service.get_history_page_count(str(chat_id))

    text = format_history_list(rows, page=page, total_pages=total_pages)
    keyboard = _pagination_keyboard("history", page, total_pages)
    await _render_list(
        chat_id=chat_id,
        text=text,
        keyboard=keyboard,
        callback_query_id=callback_query_id,
        message_id=message_id,
    )


async def handle_watchlist(
    chat_id: Any,
    input_text: str = "",
    message_id: Optional[int] = None,
    callback_query_id: Optional[str] = None,
    **kwargs,
) -> None:
    page = _parse_page(input_text, "watchlist")
    rows = container.movie_service.get_watchlist(str(chat_id), page=page)
    total_pages = container.movie_service.get_watchlist_page_count(str(chat_id))

    text = format_watchlist_list(rows, page=page, total_pages=total_pages)
    keyboard = _pagination_keyboard("watchlist", page, total_pages)
    await _render_list(
        chat_id=chat_id,
        text=text,
        keyboard=keyboard,
        callback_query_id=callback_query_id,
        message_id=message_id,
    )


async def handle_save(
    chat_id: Any,
    input_text: str = "",
    callback_query_id: Optional[str] = None,
    session: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    movie_id = (input_text or "").replace("save_", "", 1).strip()
    if not movie_id:
        if callback_query_id:
            await answer_callback_query(callback_query_id, text="Invalid movie ID.")
        return

    chat_id_str = str(chat_id)
    movie = _resolve_movie(chat_id_str, movie_id, session_row=session)
    if movie is None:
        if callback_query_id:
            await answer_callback_query(callback_query_id, text="Movie not found.")
        await send_message(chat_id, "I couldn't find that movie in your recent recommendations.")
        return

    if container.movie_service.is_in_watchlist(chat_id_str, movie.movie_id):
        if callback_query_id:
            await answer_callback_query(callback_query_id, text="Already saved.")
        await send_message(chat_id, f"💾 <b>{movie.title}</b> is already in your watchlist.")
        return

    saved = container.movie_service.add_to_watchlist(chat_id_str, movie)
    if callback_query_id:
        await answer_callback_query(
            callback_query_id,
            text="Saved to watchlist." if saved else "Could not save movie.",
        )

    if saved:
        await send_message(chat_id, f"💾 Saved <b>{movie.title}</b> to your watchlist.")
    else:
        await send_message(chat_id, "⚠️ I couldn't save that movie right now. Please try again.")


async def handle_watched(
    chat_id: Any,
    input_text: str = "",
    callback_query_id: Optional[str] = None,
    **kwargs,
) -> None:
    chat_id_str = str(chat_id)
    text = (input_text or "").strip()
    movie_id = ""
    movie_title = ""

    if text.startswith("watched_"):
        movie_id = text.replace("watched_", "", 1).strip()
    else:
        for prefix in ("/watched ", "watched "):
            if text.lower().startswith(prefix):
                query = text[len(prefix) :].strip().lower()
                total_pages = container.movie_service.get_history_page_count(chat_id_str)
                for page in range(1, total_pages + 1):
                    for row in container.movie_service.get_history(chat_id_str, page=page):
                        title = str(row.get("title") or "")
                        if query == str(row.get("movie_id", "")).lower() or query in title.lower():
                            movie_id = str(row.get("movie_id", ""))
                            movie_title = title
                            break
                    if movie_id:
                        break
                break

    if not movie_id:
        if callback_query_id:
            await answer_callback_query(callback_query_id, text="Invalid movie ID.")
        await send_message(
            chat_id,
            "✅ Use <code>/watched Movie Title</code> or tap the Watched button on a card.",
        )
        return

    movie = _resolve_movie(chat_id_str, movie_id)
    if movie is not None:
        movie_title = movie.title

    updated = container.movie_service.mark_watched(chat_id_str, movie_id)
    if callback_query_id:
        await answer_callback_query(
            callback_query_id,
            text="Marked as watched." if updated else "Could not update watch status.",
        )

    if updated:
        await send_message(
            chat_id,
            f"✔ <b>{movie_title or movie_id}</b> marked as watched!",
        )
    else:
        await send_message(chat_id, "⚠️ I couldn't update that movie right now. Please try again.")


async def handle_clear_history(
    chat_id: Any,
    **kwargs,
) -> None:
    await send_message(
        chat_id,
        "ℹ️ History clearing isn't available in this build yet. Use /reset to start a fresh question flow.",
    )
