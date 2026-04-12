"""Recommendation and question-engine handlers for CineMate."""
from __future__ import annotations

from typing import Any, Dict

from clients.telegram_card import send_movies_async
from clients.telegram_helpers import build_question_keyboard, send_message, show_typing
from models import SessionModel, UserModel
from services.container import rec_service, session_service, user_service
from services.recommendation_engine import QUESTIONS


async def handle_questioning(
    chat_id: Any,
    input_text: str,
    session: Dict[str, Any] | None,
    user: Dict[str, Any] | None,
    **kwargs,
) -> None:
    session_model = session_service.get_session(str(chat_id))
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
        session_service.upsert_session(session_model)
        await _finalize(chat_id, session_model)


async def _finalize(chat_id: Any, session_model: SessionModel) -> None:
    """Complete the onboarding flow and send real recommendations."""
    await send_message(
        chat_id,
        "🎬 <b>Reviewing my notes and scanning the archives… I've got some winners for you!</b>",
    )
    await show_typing(chat_id)

    user_model = user_service.get_user(str(chat_id))
    movies = await rec_service.get_recommendations(
        session_model, user_model, mode="question_engine", chat_id=str(chat_id)
    )

    if not movies:
        await send_message(
            chat_id,
            "😕 I couldn't find movies right now — try /trending or /surprise!",
        )
        return

    await send_movies_async(chat_id, movies)
