"""Recommendation and question-engine handlers for CineMate."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict

from clients.telegram_card import send_movies_async
from clients.telegram_helpers import build_question_keyboard, send_message, show_typing
from models import SessionModel, UserModel
from services.container import rec_service, session_service, user_service
from services.recommendation_engine import QUESTIONS

logger = logging.getLogger(__name__)


async def handle_questioning(
    chat_id: Any,
    input_text: str,
    session: Dict[str, Any] | None,
    user: Dict[str, Any] | None,
    **kwargs,
) -> None:
    session_model = session_service.get_session(str(chat_id))

    # -- Fix 1: guard against stale callbacks after the session is reset -----
    # If the session is no longer in the "questioning" state we silently ignore
    # the callback/message.  This prevents re-processing an already-completed
    # questionnaire if Telegram re-delivers a stale update or the user taps an
    # old inline-keyboard button after /start has reset the session.
    if session_model.session_state != "questioning":
        logger.debug(
            "handle_questioning called with session_state=%r for chat_id=%s — ignored",
            session_model.session_state,
            chat_id,
        )
        return

    idx = int(getattr(session_model, "question_index", 0))

    if idx >= len(QUESTIONS):
        await _finalize(chat_id, session_model)
        return

    current_key, _q_text, q_opts = QUESTIONS[idx]

    if input_text.startswith("q_skip_"):
        await _move_next(chat_id, session_model, idx, current_key, "[Skipped]")

    elif input_text.startswith("q_done_"):
        current_value = getattr(session_model, f"answers_{current_key}", "") or ""
        await _move_next(chat_id, session_model, idx, current_key, current_value)

    elif input_text.startswith(f"q_{current_key}_"):
        choice = input_text.replace(f"q_{current_key}_", "", 1)
        if current_key == "genre":
            current_ans = session_model.answers_genre or ""
            selected = [s.strip() for s in current_ans.split(",") if s.strip()]
            if choice in selected:
                selected.remove(choice)
            else:
                selected.append(choice)
            new_ans = ",".join(selected)
            session_model.answers_genre = new_ans
            session_service.upsert_session(session_model)
            await _send_current_question(chat_id, session_model.to_row())
        else:
            await _move_next(chat_id, session_model, idx, current_key, choice)

    else:
        if not q_opts:
            await _move_next(chat_id, session_model, idx, current_key, input_text.strip())
        else:
            await _send_current_question(chat_id, session_model.to_row())


async def _send_current_question(chat_id: Any, session_row: Dict[str, Any]) -> None:
    session_model = SessionModel.from_row(session_row)
    idx = int(getattr(session_model, "question_index", 0))
    if idx >= len(QUESTIONS):
        await _finalize(chat_id, session_model)
        return

    q_key, q_text, q_opts = QUESTIONS[idx]
    markup = build_question_keyboard(
        q_key,
        q_opts,
        selected=(session_model.answers_genre or "").split(",") if q_key == "genre" else [],
        show_skip=True,
        show_done=(q_key == "genre"),
    )
    # Fix 2: pending_question is set to the key of the question currently
    # displayed so that other parts of the system (e.g. analytics, recovery
    # after a crash) can always know which question was in-flight.
    # Previously this field was defined in SessionModel but never written.
    session_model.pending_question = q_key
    session_service.upsert_session(session_model)

    await send_message(
        chat_id,
        f"<b>Step {idx + 1}/{len(QUESTIONS)}</b>\n\n{q_text}",
        reply_markup=markup,
    )


async def _move_next(
    chat_id: Any, session_model: SessionModel, current_idx: int, key: str, value: str
) -> None:
    setattr(session_model, f"answers_{key}", value)
    session_model.question_index = current_idx + 1
    session_service.upsert_session(session_model)

    if session_model.question_index < len(QUESTIONS):
        await _send_current_question(chat_id, session_model.to_row())
    else:
        session_model.session_state = "idle"
        session_model.pending_question = None   # clear pending when done
        session_service.upsert_session(session_model)
        await _finalize(chat_id, session_model)


async def _finalize(chat_id: Any, session_model: SessionModel) -> None:
    """Complete the onboarding flow and send real recommendations.

    Fix 3: If rec_service.get_recommendations succeeds, we serialise the
    returned movie list into session_model.last_recs_json and persist it
    BEFORE sending the cards.  This ensures last_recs_json is never left
    stale — even when the bot crashes mid-send — and allows /more to page
    through the same result set without a second LLM call.

    If the rec service raises an exception or returns an empty list we
    write an empty array to last_recs_json so the field is always valid
    JSON and callers never see a stale previous recommendation.
    """
    await send_message(
        chat_id,
        "\U0001f3ac <b>Reviewing my notes and scanning the archives\u2026 I've got some winners for you!</b>",
    )
    await show_typing(chat_id)

    user_model = user_service.get_user(str(chat_id))

    movies = []
    try:
        movies = await rec_service.get_recommendations(
            session_model, user_model, mode="question_engine", chat_id=str(chat_id)
        )
    except Exception as exc:  # pragma: no cover
        logger.error(
            "_finalize: rec_service.get_recommendations failed for chat_id=%s: %s",
            chat_id, exc,
        )
        movies = []

    # Persist last_recs_json regardless of success/failure so the field is
    # always fresh and never carries stale data from a previous session.
    try:
        serialised = json.dumps(
            [
                m.model_dump() if hasattr(m, "model_dump") else (m.dict() if hasattr(m, "dict") else {})
                for m in movies
            ]
        )
    except Exception:  # pragma: no cover
        serialised = "[]"

    session_model.last_recs_json = serialised
    session_service.upsert_session(session_model)

    if not movies:
        await send_message(
            chat_id,
            "\U0001f615 I couldn't find movies right now \u2014 try /trending or /surprise!",
        )
        return

    await send_movies_async(chat_id, movies)
