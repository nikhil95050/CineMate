"""Handlers for recommendation-mode intents: movie, trending, surprise, more_like, more_suggestions."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from clients.telegram_card import send_movies_async
from clients.telegram_helpers import send_message, show_typing
from models.domain import MovieModel, SessionModel, UserModel
from services.container import rec_service, session_service, user_service
from services.logging_service import get_logger

logger = get_logger("movie_handlers")

# Matches any leading slash-command word so that both '/movie Inception' and
# 'movie_search Inception' (routed via normalizer) strip correctly.
_MOVIE_PREFIX_RE = re.compile(r"^/?(?:movie(?:_search)?)\s+", re.IGNORECASE)


async def handle_movie(
    chat_id: Any,
    input_text: str,
    session: Optional[Dict[str, Any]] = None,
    user: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Handle /movie <title> — similarity recommendations."""
    text = input_text.strip()
    match = _MOVIE_PREFIX_RE.match(text)
    seed_title = text[match.end():].strip() if match else ""

    if not seed_title:
        await send_message(
            chat_id,
            "\U0001f3ac <b>Movie Similarity</b>\n\n"
            "Please tell me a movie title:\n<code>/movie Inception</code>",
        )
        return

    await show_typing(chat_id)
    await send_message(chat_id, f"\U0001f50d Finding movies similar to <b>{seed_title}</b>\u2026")

    session_model = session_service.get_session(str(chat_id))
    user_model = user_service.get_user(str(chat_id))

    movies = await rec_service.get_recommendations(
        session_model, user_model, mode="movie", chat_id=str(chat_id), seed_title=seed_title
    )
    await send_movies_async(chat_id, movies)


async def handle_trending(
    chat_id: Any,
    input_text: str = "",
    session: Optional[Dict[str, Any]] = None,
    user: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Handle /trending — show what's popular right now."""
    await show_typing(chat_id)
    await send_message(chat_id, "\U0001f4c8 Fetching what's trending\u2026")

    session_model = session_service.get_session(str(chat_id))
    user_model = user_service.get_user(str(chat_id))

    movies = await rec_service.get_recommendations(
        session_model, user_model, mode="trending", chat_id=str(chat_id)
    )
    await send_movies_async(chat_id, movies)


async def handle_surprise(
    chat_id: Any,
    input_text: str = "",
    session: Optional[Dict[str, Any]] = None,
    user: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Handle /surprise — curated hidden gems."""
    await show_typing(chat_id)
    await send_message(chat_id, "\U0001f3b2 Picking a surprise for you\u2026")

    session_model = session_service.get_session(str(chat_id))
    user_model = user_service.get_user(str(chat_id))

    movies = await rec_service.get_recommendations(
        session_model, user_model, mode="surprise", chat_id=str(chat_id)
    )
    await send_movies_async(chat_id, movies)


async def handle_more_like(
    chat_id: Any,
    input_text: str = "",
    session: Optional[Dict[str, Any]] = None,
    user: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Handle more_like_{movie_id} callback — find similar movies, excluding already-seen titles."""
    movie_id = input_text.replace("more_like_", "", 1).strip()

    session_model = session_service.get_session(str(chat_id))
    user_model = user_service.get_user(str(chat_id))

    # Resolve seed title from last_recs
    seed_title = movie_id  # fallback to ID
    last_recs_raw: list = []
    try:
        last_recs_raw = json.loads(session_model.last_recs_json or "[]")
        for rec in last_recs_raw:
            if str(rec.get("movie_id", "")) == movie_id:
                seed_title = rec.get("title", movie_id)
                break
    except Exception:
        pass

    await show_typing(chat_id)
    await send_message(chat_id, f"\U0001f3af Finding movies like <b>{seed_title}</b>\u2026")

    seen_titles: list = []
    try:
        seen_titles = [
            r.get("title", "") for r in last_recs_raw if r.get("title")
        ]
    except Exception:
        pass

    movies = await rec_service.get_recommendations(
        session_model,
        user_model,
        mode="more_like",
        chat_id=str(chat_id),
        seed_title=seed_title,
        seen_titles=seen_titles,
    )
    await send_movies_async(chat_id, movies)


async def handle_more_suggestions(
    chat_id: Any,
    input_text: str = "",
    session: Optional[Dict[str, Any]] = None,
    user: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Handle more_suggestions_action — drain overflow buffer or re-discover."""
    await show_typing(chat_id)

    session_model = session_service.get_session(str(chat_id))
    user_model = user_service.get_user(str(chat_id))

    movies = await rec_service.get_more_suggestions(
        session_model, user_model, chat_id=str(chat_id)
    )
    await send_movies_async(chat_id, movies)
