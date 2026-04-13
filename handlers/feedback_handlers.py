"""Handlers for like_*, dislike_*, and /min_rating callbacks.

All handlers:
  - log the reaction to FeedbackRepository.
  - for dislikes: add genre(s) to user.disliked_genres.
  - schedule a background call to UserService.recompute_taste_profile.
  - never crash when feedback/history tables are empty.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from clients.telegram_helpers import answer_callback_query, send_message
from services.container import movie_service, user_service
from services.container import feedback_repo, history_repo  # noqa: F401

logger = logging.getLogger("feedback_handlers")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _schedule_taste_recompute(chat_id: str) -> None:
    """Fire-and-forget: recompute taste profile without blocking the handler."""
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, user_service.recompute_taste_profile, chat_id)
    except RuntimeError:
        try:
            user_service.recompute_taste_profile(chat_id)
        except Exception as exc:
            logger.warning(
                "[FeedbackHandlers] inline recompute failed: %s", exc
            )
    except Exception as exc:
        logger.warning(
            "[FeedbackHandlers] schedule taste recompute failed: %s", exc
        )


def _resolve_movie_info(
    chat_id: str,
    movie_id: str,
    session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Try to get movie info from session last_recs or history.

    Returns a dict with at least 'title' and 'genres' (may be empty strings).
    Never raises.
    """
    import json

    # 1. Try session last_recs (already plain dicts)
    try:
        last_recs = json.loads((session or {}).get("last_recs_json") or "[]")
        for rec in last_recs:
            if str(rec.get("movie_id", "")) == movie_id:
                return rec
    except Exception:
        pass

    # 2. Try history repository.
    # Fix #6 — get_movie_from_history ALWAYS returns a MovieModel (or None),
    # never a plain dict.  The old code used a hasattr / dict(row) fallback
    # that masked the bug and would still fail on a Pydantic model because
    # dict(model) produces {field: FieldInfo} in Pydantic v2, not field values.
    # Always call .model_dump() (v2) or .dict() (v1) explicitly.
    try:
        row = movie_service.get_movie_from_history(chat_id, movie_id)
        if row is not None:
            if hasattr(row, "model_dump"):   # Pydantic v2
                return row.model_dump()
            if hasattr(row, "dict"):         # Pydantic v1
                return row.dict()
            # Genuine plain dict (defensive — should not happen with current service)
            if isinstance(row, dict):
                return row
    except Exception:
        pass

    return {"title": movie_id, "genres": ""}


# ---------------------------------------------------------------------------
# like_*
# ---------------------------------------------------------------------------

async def handle_like(
    chat_id: Any,
    input_text: str = "",
    callback_query_id: Optional[str] = None,
    session: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Handle like_{movie_id} callback."""
    movie_id = (input_text or "").replace("like_", "", 1).strip()
    if not movie_id:
        if callback_query_id:
            await answer_callback_query(
                callback_query_id, text="⚠️ Invalid movie ID."
            )
        return

    chat_id_str = str(chat_id)

    # Resolve title for user-facing message
    info = _resolve_movie_info(chat_id_str, movie_id, session)
    title = info.get("title") or movie_id

    # Log reaction
    try:
        from services.container import feedback_repo as fb_repo
        fb_repo.log_reaction(chat_id_str, movie_id, "like")
    except Exception as exc:
        logger.warning("[handle_like] log_reaction failed: %s", exc)

    # Acknowledge callback
    msg = f"👍 Liked <b>{title}</b>! I'll find you more like this."
    if callback_query_id:
        await answer_callback_query(
            callback_query_id, text=f"👍 Liked {title}!", show_alert=False
        )
    await send_message(chat_id, msg)

    # Schedule background taste recompute
    _schedule_taste_recompute(chat_id_str)


# ---------------------------------------------------------------------------
# dislike_*
# ---------------------------------------------------------------------------

async def handle_dislike(
    chat_id: Any,
    input_text: str = "",
    callback_query_id: Optional[str] = None,
    session: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Handle dislike_{movie_id} callback."""
    movie_id = (input_text or "").replace("dislike_", "", 1).strip()
    if not movie_id:
        if callback_query_id:
            await answer_callback_query(
                callback_query_id, text="⚠️ Invalid movie ID."
            )
        return

    chat_id_str = str(chat_id)

    # Resolve genres for disliked_genres update
    info = _resolve_movie_info(chat_id_str, movie_id, session)
    title = info.get("title") or movie_id
    genres_raw: str = info.get("genres") or ""

    # Log reaction
    try:
        from services.container import feedback_repo as fb_repo
        fb_repo.log_reaction(chat_id_str, movie_id, "dislike")
    except Exception as exc:
        logger.warning("[handle_dislike] log_reaction failed: %s", exc)

    # Update disliked_genres on user profile
    if genres_raw:
        try:
            new_genres = [g.strip() for g in genres_raw.split(",") if g.strip()]
            user = user_service.get_user(chat_id_str)
            existing = list(user.disliked_genres or [])
            for g in new_genres:
                if g not in existing:
                    existing.append(g)
            user.disliked_genres = existing
            user_service.upsert_user(user)
        except Exception as exc:
            logger.warning(
                "[handle_dislike] disliked_genres update failed: %s", exc
            )

    # Acknowledge callback
    msg = f"👎 Got it — I'll recommend fewer movies like <b>{title}</b>."
    if callback_query_id:
        await answer_callback_query(
            callback_query_id, text=f"👎 Noted — fewer like {title}.", show_alert=False
        )
    await send_message(chat_id, msg)

    # Schedule background taste recompute
    _schedule_taste_recompute(chat_id_str)


# ---------------------------------------------------------------------------
# /min_rating  (also matches /rating)
# ---------------------------------------------------------------------------

async def handle_min_rating(
    chat_id: Any,
    input_text: str = "",
    **kwargs,
) -> None:
    """Handle /min_rating <value> or /rating <value>."""
    chat_id_str = str(chat_id)
    text = (input_text or "").strip()

    # Strip command prefix(es)
    for prefix in ("/min_rating ", "/rating ", "min_rating ", "rating "):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    else:
        await send_message(
            chat_id,
            "⭐ <b>Set Minimum Rating</b>\n\n"
            "Usage: <code>/min_rating 7.5</code>\n"
            "Accepts any value from <b>0</b> to <b>10</b>.\n\n"
            "Movies below this rating will be excluded from recommendations.",
        )
        return

    # Parse and validate
    try:
        value = float(text)
    except ValueError:
        await send_message(
            chat_id,
            f"⚠️ <b>{text!r}</b> is not a valid number.\n"
            "Please use a value between 0 and 10, e.g. <code>/min_rating 7.5</code>",
        )
        return

    if not (0.0 <= value <= 10.0):
        await send_message(
            chat_id,
            f"⚠️ Rating must be between <b>0</b> and <b>10</b>. You entered <b>{value}</b>.",
        )
        return

    # Persist
    try:
        user_service.update_min_rating(chat_id_str, value)
    except Exception as exc:
        logger.warning("[handle_min_rating] update_min_rating failed: %s", exc)
        await send_message(
            chat_id,
            "⚠️ Couldn't save your rating preference right now — please try again.",
        )
        return

    await send_message(
        chat_id,
        f"✅ Minimum rating set to <b>{value:.1f}</b> ⭐\n"
        "Future recommendations will only include movies at or above this rating.",
    )
