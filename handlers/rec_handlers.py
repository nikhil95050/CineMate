"""Handlers for /recommend and follow-up answer steps.

Fixes applied
-------------
#1  Session not reset after recommendations are delivered  →  call
    session_service.reset_session(chat_id) once all recs are sent.
#2  KeyError crash when session row is missing from DB       →  treat
    a None session as a fresh idle session instead of raising.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from clients.telegram_helpers import send_message
from services.container import (
    movie_service,
    recommendation_service,
    session_service,
    user_service,
)

logger = logging.getLogger("rec_handlers")

# Questions asked during the guided recommendation flow
QUESTIONS = [
    ("mood",      "🎭 What's your mood right now? (e.g. happy, sad, tense, romantic…)"),
    ("genre",     "🎬 Any preferred genre? (e.g. Action, Comedy, Thriller — or 'any')"),
    ("language",  "🌐 Preferred language? (e.g. English, Hindi, Telugu — or 'any')"),
    ("era",       "📅 Preferred era? (e.g. 90s, 2000s, recent — or 'any')"),
    ("context",   "🍿 Watching alone, with family, or on a date?"),
    ("time",      "⏱️ How much time do you have? (e.g. under 2 hours, any length)"),
    ("avoid",     "🚫 Anything to avoid? (genres, themes, actors — or 'nothing')"),
    ("favorites", "⭐ Name a movie you loved recently (or 'skip')"),
    ("rating",    "🌟 Minimum IMDb rating? (e.g. 7, 8 — or 'any')"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_get_session(chat_id: str) -> Dict[str, Any]:
    """Return the session dict; create a blank idle one if absent."""
    try:
        sess = session_service.get_session(chat_id)
        if sess is None:
            session_service.reset_session(chat_id)
            return {"session_state": "idle", "question_index": 0}
        return sess
    except Exception as exc:
        logger.warning("[rec_handlers] get_session failed: %s", exc)
        return {"session_state": "idle", "question_index": 0}


def _format_recs(recs: list) -> str:
    if not recs:
        return "😕 No recommendations found right now. Try again with different preferences!"
    lines = ["🎬 <b>Here are your personalised recommendations:</b>\n"]
    for i, r in enumerate(recs, 1):
        title   = r.get("title", "Unknown")
        year    = r.get("year", "")
        rating  = r.get("rating", "")
        genres  = r.get("genres", "")
        lang    = r.get("language", "")
        reason  = r.get("reason", "")
        line = f"{i}. <b>{title}</b>"
        if year:
            line += f" ({year})"
        meta = []
        if rating:
            meta.append(f"⭐ {rating}")
        if genres:
            meta.append(genres)
        if lang:
            meta.append(lang)
        if meta:
            line += "  —  " + " | ".join(meta)
        if reason:
            line += f"\n   <i>{reason}</i>"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# /recommend  (entry point)
# ---------------------------------------------------------------------------

async def handle_recommend(
    chat_id: Any,
    input_text: str = "",
    session: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Start or continue the recommendation question flow."""
    chat_id_str = str(chat_id)

    # Fix #2 — never crash on missing session
    if session is None:
        session = _safe_get_session(chat_id_str)

    state = session.get("session_state", "idle")
    q_idx = int(session.get("question_index", 0))

    # ── Already in answering flow: record answer and advance ──
    if state == "answering" and q_idx < len(QUESTIONS):
        field, _ = QUESTIONS[q_idx]
        answer   = (input_text or "").strip() or "any"

        try:
            session_service.save_answer(chat_id_str, field, answer)
        except Exception as exc:
            logger.warning("[handle_recommend] save_answer failed: %s", exc)

        q_idx += 1

        # More questions to ask?
        if q_idx < len(QUESTIONS):
            _, question_text = QUESTIONS[q_idx]
            try:
                session_service.set_question_index(chat_id_str, q_idx)
            except Exception as exc:
                logger.warning("[handle_recommend] set_question_index failed: %s", exc)
            await send_message(chat_id, question_text)
            return

        # All questions answered → generate recs
        await _finish_and_recommend(chat_id, chat_id_str, session)
        return

    # ── Not in flow yet: start from Q0 ──
    try:
        session_service.start_answering(chat_id_str)
    except Exception as exc:
        logger.warning("[handle_recommend] start_answering failed: %s", exc)

    _, first_question = QUESTIONS[0]
    await send_message(
        chat_id,
        "🎯 Let me personalise your recommendations!\n\n" + first_question,
    )


# ---------------------------------------------------------------------------
# Internal: generate and send recommendations
# ---------------------------------------------------------------------------

async def _finish_and_recommend(
    chat_id: Any,
    chat_id_str: str,
    session: Dict[str, Any],
) -> None:
    """Build preferences dict, call recommendation service, send results."""
    # Gather answers from session
    prefs = {
        "mood":      session.get("answers_mood", ""),
        "genre":     session.get("answers_genre", ""),
        "language":  session.get("answers_language", ""),
        "era":       session.get("answers_era", ""),
        "context":   session.get("answers_context", ""),
        "time":      session.get("answers_time", ""),
        "avoid":     session.get("answers_avoid", ""),
        "favorites": session.get("answers_favorites", ""),
        "rating":    session.get("answers_rating", ""),
    }

    await send_message(chat_id, "⏳ Finding the best movies for you…")

    try:
        user = user_service.get_user(chat_id_str)
        recs = recommendation_service.get_recommendations(
            chat_id=chat_id_str,
            preferences=prefs,
            user=user,
        )
    except Exception as exc:
        logger.error("[_finish_and_recommend] recommendation failed: %s", exc)
        await send_message(
            chat_id,
            "⚠️ Something went wrong while fetching recommendations. Please try /recommend again.",
        )
        # Fix #1 — always reset session, even on error, so user isn't stuck
        try:
            session_service.reset_session(chat_id_str)
        except Exception:
            pass
        return

    msg = _format_recs(recs if isinstance(recs, list) else [])
    await send_message(chat_id, msg)

    # Persist recs to session for feedback handlers to resolve later
    try:
        session_service.save_last_recs(chat_id_str, json.dumps(recs or []))
    except Exception as exc:
        logger.warning("[_finish_and_recommend] save_last_recs failed: %s", exc)

    # Fix #1 — reset session state so /recommend works cleanly next time
    try:
        session_service.reset_session(chat_id_str)
    except Exception as exc:
        logger.warning("[_finish_and_recommend] reset_session failed: %s", exc)
