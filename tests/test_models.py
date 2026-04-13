"""Comprehensive tests for models/domain.py — MovieModel, UserModel, SessionModel,
StreamingInfo — plus service-level tests for MovieService, UserService,
SessionService, and config.app_config.get_startup_readiness().

Coverage targets (per spec):
  - Required-field validation (ValidationError raised when required fields absent)
  - Default values for optional fields
  - dict-to-model conversion (from_row / from_history_row)
  - model-to-dict conversion (to_row / to_history_row / to_watchlist_row)
  - JSONB string coercion validators
  - StreamingInfo structured fields and from_display_string helper
  - Service-level usage: MovieService, UserService, SessionService
  - get_startup_readiness() env-key checks
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from models.domain import MovieModel, SessionModel, StreamingInfo, UserModel


# ===========================================================================
# StreamingInfo
# ===========================================================================

class TestStreamingInfo:
    def test_empty_by_default(self):
        si = StreamingInfo()
        assert si.platforms == []
        assert si.rent == []
        assert si.buy == []
        assert si.display == ""
        assert not si.is_available

    def test_from_display_string_parses_platforms(self):
        si = StreamingInfo.from_display_string("Netflix, Prime Video, Disney+")
        assert "Netflix" in si.platforms
        assert "Prime Video" in si.platforms
        assert "Disney+" in si.platforms
        assert si.is_available

    def test_from_display_string_none_returns_empty(self):
        si = StreamingInfo.from_display_string(None)
        assert si.platforms == []
        assert not si.is_available

    def test_from_display_string_blank_returns_empty(self):
        si = StreamingInfo.from_display_string("   ")
        assert not si.is_available

    def test_from_display_string_strips_na_values(self):
        si = StreamingInfo.from_display_string("N/A")
        assert si.platforms == []
        assert not si.is_available

    def test_from_display_string_semicolon_separator(self):
        si = StreamingInfo.from_display_string("Netflix; Hulu")
        assert "Netflix" in si.platforms
        assert "Hulu" in si.platforms

    def test_to_display_uses_display_field_first(self):
        si = StreamingInfo(display="Netflix, Hulu", platforms=["Netflix", "Hulu"])
        assert si.to_display() == "Netflix, Hulu"

    def test_to_display_falls_back_to_platforms(self):
        si = StreamingInfo(platforms=["Netflix", "Hulu"])
        result = si.to_display()
        assert "Netflix" in result
        assert "Hulu" in result

    def test_to_display_empty_returns_not_available(self):
        assert StreamingInfo().to_display() == "Not available"

    def test_is_available_with_rent(self):
        si = StreamingInfo(rent=["Apple TV"])
        assert si.is_available

    def test_is_available_with_buy(self):
        si = StreamingInfo(buy=["Amazon"])
        assert si.is_available

    def test_coerce_list_from_json_string(self):
        si = StreamingInfo(platforms='["Netflix","Hulu"]')
        assert "Netflix" in si.platforms

    def test_full_structured_data(self):
        si = StreamingInfo(
            display="Netflix (stream), Amazon (rent/buy)",
            platforms=["Netflix"],
            rent=["Amazon"],
            buy=["Amazon"],
        )
        assert si.is_available
        assert "Netflix" in si.platforms
        assert "Amazon" in si.rent


# ===========================================================================
# MovieModel
# ===========================================================================

class TestMovieModel:
    # --- Required fields ---

    def test_required_movie_id_missing_raises(self):
        with pytest.raises(ValidationError):
            MovieModel(title="Inception")  # movie_id missing

    def test_required_title_missing_raises(self):
        with pytest.raises(ValidationError):
            MovieModel(movie_id="tt1375666")  # title missing

    # --- Default values ---

    def test_defaults_all_optional_fields(self):
        m = MovieModel(movie_id="tt0000001", title="Test Movie")
        assert m.year is None
        assert m.rating is None
        assert m.genres is None
        assert m.language == "English"
        assert m.description is None
        assert m.poster is None
        assert m.trailer is None
        assert m.streaming is None
        assert isinstance(m.streaming_info, StreamingInfo)
        assert not m.streaming_info.is_available
        assert m.reason is None

    # --- genre_list property ---

    def test_genre_list_splits_correctly(self):
        m = MovieModel(movie_id="tt1", title="X", genres="Action, Sci-Fi, Drama")
        assert m.genre_list == ["Action", "Sci-Fi", "Drama"]

    def test_genre_list_empty_when_genres_none(self):
        m = MovieModel(movie_id="tt1", title="X")
        assert m.genre_list == []

    # --- streaming_info auto-sync ---

    def test_streaming_info_derived_from_streaming_string(self):
        m = MovieModel(
            movie_id="tt1", title="X", streaming="Netflix, Prime Video"
        )
        assert "Netflix" in m.streaming_info.platforms
        assert "Prime Video" in m.streaming_info.platforms
        assert m.streaming_info.is_available

    def test_streaming_info_explicit_overrides_string(self):
        si = StreamingInfo(platforms=["Hulu"], rent=["Apple TV"])
        m = MovieModel(movie_id="tt1", title="X", streaming_info=si)
        assert "Hulu" in m.streaming_info.platforms

    def test_streaming_info_accepts_dict(self):
        m = MovieModel(
            movie_id="tt1",
            title="X",
            streaming_info={"platforms": ["Netflix"], "rent": [], "buy": []},
        )
        assert "Netflix" in m.streaming_info.platforms

    def test_streaming_info_none_gives_empty(self):
        m = MovieModel(movie_id="tt1", title="X", streaming_info=None)
        assert isinstance(m.streaming_info, StreamingInfo)
        assert not m.streaming_info.is_available

    # --- from_history_row ---

    def test_from_history_row_full(self):
        row = {
            "movie_id": "tt1375666",
            "title": "Inception",
            "year": "2010",
            "genres": "Action, Sci-Fi",
            "language": "English",
            "rating": "8.8",
            "streaming": "Netflix, Prime Video",
        }
        m = MovieModel.from_history_row(row)
        assert m.movie_id == "tt1375666"
        assert m.title == "Inception"
        assert m.year == "2010"
        assert m.rating == pytest.approx(8.8)
        assert "Action" in m.genre_list
        assert m.streaming == "Netflix, Prime Video"
        assert "Netflix" in m.streaming_info.platforms

    def test_from_history_row_missing_optional_fields(self):
        row = {"movie_id": "tt1", "title": "Minimal"}
        m = MovieModel.from_history_row(row)
        assert m.rating is None
        assert m.genres is None
        assert not m.streaming_info.is_available

    def test_from_history_row_bad_rating_becomes_none(self):
        row = {"movie_id": "tt1", "title": "X", "rating": "not-a-number"}
        m = MovieModel.from_history_row(row)
        assert m.rating is None

    def test_from_history_row_integer_movie_id_coerced_to_str(self):
        row = {"movie_id": 42, "title": "X"}
        m = MovieModel.from_history_row(row)
        assert m.movie_id == "42"

    # --- to_history_row ---

    def test_to_history_row_shape(self):
        m = MovieModel(
            movie_id="tt1375666",
            title="Inception",
            year="2010",
            rating=8.8,
            genres="Action, Sci-Fi",
            language="English",
        )
        row = m.to_history_row(chat_id="123")
        assert row["chat_id"] == "123"
        assert row["movie_id"] == "tt1375666"
        assert row["title"] == "Inception"
        assert row["year"] == "2010"
        assert row["rating"] == "8.8"
        assert "Action" in row["genres"]

    def test_to_history_row_none_rating_becomes_empty_string(self):
        m = MovieModel(movie_id="tt1", title="X")
        row = m.to_history_row(chat_id="1")
        assert row["rating"] == ""

    # --- to_watchlist_row ---

    def test_to_watchlist_row_shape(self):
        m = MovieModel(
            movie_id="tt1",
            title="Dune",
            year="2021",
            rating=7.9,
            genres="Sci-Fi",
            language="English",
        )
        row = m.to_watchlist_row(chat_id="999")
        assert row["chat_id"] == "999"
        assert row["movie_id"] == "tt1"
        assert row["title"] == "Dune"
        assert row["language"] == "English"

    # --- model_dump round-trip ---

    def test_model_dump_and_reconstruct(self):
        m = MovieModel(
            movie_id="tt1",
            title="Dune",
            year="2021",
            rating=7.9,
            genres="Sci-Fi, Adventure",
            streaming="Netflix",
        )
        d = m.model_dump()
        assert d["movie_id"] == "tt1"
        assert isinstance(d["streaming_info"], dict)
        # Reconstruct
        m2 = MovieModel(**d)
        assert m2.title == "Dune"
        assert m2.streaming_info.is_available


# ===========================================================================
# UserModel
# ===========================================================================

class TestUserModel:
    # --- Required fields ---

    def test_required_chat_id_missing_raises(self):
        with pytest.raises(ValidationError):
            UserModel()  # chat_id missing

    # --- Defaults ---

    def test_defaults(self):
        u = UserModel(chat_id="1")
        assert u.username == "User"
        assert u.preferred_genres == []
        assert u.disliked_genres == []
        assert u.preferred_language is None
        assert u.preferred_era is None
        assert u.watch_context is None
        assert u.avg_rating_preference is None
        assert u.subscriptions == []
        assert u.user_taste_vector is None

    # --- JSONB string coercion ---

    def test_preferred_genres_coerced_from_json_string(self):
        u = UserModel(chat_id="1", preferred_genres='["Action","Drama"]')
        assert "Action" in u.preferred_genres

    def test_disliked_genres_coerced_from_comma_string(self):
        u = UserModel(chat_id="1", disliked_genres="Horror, Thriller")
        assert "Horror" in u.disliked_genres

    def test_subscriptions_coerced_from_json_string(self):
        u = UserModel(chat_id="1", subscriptions='["Netflix","Hulu"]')
        assert "Netflix" in u.subscriptions

    def test_user_taste_vector_coerced_from_json_string(self):
        u = UserModel(
            chat_id="1",
            user_taste_vector='{"top_genres": ["Action"]}',
        )
        assert u.user_taste_vector == {"top_genres": ["Action"]}

    def test_user_taste_vector_invalid_json_becomes_none(self):
        u = UserModel(chat_id="1", user_taste_vector="not-json")
        assert u.user_taste_vector is None

    # --- from_row ---

    def test_from_row_full(self):
        row = {
            "chat_id": "42",
            "username": "nikhil",
            "preferred_genres": ["Sci-Fi", "Drama"],
            "disliked_genres": ["Horror"],
            "preferred_language": "English",
            "preferred_era": "Modern",
            "watch_context": "Alone",
            "avg_rating_preference": 7.5,
            "subscriptions": ["Netflix", "Prime Video"],
            "user_taste_vector": {"top_actors": ["DiCaprio"]},
        }
        u = UserModel.from_row(row)
        assert u.chat_id == "42"
        assert u.username == "nikhil"
        assert "Sci-Fi" in u.preferred_genres
        assert u.avg_rating_preference == pytest.approx(7.5)
        assert u.user_taste_vector == {"top_actors": ["DiCaprio"]}

    def test_from_row_avg_rating_string_coerced(self):
        row = {"chat_id": "1", "avg_rating_preference": "6.5"}
        u = UserModel.from_row(row)
        assert u.avg_rating_preference == pytest.approx(6.5)

    def test_from_row_bad_avg_rating_becomes_none(self):
        row = {"chat_id": "1", "avg_rating_preference": "bad"}
        u = UserModel.from_row(row)
        assert u.avg_rating_preference is None

    def test_from_row_chat_id_coerced_to_str(self):
        row = {"chat_id": 99}
        u = UserModel.from_row(row)
        assert u.chat_id == "99"

    def test_from_row_missing_username_defaults_to_user(self):
        row = {"chat_id": "1"}
        u = UserModel.from_row(row)
        assert u.username == "User"

    # --- to_row ---

    def test_to_row_round_trip(self):
        row_in = {
            "chat_id": "42",
            "username": "nikhil",
            "preferred_genres": ["Sci-Fi", "Drama"],
            "disliked_genres": ["Horror"],
            "preferred_language": "English",
            "preferred_era": "Modern",
            "watch_context": "Alone",
            "avg_rating_preference": 7.5,
            "subscriptions": ["Netflix"],
            "user_taste_vector": {"top_genres": ["Sci-Fi"]},
        }
        u = UserModel.from_row(row_in)
        row_out = u.to_row()
        assert row_out["chat_id"] == "42"
        assert "Sci-Fi" in row_out["preferred_genres"]
        assert row_out["avg_rating_preference"] == pytest.approx(7.5)


# ===========================================================================
# SessionModel
# ===========================================================================

class TestSessionModel:
    # --- Required fields ---

    def test_required_chat_id_missing_raises(self):
        with pytest.raises(ValidationError):
            SessionModel()  # chat_id missing

    # --- Defaults ---

    def test_defaults(self):
        s = SessionModel(chat_id="1")
        assert s.session_state == "idle"
        assert s.question_index == 0
        assert s.last_recs_json == "[]"
        assert s.overflow_buffer_json == "[]"
        assert s.sim_depth == 0
        for key in [
            "answers_mood", "answers_genre", "answers_language",
            "answers_era", "answers_context", "answers_time",
            "answers_avoid", "answers_favorites", "answers_rating",
        ]:
            assert getattr(s, key) is None

    # --- from_row ---

    def test_from_row_full(self):
        row = {
            "chat_id": "99",
            "session_state": "questioning",
            "question_index": 3,
            "answers_mood": "Happy",
            "answers_genre": "Action, Sci-Fi",
            "answers_language": "English",
            "answers_era": "Modern",
            "answers_context": "Alone",
            "answers_time": "2h",
            "answers_avoid": "Horror",
            "answers_favorites": "Inception",
            "answers_rating": "7",
            "last_recs_json": "[]",
            "overflow_buffer_json": "[]",
            "sim_depth": 1,
        }
        s = SessionModel.from_row(row)
        assert s.chat_id == "99"
        assert s.session_state == "questioning"
        assert s.question_index == 3
        assert s.answers_mood == "Happy"
        assert s.answers_genre == "Action, Sci-Fi"
        assert s.sim_depth == 1

    def test_from_row_question_index_string_coerced(self):
        s = SessionModel.from_row({"chat_id": "1", "question_index": "5"})
        assert s.question_index == 5

    def test_from_row_missing_state_defaults_to_idle(self):
        s = SessionModel.from_row({"chat_id": "1"})
        assert s.session_state == "idle"

    def test_from_row_empty_strings_become_none(self):
        row = {"chat_id": "1", "answers_mood": "", "answers_genre": ""}
        s = SessionModel.from_row(row)
        assert s.answers_mood is None
        assert s.answers_genre is None

    # --- to_row ---

    def test_to_row_round_trip(self):
        s = SessionModel(
            chat_id="99",
            session_state="questioning",
            question_index=3,
            answers_mood="Happy",
            last_recs_json="[]",
            sim_depth=1,
        )
        row = s.to_row()
        assert row["chat_id"] == "99"
        assert row["session_state"] == "questioning"
        assert row["question_index"] == 3
        assert row["answers_mood"] == "Happy"
        assert "updated_at" in row

    def test_to_row_none_answers_become_empty_string(self):
        s = SessionModel(chat_id="1")
        row = s.to_row()
        for key in [
            "answers_mood", "answers_genre", "answers_language",
            "answers_era", "answers_context", "answers_time",
            "answers_avoid", "answers_favorites", "answers_rating",
        ]:
            assert row[key] == ""

    def test_to_row_sim_depth_is_int(self):
        s = SessionModel(chat_id="1", sim_depth=2)
        row = s.to_row()
        assert isinstance(row["sim_depth"], int)
        assert row["sim_depth"] == 2


# ===========================================================================
# Service-level tests — MovieService
# ===========================================================================

class TestMovieServiceWithModels:
    """Verify MovieService correctly uses MovieModel types throughout."""

    def _make_service(self, history_rows=None, watchlist_rows=None, total=0):
        from services.movie_service import MovieService
        history_repo = MagicMock()
        history_repo.get_history.return_value = history_rows or []
        history_repo.get_total_count.return_value = total
        history_repo.get_by_movie_id.return_value = (
            history_rows[0] if history_rows else None
        )
        watchlist_repo = MagicMock()
        watchlist_repo.get_watchlist.return_value = watchlist_rows or []
        watchlist_repo.get_total_count.return_value = total
        watchlist_repo.is_in_watchlist = MagicMock(return_value=False)
        watchlist_repo.add_to_watchlist = MagicMock(return_value=True)
        return MovieService(history_repo=history_repo, watchlist_repo=watchlist_repo)

    def test_add_to_history_calls_repo(self):
        svc = self._make_service()
        movie = MovieModel(movie_id="tt1", title="Inception")
        svc.add_to_history("123", [movie])
        svc.history_repo.log_recommendations.assert_called_once()

    def test_add_to_history_no_repo_is_noop(self):
        from services.movie_service import MovieService
        svc = MovieService()
        # Should not raise
        svc.add_to_history("1", [MovieModel(movie_id="tt1", title="X")])

    def test_get_movie_from_history_returns_movie_model(self):
        rows = [{"movie_id": "tt1", "title": "Inception", "year": "2010", "rating": "8.8"}]
        svc = self._make_service(history_rows=rows)
        result = svc.get_movie_from_history("123", "tt1")
        assert isinstance(result, MovieModel)
        assert result.title == "Inception"

    def test_get_movie_from_history_no_repo_returns_none(self):
        from services.movie_service import MovieService
        svc = MovieService()
        assert svc.get_movie_from_history("1", "tt1") is None

    def test_add_to_watchlist_uses_to_watchlist_row(self):
        svc = self._make_service()
        movie = MovieModel(
            movie_id="tt1", title="Dune", year="2021", rating=7.9, genres="Sci-Fi"
        )
        svc.add_to_watchlist("123", movie)
        svc.watchlist_repo.add_to_watchlist.assert_called_once()
        call_args = svc.watchlist_repo.add_to_watchlist.call_args
        row_passed = call_args[0][1]  # second positional arg is the row dict
        assert row_passed["title"] == "Dune"
        assert row_passed["movie_id"] == "tt1"

    def test_get_history_page_count_ceiling_division(self):
        svc = self._make_service(total=11)  # 11 items, PAGE_SIZE=10 → 2 pages
        count = svc.get_history_page_count("1")
        assert count == 2

    def test_get_history_page_count_exact_page(self):
        svc = self._make_service(total=10)  # exactly 10 → 1 page
        count = svc.get_history_page_count("1")
        assert count == 1

    def test_get_history_page_count_zero_returns_one(self):
        svc = self._make_service(total=0)
        count = svc.get_history_page_count("1")
        assert count == 1

    def test_get_watchlist_page_count(self):
        svc = self._make_service(total=25)  # 25 items → 3 pages
        count = svc.get_watchlist_page_count("1")
        assert count == 3


# ===========================================================================
# Service-level tests — UserService
# ===========================================================================

class TestUserServiceWithModels:
    def _make_service(self, user_row=None):
        from services.user_service import UserService
        user_repo = MagicMock()
        user_repo.get_user.return_value = user_row or {"chat_id": "1", "username": "User"}
        user_repo.upsert_user = MagicMock()
        return UserService(user_repo=user_repo)

    def test_get_user_returns_user_model(self):
        svc = self._make_service(user_row={"chat_id": "42", "username": "nikhil"})
        u = svc.get_user("42")
        assert isinstance(u, UserModel)
        assert u.chat_id == "42"
        assert u.username == "nikhil"

    def test_get_user_no_repo_returns_default(self):
        from services.user_service import UserService
        svc = UserService()
        u = svc.get_user("1")
        assert isinstance(u, UserModel)
        assert u.chat_id == "1"

    def test_upsert_user_calls_repo(self):
        svc = self._make_service()
        u = UserModel(chat_id="1", username="nikhil", preferred_genres=["Action"])
        svc.upsert_user(u)
        svc.user_repo.upsert_user.assert_called_once()

    def test_update_min_rating_valid_value(self):
        svc = self._make_service()
        svc.update_min_rating("1", 7.0)
        svc.user_repo.upsert_user.assert_called_once()

    def test_update_min_rating_zero_is_valid(self):
        # Regression: 0.0 is falsy but must still be persisted
        svc = self._make_service()
        svc.update_min_rating("1", 0.0)
        svc.user_repo.upsert_user.assert_called_once()

    def test_update_min_rating_out_of_range_raises(self):
        svc = self._make_service()
        with pytest.raises(ValueError, match="\\[0, 10\\]"):
            svc.update_min_rating("1", 11.0)

    def test_update_min_rating_negative_raises(self):
        svc = self._make_service()
        with pytest.raises(ValueError):
            svc.update_min_rating("1", -1.0)

    def test_update_min_rating_non_numeric_raises(self):
        svc = self._make_service()
        with pytest.raises(ValueError):
            svc.update_min_rating("1", "bad")

    def test_recompute_taste_profile_no_feedback_repo_noop(self):
        from services.user_service import UserService
        svc = UserService(user_repo=MagicMock())
        # Should not raise
        svc.recompute_taste_profile("1")

    def test_recompute_taste_profile_empty_likes_noop(self):
        from services.user_service import UserService
        fb_repo = MagicMock()
        fb_repo.get_likes.return_value = []
        svc = UserService(user_repo=MagicMock(), feedback_repo=fb_repo)
        svc.recompute_taste_profile("1")  # should not raise or write
        svc.user_repo.upsert_user.assert_not_called()

    def test_recompute_taste_profile_derives_top_genres(self):
        from services.user_service import UserService
        user_repo = MagicMock()
        user_repo.get_user.return_value = {"chat_id": "1", "username": "User"}
        user_repo.upsert_user = MagicMock()

        fb_repo = MagicMock()
        fb_repo.get_likes.return_value = [
            {"movie_id": "tt1"},
            {"movie_id": "tt2"},
            {"movie_id": "tt3"},
        ]

        history_repo = MagicMock()
        history_repo.get_by_movie_id.side_effect = [
            {"movie_id": "tt1", "genres": "Action, Sci-Fi"},
            {"movie_id": "tt2", "genres": "Action, Drama"},
            {"movie_id": "tt3", "genres": "Sci-Fi"},
        ]

        svc = UserService(
            user_repo=user_repo,
            feedback_repo=fb_repo,
            history_repo=history_repo,
        )
        svc.recompute_taste_profile("1")
        user_repo.upsert_user.assert_called_once()
        call_kwargs = user_repo.upsert_user.call_args[1]
        patch = call_kwargs.get("patch", {})
        top_genres = patch.get("preferred_genres", [])
        # Action and Sci-Fi should appear in top genres
        assert "Action" in top_genres
        assert "Sci-Fi" in top_genres


# ===========================================================================
# Service-level tests — SessionService
# ===========================================================================

class TestSessionServiceWithModels:
    def _make_service(self, session_row=None):
        from services.movie_service import SessionService
        session_repo = MagicMock()
        session_repo.get_session.return_value = session_row or {"chat_id": "1"}
        session_repo.upsert_session = MagicMock()
        return SessionService(session_repo=session_repo)

    def test_get_session_returns_session_model(self):
        svc = self._make_service(
            session_row={"chat_id": "99", "session_state": "questioning", "question_index": 2}
        )
        s = svc.get_session("99")
        assert isinstance(s, SessionModel)
        assert s.chat_id == "99"
        assert s.session_state == "questioning"
        assert s.question_index == 2

    def test_get_session_no_repo_returns_default(self):
        from services.movie_service import SessionService
        svc = SessionService()
        s = svc.get_session("1")
        assert isinstance(s, SessionModel)
        assert s.session_state == "idle"

    def test_upsert_session_calls_repo(self):
        svc = self._make_service()
        s = SessionModel(chat_id="1", session_state="questioning", question_index=1)
        svc.upsert_session(s)
        svc.session_repo.upsert_session.assert_called_once()
        call_args = svc.session_repo.upsert_session.call_args
        assert call_args[0][0] == "1"  # chat_id first arg

    def test_reset_session_clears_state(self):
        svc = self._make_service(
            session_row={"chat_id": "1", "session_state": "questioning", "question_index": 5}
        )
        fresh = svc.reset_session("1")
        assert isinstance(fresh, SessionModel)
        assert fresh.session_state == "idle"
        assert fresh.question_index == 0


# ===========================================================================
# config.app_config — get_startup_readiness()
# ===========================================================================

class TestGetStartupReadiness:
    """Verify get_startup_readiness() correctly reflects env vars."""

    def test_all_keys_present_all_true(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "tok_abc",
            "PERPLEXITY_API_KEY": "pplx_abc",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "key_abc",
            "REDIS_URL": "redis://localhost:6379",
        }
        with patch.dict(os.environ, env, clear=False):
            from config.app_config import get_startup_readiness
            result = get_startup_readiness()
        assert result["telegram_bot_token"] is True
        assert result["perplexity_api_key"] is True
        assert result["supabase_url"] is True
        assert result["supabase_service_key"] is True
        assert result["redis_url"] is True

    def test_missing_telegram_token_is_false(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": ""}, clear=False):
            from config.app_config import get_startup_readiness
            result = get_startup_readiness()
        assert result["telegram_bot_token"] is False

    def test_missing_perplexity_key_is_false(self):
        with patch.dict(os.environ, {"PERPLEXITY_API_KEY": ""}, clear=False):
            from config.app_config import get_startup_readiness
            result = get_startup_readiness()
        assert result["perplexity_api_key"] is False

    def test_missing_supabase_url_is_false(self):
        env = {"SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": "", "SUPABASE_API_KEY": ""}
        with patch.dict(os.environ, env, clear=False):
            from config.app_config import get_startup_readiness
            result = get_startup_readiness()
        assert result["supabase_url"] is False

    def test_supabase_api_key_fallback(self):
        """SUPABASE_API_KEY should be accepted when SERVICE_ROLE_KEY is absent."""
        env = {
            "SUPABASE_SERVICE_ROLE_KEY": "",
            "SUPABASE_API_KEY": "fallback_key",
        }
        with patch.dict(os.environ, env, clear=False):
            from config.app_config import get_startup_readiness
            result = get_startup_readiness()
        assert result["supabase_service_key"] is True

    def test_upstash_redis_url_fallback(self):
        """UPSTASH_REDIS_URL should be accepted when REDIS_URL is absent."""
        env = {
            "REDIS_URL": "",
            "UPSTASH_REDIS_URL": "rediss://upstash.example.com",
        }
        with patch.dict(os.environ, env, clear=False):
            from config.app_config import get_startup_readiness
            result = get_startup_readiness()
        assert result["redis_url"] is True

    def test_missing_redis_url_is_false(self):
        env = {"REDIS_URL": "", "UPSTASH_REDIS_URL": ""}
        with patch.dict(os.environ, env, clear=False):
            from config.app_config import get_startup_readiness
            result = get_startup_readiness()
        assert result["redis_url"] is False

    def test_whitespace_only_values_are_false(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "   ",
            "PERPLEXITY_API_KEY": "\t",
            "SUPABASE_URL": "  ",
            "SUPABASE_SERVICE_ROLE_KEY": "",
            "SUPABASE_API_KEY": "",
            "REDIS_URL": "",
            "UPSTASH_REDIS_URL": "",
        }
        with patch.dict(os.environ, env, clear=False):
            from config.app_config import get_startup_readiness
            result = get_startup_readiness()
        assert result["telegram_bot_token"] is False
        assert result["perplexity_api_key"] is False
        assert result["supabase_url"] is False

    def test_return_value_has_all_expected_keys(self):
        from config.app_config import get_startup_readiness
        result = get_startup_readiness()
        expected_keys = {
            "telegram_bot_token",
            "perplexity_api_key",
            "supabase_url",
            "supabase_service_key",
            "redis_url",
        }
        assert expected_keys == set(result.keys())

    def test_all_values_are_bool(self):
        from config.app_config import get_startup_readiness
        result = get_startup_readiness()
        for key, val in result.items():
            assert isinstance(val, bool), f"{key} should be bool, got {type(val)}"
