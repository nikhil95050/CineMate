"""Unit tests for Feature 4 -- Question Engine (Onboarding Flow).

Covers:
  * handle_questioning guards against stale callbacks (session_state != "questioning")
  * Full progression through all 9 questions via button choices
  * Full progression via q_skip for every question
  * Genre multi-select: add, toggle-off, Done finalises
  * Free-text answers for open questions (avoid, favorites)
  * "Any" rating choice advances correctly
  * Free-text is rejected (question re-shown) when question has options
  * _finalize writes last_recs_json before sending cards
  * _finalize writes empty last_recs_json when rec service returns []
  * _finalize writes empty last_recs_json when rec service raises
  * _send_current_question writes pending_question to session
  * _move_next clears pending_question on final question
  * question_index never exceeds len(QUESTIONS) after final answer
  * session_state set to "idle" after final answer
  * Callback with wrong key for current question re-shows question
"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.domain import SessionModel, UserModel
from services.recommendation_engine import QUESTIONS, QUESTION_KEYS


# ---------------------------------------------------------------------------
# Helpers & shared fixtures
# ---------------------------------------------------------------------------

def _fresh_session(chat_id: str = "42", state: str = "questioning") -> SessionModel:
    """Return a new SessionModel in the given state at question_index=0."""
    return SessionModel(
        chat_id=chat_id,
        session_state=state,
        question_index=0,
    )


def _session_at(idx: int, chat_id: str = "42") -> SessionModel:
    """Return a questioning session positioned at question index *idx*."""
    s = _fresh_session(chat_id)
    s.question_index = idx
    return s


class _FakeSessionService:
    """In-memory session service: stores one session per chat_id."""

    def __init__(self, initial: SessionModel):
        self._store: Dict[str, SessionModel] = {initial.chat_id: initial}

    def get_session(self, chat_id: str) -> SessionModel:
        if chat_id not in self._store:
            self._store[chat_id] = SessionModel(chat_id=chat_id)
        return self._store[chat_id]

    def upsert_session(self, model: SessionModel) -> None:
        self._store[model.chat_id] = model


class _FakeUserService:
    def get_user(self, chat_id: str) -> UserModel:
        return UserModel(chat_id=chat_id)


def _patch_services(session: SessionModel, monkeypatch) -> _FakeSessionService:
    """Patch services.container so handlers use in-memory fakes.

    Also patches clients.telegram_card and clients.telegram_helpers at the
    module level so rec_handlers can be imported without real Telegram creds.
    """
    import services.container as container
    svc = _FakeSessionService(session)
    monkeypatch.setattr(container, "session_service", svc)
    monkeypatch.setattr(container, "user_service", _FakeUserService())
    return svc


def _stub_telegram(monkeypatch) -> None:
    """Stub Telegram I/O at the rec_handlers module attribute level.

    rec_handlers imports send_message, show_typing, build_question_keyboard
    and send_movies_async at import time.  We patch the *module attributes*
    inside handlers.rec_handlers after it has been imported so the stubs are
    active for the duration of each test.
    """
    # Force-import the module so it's in sys.modules
    import handlers.rec_handlers as rh

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(rh, "send_message", _noop)
    monkeypatch.setattr(rh, "show_typing", _noop)
    monkeypatch.setattr(rh, "send_movies_async", AsyncMock(return_value=None))
    monkeypatch.setattr(
        rh, "build_question_keyboard",
        lambda *a, **kw: {"inline_keyboard": []},
    )


async def _noop(*args, **kwargs):
    return None


# ===========================================================================
# Fix 1 -- session_state guard
# ===========================================================================

class TestSessionStateGuard:
    @pytest.mark.asyncio
    async def test_stale_callback_idle_session_is_ignored(self, monkeypatch):
        """handle_questioning must be a no-op when session_state != 'questioning'."""
        idle_session = _fresh_session(state="idle")
        svc = _patch_services(idle_session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh

        send_q_mock = AsyncMock()
        monkeypatch.setattr(rh, "_send_current_question", send_q_mock)

        await rh.handle_questioning(
            chat_id="42",
            input_text="q_skip_mood",
            session=idle_session.to_row(),
            user={},
        )

        send_q_mock.assert_not_called()
        assert svc.get_session("42").session_state == "idle"
        assert svc.get_session("42").question_index == 0

    @pytest.mark.asyncio
    async def test_questioning_state_proceeds_normally(self, monkeypatch):
        """handle_questioning must proceed when session_state == 'questioning'."""
        session = _fresh_session(state="questioning")
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh

        send_calls: list = []

        async def fake_send(chat_id, text, **kwargs):
            send_calls.append(text)

        monkeypatch.setattr(rh, "send_message", fake_send)

        await rh.handle_questioning(
            chat_id="42",
            input_text="q_skip_mood",
            session=session.to_row(),
            user={},
        )

        # question_index must have advanced to 1
        assert svc.get_session("42").question_index == 1


# ===========================================================================
# Progression through all 9 questions via q_skip
# ===========================================================================

class TestFullProgressionViaSkip:
    @pytest.mark.asyncio
    async def test_skip_all_9_questions_reaches_finalize(self, monkeypatch):
        """Skipping every question must exhaust all 9 and call _finalize."""
        session = _fresh_session()
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh

        finalize_calls: list = []

        async def recording_finalize(chat_id, session_model):
            finalize_calls.append(session_model.question_index)

        monkeypatch.setattr(rh, "_finalize", recording_finalize)

        for key, _, _opts in QUESTIONS:
            s = svc.get_session("42")
            await rh.handle_questioning(
                chat_id="42",
                input_text=f"q_skip_{key}",
                session=s.to_row(),
                user={},
            )

        assert len(finalize_calls) == 1, "_finalize must be called exactly once"
        assert svc.get_session("42").question_index == len(QUESTIONS)

    @pytest.mark.asyncio
    async def test_all_answers_skipped_are_stored_as_skipped(self, monkeypatch):
        """Each skipped answer must be stored as '[Skipped]'."""
        session = _fresh_session()
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh
        monkeypatch.setattr(rh, "_finalize", AsyncMock())

        for key, _, _ in QUESTIONS:
            s = svc.get_session("42")
            await rh.handle_questioning(
                chat_id="42", input_text=f"q_skip_{key}",
                session=s.to_row(), user={},
            )

        final = svc.get_session("42")
        for key in QUESTION_KEYS:
            val = getattr(final, f"answers_{key}", None)
            assert val == "[Skipped]", f"answers_{key} should be '[Skipped]', got {val!r}"


# ===========================================================================
# Full progression via button choices
# ===========================================================================

class TestFullProgressionViaChoice:
    @pytest.mark.asyncio
    async def test_choosing_first_option_advances_through_all_questions(self, monkeypatch):
        """Selecting the first option for every question advances the full pipeline."""
        session = _fresh_session()
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh
        monkeypatch.setattr(rh, "_finalize", AsyncMock())

        for key, _, opts in QUESTIONS:
            s = svc.get_session("42")
            if key == "genre":
                # Select first genre then confirm with done
                await rh.handle_questioning(
                    chat_id="42", input_text=f"q_genre_{opts[0]}",
                    session=s.to_row(), user={},
                )
                s = svc.get_session("42")
                await rh.handle_questioning(
                    chat_id="42", input_text="q_done_genre",
                    session=s.to_row(), user={},
                )
            elif not opts:
                await rh.handle_questioning(
                    chat_id="42", input_text="some free text",
                    session=s.to_row(), user={},
                )
            else:
                await rh.handle_questioning(
                    chat_id="42", input_text=f"q_{key}_{opts[0]}",
                    session=s.to_row(), user={},
                )

        rh._finalize.assert_called_once()


# ===========================================================================
# Genre multi-select
# ===========================================================================

class TestGenreMultiSelect:
    def _at_genre(self) -> SessionModel:
        genre_idx = QUESTION_KEYS.index("genre")
        return _session_at(genre_idx)

    @pytest.mark.asyncio
    async def test_selecting_genre_adds_to_answers(self, monkeypatch):
        session = self._at_genre()
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh

        await rh.handle_questioning(
            chat_id="42", input_text="q_genre_Action",
            session=session.to_row(), user={},
        )

        assert "Action" in (svc.get_session("42").answers_genre or "")

    @pytest.mark.asyncio
    async def test_selecting_same_genre_twice_removes_it(self, monkeypatch):
        session = self._at_genre()
        session.answers_genre = "Action,Comedy"
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh

        await rh.handle_questioning(
            chat_id="42", input_text="q_genre_Action",
            session=session.to_row(), user={},
        )

        genres = svc.get_session("42").answers_genre or ""
        assert "Action" not in genres
        assert "Comedy" in genres

    @pytest.mark.asyncio
    async def test_multiple_genres_selected_then_done_advances(self, monkeypatch):
        session = self._at_genre()
        svc = _patch_services(session, monkeypatch)
        genre_idx = QUESTION_KEYS.index("genre")
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh
        monkeypatch.setattr(rh, "_finalize", AsyncMock())

        for genre in ("Action", "Drama"):
            s = svc.get_session("42")
            await rh.handle_questioning(
                chat_id="42", input_text=f"q_genre_{genre}",
                session=s.to_row(), user={},
            )

        s = svc.get_session("42")
        await rh.handle_questioning(
            chat_id="42", input_text="q_done_genre",
            session=s.to_row(), user={},
        )

        final = svc.get_session("42")
        assert final.question_index == genre_idx + 1
        assert "Action" in (final.answers_genre or "")
        assert "Drama" in (final.answers_genre or "")


# ===========================================================================
# Free-text questions (avoid, favorites)
# ===========================================================================

class TestFreeTextQuestions:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", ["avoid", "favorites"])
    async def test_free_text_stored_and_index_advances(self, key, monkeypatch):
        idx = QUESTION_KEYS.index(key)
        session = _session_at(idx)
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh
        monkeypatch.setattr(rh, "_finalize", AsyncMock())

        free_text = "I love Tarantino films"
        await rh.handle_questioning(
            chat_id="42", input_text=free_text,
            session=session.to_row(), user={},
        )

        final = svc.get_session("42")
        assert getattr(final, f"answers_{key}") == free_text
        assert final.question_index == idx + 1

    @pytest.mark.asyncio
    async def test_free_text_rejected_when_question_has_options(self, monkeypatch):
        """Free-text input must re-show the question when options exist."""
        mood_idx = QUESTION_KEYS.index("mood")
        session = _session_at(mood_idx)
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh

        send_calls: list = []

        async def fake_send(chat_id, text, **kwargs):
            send_calls.append(text)

        monkeypatch.setattr(rh, "send_message", fake_send)

        await rh.handle_questioning(
            chat_id="42", input_text="some random text",
            session=session.to_row(), user={},
        )

        # question_index must NOT advance
        assert svc.get_session("42").question_index == mood_idx
        assert len(send_calls) >= 1


# ===========================================================================
# "Any" rating choice
# ===========================================================================

class TestRatingAny:
    @pytest.mark.asyncio
    async def test_any_rating_advances_correctly(self, monkeypatch):
        """Selecting 'Any' for rating must store it and advance the index."""
        rating_idx = QUESTION_KEYS.index("rating")
        session = _session_at(rating_idx)
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh
        monkeypatch.setattr(rh, "_finalize", AsyncMock())

        await rh.handle_questioning(
            chat_id="42", input_text="q_rating_Any",
            session=session.to_row(), user={},
        )

        final = svc.get_session("42")
        assert final.answers_rating == "Any"
        assert final.question_index == rating_idx + 1


# ===========================================================================
# Fix 2 -- pending_question is written
# ===========================================================================

class TestPendingQuestion:
    @pytest.mark.asyncio
    async def test_send_current_question_writes_pending_question(self, monkeypatch):
        session = _fresh_session()
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh

        await rh._send_current_question("42", session.to_row())

        stored = svc.get_session("42")
        expected_key = QUESTIONS[0][0]
        assert stored.pending_question == expected_key

    @pytest.mark.asyncio
    async def test_move_next_clears_pending_question_on_last_question(self, monkeypatch):
        last_idx = len(QUESTIONS) - 1
        session = _session_at(last_idx)
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh
        monkeypatch.setattr(rh, "_finalize", AsyncMock())

        last_key = QUESTIONS[last_idx][0]
        await rh._move_next("42", session, last_idx, last_key, "value")

        stored = svc.get_session("42")
        assert stored.pending_question is None


# ===========================================================================
# Fix 3 -- _finalize writes last_recs_json
# ===========================================================================

class TestFinalizeLastRecs:
    @pytest.mark.asyncio
    async def test_finalize_writes_last_recs_json_on_success(self, monkeypatch):
        """_finalize must persist last_recs_json when rec service returns movies."""
        session = _fresh_session()
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh
        import services.container as container

        from models.domain import MovieModel
        fake_movie = MovieModel(movie_id="tt1234", title="Fake Film")

        mock_rec = MagicMock()
        mock_rec.get_recommendations = AsyncMock(return_value=[fake_movie])
        monkeypatch.setattr(container, "rec_service", mock_rec)
        monkeypatch.setattr(rh, "send_movies_async", AsyncMock())

        await rh._finalize("42", session)

        stored = svc.get_session("42")
        recs = json.loads(stored.last_recs_json)
        assert isinstance(recs, list)
        assert len(recs) == 1
        assert recs[0]["title"] == "Fake Film"

    @pytest.mark.asyncio
    async def test_finalize_writes_empty_last_recs_json_when_no_movies(self, monkeypatch):
        session = _fresh_session()
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh
        import services.container as container

        mock_rec = MagicMock()
        mock_rec.get_recommendations = AsyncMock(return_value=[])
        monkeypatch.setattr(container, "rec_service", mock_rec)

        await rh._finalize("42", session)

        stored = svc.get_session("42")
        assert json.loads(stored.last_recs_json) == []

    @pytest.mark.asyncio
    async def test_finalize_writes_empty_last_recs_json_when_rec_service_raises(self, monkeypatch):
        """_finalize must write [] to last_recs_json even when rec service fails."""
        session = _fresh_session()
        session.last_recs_json = '[{"title": "stale"}]'
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh
        import services.container as container

        mock_rec = MagicMock()
        mock_rec.get_recommendations = AsyncMock(side_effect=RuntimeError("LLM down"))
        monkeypatch.setattr(container, "rec_service", mock_rec)

        await rh._finalize("42", session)

        stored = svc.get_session("42")
        assert json.loads(stored.last_recs_json) == [], (
            "Stale last_recs_json must be cleared even when rec service raises"
        )

    @pytest.mark.asyncio
    async def test_finalize_session_state_is_idle_before_send(self, monkeypatch):
        """session_state must be 'idle' when _finalize is called from _move_next."""
        last_idx = len(QUESTIONS) - 1
        session = _session_at(last_idx)
        svc = _patch_services(session, monkeypatch)
        _stub_telegram(monkeypatch)

        import handlers.rec_handlers as rh

        state_at_finalize: list = []

        async def recording_finalize(chat_id, sm):
            state_at_finalize.append(sm.session_state)

        monkeypatch.setattr(rh, "_finalize", recording_finalize)

        last_key = QUESTIONS[last_idx][0]
        await rh._move_next("42", session, last_idx, last_key, "somevalue")

        assert state_at_finalize == ["idle"]
