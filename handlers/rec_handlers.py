"""Question-engine handlers for onboarding and guided recommendations."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from clients.telegram_card import send_movies_async
from clients.telegram_helpers import answer_callback_query, edit_message, send_message, show_typing
from models.domain import QUESTION_COLUMNS, SessionModel
import services.container as container
from services.logging_service import get_logger
from services.recommendation_engine import QUESTIONS, get_next_question

logger = get_logger("rec_handlers")


def _current_question_key(session_model: SessionModel) -> str:
    if 0 <= session_model.question_index < len(QUESTION_COLUMNS):
        return QUESTION_COLUMNS[session_model.question_index].replace("answers_", "")
    return QUESTION_COLUMNS[-1].replace("answers_", "")


def _split_csv(raw: str | None) -> List[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _question_text(question_index: int) -> str:
    _, question, _ = QUESTIONS[question_index]
    return f"<b>Step {question_index + 1}/{len(QUESTIONS)}</b>\n\n{question}"


def build_question_keyboard(session_row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    session_model = SessionModel.from_row(session_row)
    question = get_next_question(session_model.question_index)
    if question is None:
        return None

    key, _, options = question
    rows: List[List[Dict[str, str]]] = []

    if key == "genre":
        selected = set(_split_csv(session_model.answers_genre))
        current_row: List[Dict[str, str]] = []
        for option in options:
            label = f"{'\u2713 ' if option in selected else ''}{option}"
            current_row.append({"text": label, "callback_data": f"q_genre_{option}"})
            if len(current_row) == 2:
                rows.append(current_row)
                current_row = []
        if current_row:
            rows.append(current_row)
        rows.append(
            [
                {"text": "Done", "callback_data": "q_done_genre"},
                {"text": "Skip", "callback_data": "q_skip_genre"},
            ]
        )
        return {"inline_keyboard": rows}

    if options:
        for option in options:
            rows.append([{"text": option, "callback_data": f"q_{key}_{option}"}])
        return {"inline_keyboard": rows}

    return {
        "inline_keyboard": [[{"text": "Skip", "callback_data": f"q_skip_{key}"}]],
    }


async def _send_current_question(
    chat_id: Any,
    session_row: Dict[str, Any],
    callback_query_id: Optional[str] = None,
    message_id: Optional[int] = None,
) -> None:
    session_model = SessionModel.from_row(session_row)
    question = get_next_question(session_model.question_index)
    if question is None:
        return

    session_model.pending_question = question[0]
    container.session_service.upsert_session(session_model)

    text = _question_text(session_model.question_index)
    keyboard = build_question_keyboard(session_model.to_row())

    if callback_query_id:
        try:
            await answer_callback_query(callback_query_id)
        except Exception as exc:
            logger.debug("callback ack failed for %s: %s", chat_id, exc)

    if message_id is not None:
        try:
            await edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=keyboard,
            )
            return
        except Exception as exc:
            logger.debug("question edit failed for %s: %s", chat_id, exc)

    await send_message(chat_id, text, reply_markup=keyboard)


def _normalise_answer(question_key: str, answer: str) -> str:
    cleaned = (answer or "").strip()
    if cleaned.lower() in {"skip", "[skipped]"}:
        return "[Skipped]"
    if question_key == "rating" and cleaned.lower() == "any":
        return "Any"
    return cleaned


def _legacy_option_answer(session_model: SessionModel, input_text: str) -> Optional[str]:
    # ISSUE 11 FIX: the old code did `suffix = input_text[2:]` which took
    # everything after the first two characters of a "q_X" callback, making a
    # subtle assumption about a single-char question key.  Any multi-char key
    # (e.g. "q_era") would leave residual chars in suffix and .isdigit() would
    # return False, silently rejecting valid callbacks.
    #
    # The correct approach: a legacy numeric callback has the form "q_<N>" where
    # N is a 1-based integer with no further underscores.  We parse it via the
    # prefix "q_" and verify that the remainder is a pure digit string.
    if not input_text.startswith("q_"):
        return None
    suffix = input_text[2:]   # everything after "q_"
    if not suffix.isdigit():
        # Not a legacy numeric callback — caller will handle it as unknown.
        return None
    question = get_next_question(session_model.question_index)
    if question is None:
        return None
    _, _, options = question
    if not options:
        return None
    idx = int(suffix) - 1
    if 0 <= idx < len(options):
        return options[idx]
    return None


async def _finalize(chat_id: Any, session_model: SessionModel) -> None:
    chat_id_str = str(chat_id)
    await show_typing(chat_id)
    await send_message(chat_id, "Reviewing my notes and scanning the archives\u2026")

    user_model = container.user_service.get_user(chat_id_str)
    try:
        movies = await container.rec_service.get_recommendations(
            session=session_model,
            user=user_model,
            mode="question_engine",
            chat_id=chat_id_str,
        )
    except Exception as exc:
        logger.warning("question finalize failed for %s: %s", chat_id, exc)
        movies = []

    if movies:
        await send_movies_async(chat_id, movies)
    else:
        await send_message(
            chat_id,
            "\U0001f614 I couldn't find great matches right now.\n\n"
            "Try /surprise or /trending for instant picks, or "
            "/start to adjust your preferences.",
        )


async def _move_next(
    chat_id: Any,
    session_model: SessionModel,
    question_index: int,
    question_key: str,
    answer: str,
    callback_query_id: Optional[str] = None,
    message_id: Optional[int] = None,
) -> None:
    setattr(session_model, f"answers_{question_key}", answer)
    session_model.question_index = question_index + 1

    if session_model.question_index >= len(QUESTIONS):
        session_model.pending_question = None
        session_model.session_state = "idle"
        container.session_service.upsert_session(session_model)
        await _finalize(chat_id, session_model)
        return

    container.session_service.upsert_session(session_model)
    await _send_current_question(
        chat_id=chat_id,
        session_row=session_model.to_row(),
        callback_query_id=callback_query_id,
        message_id=message_id,
    )


async def handle_questioning(
    chat_id: Any,
    input_text: str = "",
    session: Optional[Dict[str, Any]] = None,
    callback_query_id: Optional[str] = None,
    message_id: Optional[int] = None,
    **kwargs,
) -> None:
    chat_id_str = str(chat_id)
    session_model = container.session_service.get_session(chat_id_str)

    if session_model.session_state != "questioning":
        if callback_query_id:
            await answer_callback_query(
                callback_query_id,
                text="That question is no longer active. Send /start to begin again.",
                show_alert=False,
            )
        return

    raw = (input_text or "").strip()
    question = get_next_question(session_model.question_index)
    if question is None:
        session_model.session_state = "idle"
        session_model.pending_question = None
        container.session_service.upsert_session(session_model)
        return

    question_key, _, options = question

    if raw in {f"q_skip_{question_key}", "q_skip"}:
        answer = "[Skipped]"
        if callback_query_id:
            await answer_callback_query(callback_query_id, text="Skipped")
        await _move_next(
            chat_id=chat_id,
            session_model=session_model,
            question_index=session_model.question_index,
            question_key=question_key,
            answer=answer,
            callback_query_id=callback_query_id,
            message_id=message_id,
        )
        return

    if raw == "q_done_genre":
        if question_key != "genre":
            return
        answer = session_model.answers_genre or "[Skipped]"
        await _move_next(
            chat_id=chat_id,
            session_model=session_model,
            question_index=session_model.question_index,
            question_key=question_key,
            answer=answer,
            callback_query_id=callback_query_id,
            message_id=message_id,
        )
        return

    if raw.startswith("q_genre_"):
        option = raw.replace("q_genre_", "", 1).strip()
        selected = _split_csv(session_model.answers_genre)
        if option in selected:
            selected = [item for item in selected if item != option]
            callback_text = f"Removed {option}"
        else:
            selected.append(option)
            callback_text = f"Added {option}"
        session_model.answers_genre = ", ".join(selected)
        container.session_service.upsert_session(session_model)
        if callback_query_id:
            await answer_callback_query(callback_query_id, text=callback_text)
        await _send_current_question(
            chat_id=chat_id,
            session_row=session_model.to_row(),
            callback_query_id=None,
            message_id=message_id,
        )
        return

    if raw.startswith("q_"):
        prefix = f"q_{question_key}_"
        if raw.startswith(prefix):
            answer = raw[len(prefix):].strip()
        else:
            answer = _legacy_option_answer(session_model, raw)
            if answer is None:
                if callback_query_id:
                    await answer_callback_query(
                        callback_query_id,
                        text="That option is not available anymore.",
                        show_alert=False,
                    )
                return
        if callback_query_id:
            await answer_callback_query(callback_query_id)
        answer = _normalise_answer(question_key, answer)
        await _move_next(
            chat_id=chat_id,
            session_model=session_model,
            question_index=session_model.question_index,
            question_key=question_key,
            answer=answer,
            callback_query_id=callback_query_id,
            message_id=message_id,
        )
        return

    if options:
        await _send_current_question(
            chat_id=chat_id,
            session_row=session_model.to_row(),
            callback_query_id=callback_query_id,
            message_id=message_id,
        )
        return

    answer = _normalise_answer(question_key, raw)
    await _move_next(
        chat_id=chat_id,
        session_model=session_model,
        question_index=session_model.question_index,
        question_key=question_key,
        answer=answer,
        callback_query_id=callback_query_id,
        message_id=message_id,
    )


async def handle_recommend(
    chat_id: Any,
    **kwargs,
) -> None:
    session_model = container.session_service.get_session(str(chat_id))
    session_model.session_state = "questioning"
    session_model.question_index = 0
    session_model.pending_question = None
    for col in QUESTION_COLUMNS:
        setattr(session_model, col, None)
    container.session_service.upsert_session(session_model)
    await _send_current_question(chat_id, session_model.to_row())
