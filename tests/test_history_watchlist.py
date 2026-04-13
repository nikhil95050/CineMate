"""Tests for Feature 6 — History & Watchlist.

Covers:
  - HistoryRepository  : CRUD, pagination, mark_watched, cache invalidation
  - WatchlistRepository: CRUD, pagination, is_in_watchlist
  - MovieService       : composition logic, page-count helper, random reminder
  - history_handlers   : /history, /watchlist, pagination edit vs send,
                         handle_watched, handle_save (stale + duplicate)

All Supabase calls are blocked by conftest._isolate_from_supabase so no
live DB is required or touched.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.domain import MovieModel


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    """Return a unique chat_id for each test so live-DB rows never collide."""
    return str(uuid.uuid4())


def _movie(title: str = "Inception", movie_id: str = "tt1375666") -> MovieModel:
    return MovieModel(
        movie_id=movie_id,
        title=title,
        year="2010",
        rating=8.8,
        genres="Action, Sci-Fi",
        language="English",
        reason="Great film",
    )


def _history_row(
    title: str = "Inception",
    movie_id: str = "tt1375666",
    watched: bool = False,
) -> dict:
    return {
        "chat_id": "PLACEHOLDER",  # overwritten by log_recommendations
        "movie_id": movie_id,
        "title": title,
        "year": "2010",
        "genres": "Action, Sci-Fi",
        "language": "English",
        "rating": "8.8",
        "recommended_at": "2026-01-01T00:00:00Z",
        "watched": watched,
        "watched_at": None,
    }


# ===========================================================================
# TABLE name regression guard
# ===========================================================================

class TestHistoryTableName:
    """Prevent TABLE name regressions that break all Supabase queries."""

    def test_history_table_is_history(self):
        from repositories.history_repository import TABLE
        assert TABLE == "history", (
            f"HistoryRepository TABLE must be 'history', got '{TABLE}'. "
            "The Supabase table is named 'history', not 'recommendation_history'."
        )

    def test_watchlist_table_is_watchlist(self):
        from repositories.watchlist_repository import TABLE
        assert TABLE == "watchlist"


# ===========================================================================
# HistoryRepository
# ===========================================================================

class TestHistoryRepository:
    # _isolate_from_supabase (autouse) guarantees sb.is_configured() == False
    # for every method call, so all paths go through _store.

    def _repo(self):
        from repositories.history_repository import HistoryRepository
        return HistoryRepository()

    def test_log_and_retrieve(self):
        repo = self._repo()
        cid = _uid()
        row = _history_row()
        repo.log_recommendations(cid, [row])
        result = repo.get_history(cid, page=1)
        assert len(result) == 1
        assert result[0]["title"] == "Inception"

    def test_pagination_splits_correctly(self):
        repo = self._repo()
        cid = _uid()
        rows = [
            _history_row(title=f"Film {i}", movie_id=f"tt{i:07d}")
            for i in range(25)
        ]
        repo.log_recommendations(cid, rows)
        page1 = repo.get_history(cid, page=1)
        page2 = repo.get_history(cid, page=2)
        page3 = repo.get_history(cid, page=3)
        assert len(page1) == 10
        assert len(page2) == 10
        assert len(page3) == 5

    def test_mark_watched_updates_flag(self):
        repo = self._repo()
        cid = _uid()
        repo.log_recommendations(cid, [_history_row()])
        ok = repo.mark_watched(cid, "tt1375666")
        assert ok is True
        rows = repo.get_history(cid)
        assert rows[0]["watched"] is True
        assert rows[0]["watched_at"] is not None

    def test_get_by_movie_id_found(self):
        repo = self._repo()
        cid = _uid()
        repo.log_recommendations(cid, [_history_row()])
        row = repo.get_by_movie_id(cid, "tt1375666")
        assert row is not None
        assert row["title"] == "Inception"

    def test_get_by_movie_id_missing(self):
        repo = self._repo()
        cid = _uid()
        row = repo.get_by_movie_id(cid, "tt9999999")
        assert row is None

    def test_get_total_count(self):
        repo = self._repo()
        cid = _uid()
        rows = [
            _history_row(title=f"Film {i}", movie_id=f"tt{i:07d}")
            for i in range(7)
        ]
        repo.log_recommendations(cid, rows)
        assert repo.get_total_count(cid) == 7

    def test_upsert_deduplicates_by_movie_id(self):
        repo = self._repo()
        cid = _uid()
        repo.log_recommendations(cid, [_history_row()])
        repo.log_recommendations(cid, [_history_row()])  # same movie_id
        assert repo.get_total_count(cid) == 1


# ===========================================================================
# WatchlistRepository
# ===========================================================================

class TestWatchlistRepository:
    def _repo(self):
        from repositories.watchlist_repository import WatchlistRepository
        return WatchlistRepository()

    def test_add_and_retrieve(self):
        repo = self._repo()
        cid = _uid()
        row = {"movie_id": "tt1375666", "title": "Inception", "year": "2010",
               "language": "English", "rating": "8.8", "genres": "Action"}
        repo.add_to_watchlist(cid, row)
        results = repo.get_watchlist(cid)
        assert len(results) == 1
        assert results[0]["title"] == "Inception"

    def test_pagination(self):
        repo = self._repo()
        cid = _uid()
        for i in range(15):
            repo.add_to_watchlist(
                cid,
                {"movie_id": f"tt{i:07d}", "title": f"Movie {i}",
                 "year": "2020", "language": "English", "rating": "", "genres": ""},
            )
        assert len(repo.get_watchlist(cid, page=1)) == 10
        assert len(repo.get_watchlist(cid, page=2)) == 5

    def test_is_in_watchlist_true(self):
        repo = self._repo()
        cid = _uid()
        repo.add_to_watchlist(
            cid,
            {"movie_id": "tt1375666", "title": "Inception", "year": "2010",
             "language": "English", "rating": "8.8", "genres": ""},
        )
        assert repo.is_in_watchlist(cid, "tt1375666") is True

    def test_is_in_watchlist_false(self):
        repo = self._repo()
        cid = _uid()
        assert repo.is_in_watchlist(cid, "tt9999999") is False

    def test_remove_from_watchlist(self):
        repo = self._repo()
        cid = _uid()
        repo.add_to_watchlist(
            cid,
            {"movie_id": "tt1375666", "title": "Inception", "year": "2010",
             "language": "English", "rating": "8.8", "genres": ""},
        )
        repo.remove_from_watchlist(cid, "tt1375666")
        assert repo.is_in_watchlist(cid, "tt1375666") is False

    def test_upsert_deduplicates(self):
        repo = self._repo()
        cid = _uid()
        for _ in range(3):
            repo.add_to_watchlist(
                cid,
                {"movie_id": "tt1375666", "title": "Inception", "year": "2010",
                 "language": "English", "rating": "8.8", "genres": ""},
            )
        assert repo.get_total_count(cid) == 1


# ===========================================================================
# MovieService
# ===========================================================================

class TestMovieService:
    def _make_svc(self, history_rows=None, watchlist_rows=None, total_h=0, total_w=0):
        history_repo = MagicMock()
        history_repo.get_history.return_value = history_rows or []
        history_repo.get_total_count.return_value = total_h
        history_repo.mark_watched.return_value = True
        history_repo.get_by_movie_id.return_value = (
            history_rows[0] if history_rows else None
        )
        history_repo.log_recommendations.return_value = None

        watchlist_repo = MagicMock()
        watchlist_repo.get_watchlist.return_value = watchlist_rows or []
        watchlist_repo.get_total_count.return_value = total_w
        watchlist_repo.add_to_watchlist.return_value = True
        watchlist_repo.is_in_watchlist.return_value = False

        from services.movie_service import MovieService
        return MovieService(
            history_repo=history_repo, watchlist_repo=watchlist_repo
        )

    def test_get_history_delegates_to_repo(self):
        rows = [_history_row()]
        svc = self._make_svc(history_rows=rows, total_h=1)
        result = svc.get_history("123", page=1)
        assert result == rows
        svc.history_repo.get_history.assert_called_once_with("123", page=1)

    def test_add_to_history_maps_models(self):
        svc = self._make_svc()
        movie = _movie()
        svc.add_to_history("123", [movie])
        svc.history_repo.log_recommendations.assert_called_once()
        args = svc.history_repo.log_recommendations.call_args[0]
        assert args[0] == "123"
        assert args[1][0]["movie_id"] == "tt1375666"

    def test_mark_watched_delegates(self):
        svc = self._make_svc()
        ok = svc.mark_watched("123", "tt1375666")
        assert ok is True
        svc.history_repo.mark_watched.assert_called_once_with("123", "tt1375666")

    def test_get_history_page_count_ceiling(self):
        svc = self._make_svc(total_h=25)
        assert svc.get_history_page_count("123") == 3  # ceil(25/10)

    def test_get_history_page_count_zero_returns_one(self):
        svc = self._make_svc(total_h=0)
        assert svc.get_history_page_count("123") == 1

    def test_add_to_watchlist_uses_model(self):
        svc = self._make_svc()
        movie = _movie()
        ok = svc.add_to_watchlist("123", movie)
        assert ok is True
        svc.watchlist_repo.add_to_watchlist.assert_called_once()

    def test_get_random_watchlist_reminder_returns_item(self):
        rows = [{"movie_id": "tt1375666", "title": "Inception"}]
        svc = self._make_svc(watchlist_rows=rows)
        result = svc.get_random_watchlist_reminder("123")
        assert result == rows[0]

    def test_get_random_watchlist_reminder_empty(self):
        svc = self._make_svc(watchlist_rows=[])
        assert svc.get_random_watchlist_reminder("123") is None

    def test_get_movie_from_history(self):
        rows = [_history_row()]
        svc = self._make_svc(history_rows=rows)
        result = svc.get_movie_from_history("123", "tt1375666")
        assert result == rows[0]


# ===========================================================================
# history_handlers — /history
# ===========================================================================

class TestHandleHistory:
    @pytest.mark.asyncio
    async def test_empty_history_sends_message(self):
        mock_send = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.get_history.return_value = []
        mock_svc.get_history_page_count.return_value = 1

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.edit_message_text", AsyncMock()),
            patch("handlers.history_handlers.answer_callback_query", AsyncMock()),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_history
            await handle_history(chat_id="123", input_text="/history")

        mock_send.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "No recommendations yet" in text

    @pytest.mark.asyncio
    async def test_history_with_rows_shows_list(self):
        mock_send = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.get_history.return_value = [_history_row()]
        mock_svc.get_history_page_count.return_value = 1

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.edit_message_text", AsyncMock()),
            patch("handlers.history_handlers.answer_callback_query", AsyncMock()),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_history
            await handle_history(chat_id="123", input_text="/history")

        text = mock_send.call_args[0][1]
        assert "Inception" in text

    @pytest.mark.asyncio
    async def test_pagination_callback_edits_message(self):
        """history_p2 with message_id and callback_query_id must EDIT, not send."""
        mock_edit = AsyncMock()
        mock_answer = AsyncMock()
        mock_send = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.get_history.return_value = [_history_row()]
        mock_svc.get_history_page_count.return_value = 3

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.edit_message_text", mock_edit),
            patch("handlers.history_handlers.answer_callback_query", mock_answer),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_history
            await handle_history(
                chat_id="123",
                input_text="history_p2",
                message_id=42,
                callback_query_id="cq123",
            )

        mock_edit.assert_called_once()
        mock_answer.assert_called_once()
        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_page_command_sends_new_message(self):
        """Plain /history (no callback_query_id) must send a new message."""
        mock_send = AsyncMock()
        mock_edit = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.get_history.return_value = [_history_row()]
        mock_svc.get_history_page_count.return_value = 2

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.edit_message_text", mock_edit),
            patch("handlers.history_handlers.answer_callback_query", AsyncMock()),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_history
            await handle_history(chat_id="123", input_text="/history")

        mock_send.assert_called_once()
        mock_edit.assert_not_called()


# ===========================================================================
# history_handlers — /watchlist
# ===========================================================================

class TestHandleWatchlist:
    @pytest.mark.asyncio
    async def test_empty_watchlist_sends_friendly_message(self):
        mock_send = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.get_watchlist.return_value = []
        mock_svc.get_watchlist_page_count.return_value = 1

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.edit_message_text", AsyncMock()),
            patch("handlers.history_handlers.answer_callback_query", AsyncMock()),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_watchlist
            await handle_watchlist(chat_id="123", input_text="/watchlist")

        text = mock_send.call_args[0][1]
        assert "Nothing saved yet" in text

    @pytest.mark.asyncio
    async def test_watchlist_pagination_edits_message(self):
        mock_edit = AsyncMock()
        mock_answer = AsyncMock()
        mock_send = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.get_watchlist.return_value = [
            {"movie_id": "tt1375666", "title": "Inception", "year": "2010",
             "rating": "8.8", "added_at": "2026-01-01T00:00:00Z"}
        ]
        mock_svc.get_watchlist_page_count.return_value = 2

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.edit_message_text", mock_edit),
            patch("handlers.history_handlers.answer_callback_query", mock_answer),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_watchlist
            await handle_watchlist(
                chat_id="123",
                input_text="watchlist_p2",
                message_id=99,
                callback_query_id="cq456",
            )

        mock_edit.assert_called_once()
        mock_answer.assert_called_once()
        mock_send.assert_not_called()


# ===========================================================================
# handle_watched
# ===========================================================================

class TestHandleWatched:
    @pytest.mark.asyncio
    async def test_marks_watched_and_sends_ack(self):
        mock_send = AsyncMock()
        mock_answer = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.mark_watched.return_value = True
        mock_svc.get_movie_from_history.return_value = _history_row()

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.answer_callback_query", mock_answer),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_watched
            await handle_watched(
                chat_id="123",
                input_text="watched_tt1375666",
                callback_query_id="cq789",
            )

        mock_svc.mark_watched.assert_called_once_with("123", "tt1375666")
        text = mock_send.call_args[0][1]
        assert "Inception" in text
        assert "\u2714" in text

    @pytest.mark.asyncio
    async def test_empty_movie_id_answers_error(self):
        mock_answer = AsyncMock()
        mock_send = AsyncMock()
        mock_svc = MagicMock()

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.answer_callback_query", mock_answer),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_watched
            await handle_watched(
                chat_id="123",
                input_text="watched_",
                callback_query_id="cq000",
            )

        mock_svc.mark_watched.assert_not_called()
        mock_answer.assert_called_once()

    @pytest.mark.asyncio
    async def test_mark_watched_failure_sends_error_message(self):
        mock_send = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.mark_watched.return_value = False
        mock_svc.get_movie_from_history.return_value = _history_row()

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.answer_callback_query", AsyncMock()),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_watched
            await handle_watched(
                chat_id="123",
                input_text="watched_tt1375666",
            )

        text = mock_send.call_args[0][1]
        assert "Couldn" in text or "couldn" in text


# ===========================================================================
# handle_save
# ===========================================================================

class TestHandleSave:
    def _session_with_rec(self, movie_id: str = "tt1375666") -> dict:
        return {
            "last_recs_json": json.dumps([
                {
                    "movie_id": movie_id,
                    "title": "Inception",
                    "year": "2010",
                    "language": "English",
                    "rating": 8.8,
                    "genres": "Action, Sci-Fi",
                }
            ])
        }

    @pytest.mark.asyncio
    async def test_save_from_last_recs(self):
        mock_send = AsyncMock()
        mock_answer = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.add_to_watchlist.return_value = True
        # Use the service-level method — not watchlist_repo directly
        mock_svc.is_in_watchlist.return_value = False

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.answer_callback_query", mock_answer),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_save
            await handle_save(
                chat_id="123",
                input_text="save_tt1375666",
                callback_query_id="cq111",
                session=self._session_with_rec(),
            )

        mock_svc.add_to_watchlist.assert_called_once()
        text = mock_send.call_args[0][1]
        assert "saved" in text.lower()

    @pytest.mark.asyncio
    async def test_save_stale_movie_sends_friendly_message(self):
        """Movie not in last_recs or history → friendly message, no crash."""
        mock_send = AsyncMock()
        mock_answer = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.get_movie_from_history.return_value = None

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.answer_callback_query", mock_answer),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_save
            await handle_save(
                chat_id="123",
                input_text="save_tt9999999",
                callback_query_id="cq222",
                session={"last_recs_json": "[]"},
            )

        mock_svc.add_to_watchlist.assert_not_called()
        text = mock_send.call_args[0][1]
        assert "couldn" in text.lower() or "cleared" in text.lower()

    @pytest.mark.asyncio
    async def test_save_duplicate_sends_already_saved(self):
        mock_send = AsyncMock()
        mock_answer = AsyncMock()
        mock_svc = MagicMock()
        # Use the service-level method — not watchlist_repo directly
        mock_svc.is_in_watchlist.return_value = True

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.answer_callback_query", mock_answer),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_save
            await handle_save(
                chat_id="123",
                input_text="save_tt1375666",
                callback_query_id="cq333",
                session=self._session_with_rec(),
            )

        mock_svc.add_to_watchlist.assert_not_called()
        text = mock_send.call_args[0][1]
        assert "already" in text.lower()

    @pytest.mark.asyncio
    async def test_save_falls_back_to_history(self):
        """When last_recs is empty, history lookup is used as fallback."""
        mock_send = AsyncMock()
        mock_svc = MagicMock()
        mock_svc.get_movie_from_history.return_value = _history_row()
        mock_svc.add_to_watchlist.return_value = True
        # Use the service-level method — not watchlist_repo directly
        mock_svc.is_in_watchlist.return_value = False

        with (
            patch("handlers.history_handlers.send_message", mock_send),
            patch("handlers.history_handlers.answer_callback_query", AsyncMock()),
            patch("services.container.movie_service", mock_svc),
        ):
            from handlers.history_handlers import handle_save
            await handle_save(
                chat_id="123",
                input_text="save_tt1375666",
                session={"last_recs_json": "[]"},
            )

        mock_svc.get_movie_from_history.assert_called_once_with("123", "tt1375666")
        mock_svc.add_to_watchlist.assert_called_once()
