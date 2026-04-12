"""Tests for Feature 7: feedback logging, taste profile recomputation,
like/dislike handlers, and /min_rating.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Lightweight in-memory stubs (no Supabase, no Redis)
# ---------------------------------------------------------------------------

class _InMemoryFeedbackRepo:
    def __init__(self):
        self._store: Dict[str, Dict[str, Dict]] = {}

    def log_reaction(self, chat_id, movie_id, reaction_type):
        self._store.setdefault(chat_id, {})[movie_id] = {
            "chat_id": chat_id,
            "movie_id": movie_id,
            "reaction_type": reaction_type,
        }

    def get_likes(self, chat_id, limit=50):
        return [
            r for r in self._store.get(chat_id, {}).values()
            if r["reaction_type"] == "like"
        ][:limit]

    def get_dislikes(self, chat_id, limit=50):
        return [
            r for r in self._store.get(chat_id, {}).values()
            if r["reaction_type"] == "dislike"
        ][:limit]

    def get_reaction(self, chat_id, movie_id):
        row = self._store.get(chat_id, {}).get(movie_id)
        return row["reaction_type"] if row else None


class _InMemoryHistoryRepo:
    def __init__(self, rows: List[Dict]):
        # rows: list of dicts with chat_id, movie_id, genres, ...
        self._rows = rows

    def get_by_movie_id(self, chat_id, movie_id):
        for r in self._rows:
            if r["chat_id"] == chat_id and r["movie_id"] == movie_id:
                return r
        return None


class _InMemoryUserRepo:
    def __init__(self):
        self._store: Dict[str, Dict] = {}

    def get_user(self, chat_id):
        return self._store.get(chat_id, {"chat_id": chat_id})

    def upsert_user(self, chat_id, username=None, patch=None):
        row = self._store.get(chat_id, {"chat_id": chat_id})
        if username:
            row["username"] = username
        if patch:
            row.update(patch)
        self._store[chat_id] = row


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user_service(
    history_rows: Optional[List[Dict]] = None,
):
    """Return a UserService wired with in-memory stubs."""
    from services.user_service import UserService
    fb_repo = _InMemoryFeedbackRepo()
    hist_repo = _InMemoryHistoryRepo(history_rows or [])
    user_repo = _InMemoryUserRepo()
    svc = UserService(
        user_repo=user_repo,
        feedback_repo=fb_repo,
        history_repo=hist_repo,
    )
    return svc, fb_repo, hist_repo, user_repo


# ===========================================================================
# 1. FeedbackRepository unit tests
# ===========================================================================

class TestFeedbackRepository:
    def test_log_and_retrieve_like(self):
        repo = _InMemoryFeedbackRepo()
        repo.log_reaction("u1", "tt001", "like")
        assert repo.get_reaction("u1", "tt001") == "like"
        likes = repo.get_likes("u1")
        assert any(r["movie_id"] == "tt001" for r in likes)

    def test_log_and_retrieve_dislike(self):
        repo = _InMemoryFeedbackRepo()
        repo.log_reaction("u1", "tt002", "dislike")
        assert repo.get_reaction("u1", "tt002") == "dislike"
        dislikes = repo.get_dislikes("u1")
        assert any(r["movie_id"] == "tt002" for r in dislikes)

    def test_reaction_flip(self):
        """Like followed by dislike for same movie should overwrite."""
        repo = _InMemoryFeedbackRepo()
        repo.log_reaction("u1", "tt003", "like")
        repo.log_reaction("u1", "tt003", "dislike")
        assert repo.get_reaction("u1", "tt003") == "dislike"
        assert len(repo.get_likes("u1")) == 0

    def test_empty_returns_empty_list(self):
        repo = _InMemoryFeedbackRepo()
        assert repo.get_likes("unknown_user") == []
        assert repo.get_dislikes("unknown_user") == []
        assert repo.get_reaction("unknown_user", "tt000") is None


# ===========================================================================
# 2. UserService.recompute_taste_profile
# ===========================================================================

class TestRecomputeTasteProfile:
    def _history_rows(self):
        return [
            {"chat_id": "u1", "movie_id": "tt001", "title": "Movie A",
             "genres": "Action,Thriller", "rating": "7.5", "year": "2020",
             "language": "English", "watched": False},
            {"chat_id": "u1", "movie_id": "tt002", "title": "Movie B",
             "genres": "Action,Drama", "rating": "8.0", "year": "2019",
             "language": "English", "watched": False},
            {"chat_id": "u1", "movie_id": "tt003", "title": "Movie C",
             "genres": "Drama,Romance", "rating": "7.0", "year": "2021",
             "language": "English", "watched": False},
            {"chat_id": "u1", "movie_id": "tt004", "title": "Movie D",
             "genres": "Action", "rating": "6.5", "year": "2022",
             "language": "English", "watched": False},
        ]

    def test_top_genres_derived_correctly(self):
        svc, fb_repo, hist_repo, user_repo = _make_user_service(self._history_rows())
        # Like tt001 (Action, Thriller), tt002 (Action, Drama), tt004 (Action)
        fb_repo.log_reaction("u1", "tt001", "like")
        fb_repo.log_reaction("u1", "tt002", "like")
        fb_repo.log_reaction("u1", "tt004", "like")

        svc.recompute_taste_profile("u1")

        user_row = user_repo.get_user("u1")
        preferred = user_row.get("preferred_genres", [])
        # Action should be #1 (appears in all 3 liked movies)
        assert "Action" in preferred
        assert preferred[0] == "Action"

    def test_taste_vector_contains_genre_counts(self):
        svc, fb_repo, hist_repo, user_repo = _make_user_service(self._history_rows())
        fb_repo.log_reaction("u1", "tt001", "like")
        fb_repo.log_reaction("u1", "tt002", "like")

        svc.recompute_taste_profile("u1")

        import json
        user_row = user_repo.get_user("u1")
        vector = user_row.get("user_taste_vector") or {}
        if isinstance(vector, str):
            vector = json.loads(vector)
        assert "genre_counts" in vector
        assert vector["genre_counts"].get("Action", 0) >= 2

    def test_empty_feedback_no_crash(self):
        """No feedback → no update, no exception."""
        svc, fb_repo, hist_repo, user_repo = _make_user_service(self._history_rows())
        # No likes logged
        svc.recompute_taste_profile("u1")  # must not raise
        user_row = user_repo.get_user("u1")
        # preferred_genres should remain at default (not set)
        assert user_row.get("preferred_genres") is None or user_row.get("preferred_genres") == []

    def test_missing_history_entries_no_crash(self):
        """Liked movie_ids not in history → graceful skip, no exception."""
        svc, fb_repo, hist_repo, user_repo = _make_user_service([])  # empty history
        fb_repo.log_reaction("u1", "tt999", "like")
        svc.recompute_taste_profile("u1")  # must not raise

    def test_no_feedback_repo_no_crash(self):
        from services.user_service import UserService
        svc = UserService(user_repo=None, feedback_repo=None, history_repo=None)
        svc.recompute_taste_profile("u1")  # must not raise

    def test_liked_count_in_vector(self):
        svc, fb_repo, hist_repo, user_repo = _make_user_service(self._history_rows())
        fb_repo.log_reaction("u1", "tt001", "like")
        fb_repo.log_reaction("u1", "tt002", "like")
        svc.recompute_taste_profile("u1")

        import json
        user_row = user_repo.get_user("u1")
        vector = user_row.get("user_taste_vector") or {}
        if isinstance(vector, str):
            vector = json.loads(vector)
        assert vector.get("liked_count") == 2


# ===========================================================================
# 3. handle_like / handle_dislike callback handlers
# ===========================================================================

class TestLikeDislikeHandlers:
    """Tests use a mocked send_message so no actual Telegram calls are made."""

    def _session_with_recs(self, movie_id: str, genres: str = "Action,Drama") -> Dict:
        import json
        return {
            "last_recs_json": json.dumps([
                {
                    "movie_id": movie_id,
                    "title": "Test Movie",
                    "genres": genres,
                }
            ])
        }

    @pytest.mark.asyncio
    async def test_handle_like_logs_reaction(self):
        from repositories.feedback_repository import FeedbackRepository
        fb_repo = FeedbackRepository()

        logged = []
        original = fb_repo.log_reaction
        def _capture(chat_id, movie_id, reaction_type):
            logged.append((chat_id, movie_id, reaction_type))
        fb_repo.log_reaction = _capture

        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.feedback_handlers.answer_callback_query", new_callable=AsyncMock), \
             patch("services.container.feedback_repo", fb_repo), \
             patch("handlers.feedback_handlers._schedule_taste_recompute"):
            from handlers.feedback_handlers import handle_like
            await handle_like(
                chat_id="u1",
                input_text="like_tt001",
                callback_query_id="cq1",
                session=self._session_with_recs("tt001"),
            )

        assert any(r == ("u1", "tt001", "like") for r in logged)

    @pytest.mark.asyncio
    async def test_handle_dislike_logs_reaction_and_updates_disliked_genres(self):
        from repositories.feedback_repository import FeedbackRepository
        from services.user_service import UserService

        fb_repo = FeedbackRepository()
        user_repo = _InMemoryUserRepo()
        svc = UserService(user_repo=user_repo, feedback_repo=fb_repo, history_repo=None)

        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.feedback_handlers.answer_callback_query", new_callable=AsyncMock), \
             patch("services.container.feedback_repo", fb_repo), \
             patch("handlers.feedback_handlers.user_service", svc), \
             patch("handlers.feedback_handlers._schedule_taste_recompute"):
            from handlers.feedback_handlers import handle_dislike
            await handle_dislike(
                chat_id="u1",
                input_text="dislike_tt002",
                callback_query_id="cq2",
                session=self._session_with_recs("tt002", genres="Horror,Gore"),
            )

        # Reaction logged
        assert fb_repo.get_reaction("u1", "tt002") == "dislike"
        # Genres added to disliked list
        user_row = user_repo.get_user("u1")
        disliked = user_row.get("disliked_genres", [])
        assert "Horror" in disliked or "Gore" in disliked

    @pytest.mark.asyncio
    async def test_handle_like_missing_movie_id_no_crash(self):
        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.feedback_handlers.answer_callback_query", new_callable=AsyncMock):
            from handlers.feedback_handlers import handle_like
            await handle_like(chat_id="u1", input_text="like_")  # empty movie_id

    @pytest.mark.asyncio
    async def test_handle_dislike_missing_movie_id_no_crash(self):
        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.feedback_handlers.answer_callback_query", new_callable=AsyncMock):
            from handlers.feedback_handlers import handle_dislike
            await handle_dislike(chat_id="u1", input_text="dislike_")  # empty movie_id


# ===========================================================================
# 4. handle_min_rating
# ===========================================================================

class TestMinRatingHandler:
    @pytest.mark.asyncio
    async def test_valid_rating_persisted(self):
        from services.user_service import UserService
        user_repo = _InMemoryUserRepo()
        svc = UserService(user_repo=user_repo)

        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock) as mock_send, \
             patch("handlers.feedback_handlers.user_service", svc):
            from handlers.feedback_handlers import handle_min_rating
            await handle_min_rating(chat_id="u1", input_text="/min_rating 7.5")

        user_row = user_repo.get_user("u1")
        assert float(user_row.get("avg_rating_preference", 0)) == 7.5
        assert mock_send.called

    @pytest.mark.asyncio
    async def test_rating_zero_is_valid(self):
        from services.user_service import UserService
        user_repo = _InMemoryUserRepo()
        svc = UserService(user_repo=user_repo)

        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.feedback_handlers.user_service", svc):
            from handlers.feedback_handlers import handle_min_rating
            await handle_min_rating(chat_id="u1", input_text="/min_rating 0")

        assert float(user_repo.get_user("u1").get("avg_rating_preference", -1)) == 0.0

    @pytest.mark.asyncio
    async def test_rating_ten_is_valid(self):
        from services.user_service import UserService
        user_repo = _InMemoryUserRepo()
        svc = UserService(user_repo=user_repo)

        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.feedback_handlers.user_service", svc):
            from handlers.feedback_handlers import handle_min_rating
            await handle_min_rating(chat_id="u1", input_text="/rating 10")

        assert float(user_repo.get_user("u1").get("avg_rating_preference", -1)) == 10.0

    @pytest.mark.asyncio
    async def test_out_of_range_rejected(self):
        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock) as mock_send:
            from handlers.feedback_handlers import handle_min_rating
            await handle_min_rating(chat_id="u1", input_text="/min_rating 11")

        # Should send an error message, not crash
        assert mock_send.called
        call_text = mock_send.call_args[0][1] if mock_send.call_args else ""
        assert "must be between" in call_text or "⚠️" in call_text

    @pytest.mark.asyncio
    async def test_non_numeric_rejected(self):
        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock) as mock_send:
            from handlers.feedback_handlers import handle_min_rating
            await handle_min_rating(chat_id="u1", input_text="/min_rating abc")

        assert mock_send.called

    @pytest.mark.asyncio
    async def test_no_argument_shows_usage(self):
        with patch("handlers.feedback_handlers.send_message", new_callable=AsyncMock) as mock_send:
            from handlers.feedback_handlers import handle_min_rating
            await handle_min_rating(chat_id="u1", input_text="/min_rating")

        assert mock_send.called
        call_text = mock_send.call_args[0][1] if mock_send.call_args else ""
        assert "Usage" in call_text or "0" in call_text


# ===========================================================================
# 5. RecommendationService respects avg_rating_preference
# ===========================================================================

class TestRecommendationRatingFilter:
    def test_min_rating_from_user_profile_applied(self):
        """_resolve_min_rating returns user.avg_rating_preference when
        session.answers_rating is empty.
        """
        from services.recommendation_service import RecommendationService
        from models.domain import SessionModel, UserModel

        svc = RecommendationService()
        session = SessionModel(chat_id="u1")  # no answers_rating
        user = UserModel(chat_id="u1", avg_rating_preference=8.0)
        result = svc._resolve_min_rating(session, user)
        assert result == 8.0

    def test_session_rating_overrides_user_profile(self):
        from services.recommendation_service import RecommendationService
        from models.domain import SessionModel, UserModel

        svc = RecommendationService()
        session = SessionModel(chat_id="u1", answers_rating="9+")
        user = UserModel(chat_id="u1", avg_rating_preference=6.0)
        result = svc._resolve_min_rating(session, user)
        assert result == 9.0

    def test_no_rating_returns_none(self):
        from services.recommendation_service import RecommendationService
        from models.domain import SessionModel, UserModel

        svc = RecommendationService()
        session = SessionModel(chat_id="u1")
        user = UserModel(chat_id="u1")
        result = svc._resolve_min_rating(session, user)
        assert result is None
