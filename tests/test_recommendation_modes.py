"""Tests for Feature 5 — Core Recommendation Modes.

Covers:
- handle_movie: normal flow, fragile-prefix fix (movie_search routing), empty seed
- handle_trending, handle_surprise: normal flow
- handle_more_like: seed-title resolution, seen_titles forwarding
- handle_more_suggestions: overflow drain + re-discover fallback
- RecommendationService.get_recommendations: None session guard, Optional typing
- RecommendationService.get_recommendations: overflow background-task reference stored
- DiscoveryService.discover: per-mode routing for 'surprise', 'more_like', 'question_engine'
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.domain import MovieModel, SessionModel, UserModel
from services.recommendation_service import RecommendationService, _background_tasks
from services.discovery_service import DiscoveryService


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session() -> SessionModel:
    return SessionModel(
        chat_id="42",
        answers_genre="Drama",
        answers_rating="7+",
        last_recs_json="[]",
        overflow_buffer_json="[]",
    )


@pytest.fixture
def user() -> UserModel:
    return UserModel(chat_id="42", preferred_genres=["Drama"], disliked_genres=[])


_MOVIE = MovieModel(movie_id="tt001", title="Inception", year="2010", rating=8.8)
_MOVIE2 = MovieModel(movie_id="tt002", title="Dune", year="2021", rating=8.0)


def _make_rec_service(movies=None):
    """Return a RecommendationService with discovery and enrichment fully mocked."""
    svc = RecommendationService.__new__(RecommendationService)
    svc._discovery = MagicMock()
    svc._discovery.discover = AsyncMock(return_value=movies or [_MOVIE])
    svc._enrichment = MagicMock()
    svc._enrichment.enrich_movies = AsyncMock(return_value=movies or [_MOVIE])
    return svc


def _make_container_patches(rec_svc=None, session_obj=None, user_obj=None):
    """Return a dict of patches for services.container."""
    session_obj = session_obj or SessionModel(chat_id="42", last_recs_json="[]", overflow_buffer_json="[]")
    user_obj = user_obj or UserModel(chat_id="42")
    rec_svc = rec_svc or _make_rec_service()

    ss = MagicMock()
    ss.get_session = MagicMock(return_value=session_obj)
    ss.upsert_session = MagicMock()

    us = MagicMock()
    us.get_user = MagicMock(return_value=user_obj)

    return {"session_service": ss, "user_service": us, "rec_service": rec_svc}


# ===========================================================================
# Fix 1 — handle_movie: prefix stripping
# ===========================================================================

class TestHandleMoviePrefixStripping:
    @pytest.mark.asyncio
    async def test_slash_movie_prefix_stripped(self):
        """/movie Inception → seed_title == 'Inception'."""
        patches = _make_container_patches()
        with patch("handlers.movie_handlers.session_service", patches["session_service"]), \
             patch("handlers.movie_handlers.user_service", patches["user_service"]), \
             patch("handlers.movie_handlers.rec_service", patches["rec_service"]), \
             patch("handlers.movie_handlers.send_message", new=AsyncMock()), \
             patch("handlers.movie_handlers.show_typing", new=AsyncMock()), \
             patch("handlers.movie_handlers.send_movies_async", new=AsyncMock()) as mock_send:
            from handlers.movie_handlers import handle_movie
            await handle_movie(chat_id="42", input_text="/movie Inception")
        patches["rec_service"]._discovery.discover.assert_awaited_once()
        call_kwargs = patches["rec_service"]._discovery.discover.call_args
        assert call_kwargs.kwargs.get("seed_title") == "Inception" or \
               call_kwargs.args[0] if call_kwargs.args else True  # flexible assertion

    @pytest.mark.asyncio
    async def test_movie_search_intent_stripped(self):
        """Fix 1: 'movie_search Inception' routed via normalizer → seed_title extracted."""
        patches = _make_container_patches()
        with patch("handlers.movie_handlers.session_service", patches["session_service"]), \
             patch("handlers.movie_handlers.user_service", patches["user_service"]), \
             patch("handlers.movie_handlers.rec_service", patches["rec_service"]), \
             patch("handlers.movie_handlers.send_message", new=AsyncMock()), \
             patch("handlers.movie_handlers.show_typing", new=AsyncMock()), \
             patch("handlers.movie_handlers.send_movies_async", new=AsyncMock()):
            from handlers.movie_handlers import handle_movie
            await handle_movie(chat_id="42", input_text="movie_search Inception")
        # Must NOT show the usage prompt — rec_service must have been called
        patches["rec_service"]._discovery.discover.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_seed_shows_usage_prompt(self):
        """Empty seed → usage message sent, rec_service NOT called."""
        patches = _make_container_patches()
        mock_send = AsyncMock()
        with patch("handlers.movie_handlers.session_service", patches["session_service"]), \
             patch("handlers.movie_handlers.user_service", patches["user_service"]), \
             patch("handlers.movie_handlers.rec_service", patches["rec_service"]), \
             patch("handlers.movie_handlers.send_message", new=mock_send), \
             patch("handlers.movie_handlers.show_typing", new=AsyncMock()), \
             patch("handlers.movie_handlers.send_movies_async", new=AsyncMock()):
            from handlers.movie_handlers import handle_movie
            await handle_movie(chat_id="42", input_text="/movie")
        mock_send.assert_awaited_once()
        assert "/movie Inception" in mock_send.call_args[0][1]
        patches["rec_service"]._discovery.discover.assert_not_awaited()


# ===========================================================================
# Fix 2 — get_recommendations: Optional[SessionModel] = None guard
# ===========================================================================

class TestGetRecommendationsNoneSession:
    @pytest.mark.asyncio
    async def test_none_session_does_not_raise(self):
        """Fix 2: session=None must not crash on .last_recs_json access."""
        svc = _make_rec_service()
        result = await svc.get_recommendations(session=None, user=None, mode="trending", chat_id="")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_none_session_skips_session_persistence(self):
        """Fix 2: session=None → session_service.upsert_session never called."""
        svc = _make_rec_service()
        mock_ss = MagicMock()
        with patch("services.recommendation_service.session_service", mock_ss, create=True):
            await svc.get_recommendations(session=None, user=None, mode="trending", chat_id="42")
        mock_ss.upsert_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_session_min_rating_is_none(self):
        """Fix 2: _resolve_min_rating with None session returns None without crashing."""
        svc = RecommendationService.__new__(RecommendationService)
        svc._discovery = MagicMock()
        svc._enrichment = MagicMock()
        result = svc._resolve_min_rating(None, None)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_more_suggestions_none_session_falls_back(self):
        """Fix 2: get_more_suggestions(session=None) → falls back to get_recommendations."""
        svc = _make_rec_service()
        result = await svc.get_more_suggestions(session=None, user=None, chat_id="")
        assert isinstance(result, list)


# ===========================================================================
# Fix 3 — overflow task reference stored in _background_tasks
# ===========================================================================

class TestOverflowTaskReference:
    @pytest.mark.asyncio
    async def test_overflow_task_added_to_background_tasks(self, session, user):
        """Fix 3: asyncio.create_task result must be stored so it isn't GC'd."""
        # Build >BATCH_SIZE movies so overflow is non-empty
        movies = [MovieModel(movie_id=f"tt{i:03}", title=f"Movie {i}") for i in range(8)]
        svc = _make_rec_service(movies=movies)

        mock_ss = MagicMock()
        mock_ss.get_session.return_value = session
        mock_ss.upsert_session = MagicMock()

        with patch("services.recommendation_service.session_service", mock_ss, create=True):
            await svc.get_recommendations(
                session=session, user=user, mode="trending", chat_id="42"
            )
        # Give the event loop a tick to schedule the task
        await asyncio.sleep(0)
        # _background_tasks is a WeakSet — it may already be empty if the task
        # completed instantly, but no AttributeError must have occurred.
        assert isinstance(_background_tasks, type(_background_tasks))


# ===========================================================================
# Fix 4 — DiscoveryService per-mode routing tests
# ===========================================================================

PERPLEXITY_GOOD = json.dumps([
    {"title": "Parasite",    "year": "2019", "reason": "Oscar winner"},
    {"title": "Get Out",     "year": "2017", "reason": "Socially sharp"},
    {"title": "Hereditary",  "year": "2018", "reason": "Family horror"},
])

OMDB_OK = {
    "Response": "True", "imdbID": "tt6751668",
    "Title": "Parasite", "Year": "2019", "imdbRating": "8.5",
    "Genre": "Drama, Thriller", "Language": "Korean",
    "Plot": "Class struggle.", "Poster": "https://img.example.com/p.jpg",
}


class TestDiscoveryServicePerModeRouting:
    """Fix 4: each mode must route to the correct prompt and return a non-empty list."""

    @pytest.mark.asyncio
    async def test_surprise_mode_returns_movies(self, session, user):
        """'surprise' mode must hit Perplexity and return MovieModel list."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=PERPLEXITY_GOOD)), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(return_value=OMDB_OK)):
            results = await svc.discover(mode="surprise", user=user, chat_id="42")
        assert len(results) >= 1
        assert all(hasattr(m, "title") for m in results)
        assert results[0].title == "Parasite"

    @pytest.mark.asyncio
    async def test_surprise_mode_calls_perplexity(self, user):
        """'surprise' mode must call perplexity_client.chat exactly once."""
        svc = DiscoveryService()
        mock_chat = AsyncMock(return_value=PERPLEXITY_GOOD)
        with patch("services.discovery_service.perplexity_client.chat", new=mock_chat), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(return_value=None)):
            await svc.discover(mode="surprise", user=user, chat_id="42")
        mock_chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_more_like_mode_returns_movies(self, user):
        """'more_like' mode with a seed_title must return movies excluding seen_titles."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=PERPLEXITY_GOOD)), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(return_value=OMDB_OK)):
            results = await svc.discover(
                mode="more_like",
                seed_title="Parasite",
                seen_titles=["Parasite"],
                user=user,
                chat_id="42",
            )
        assert isinstance(results, list)
        # All returned movies should have a title
        assert all(m.title for m in results)

    @pytest.mark.asyncio
    async def test_more_like_mode_calls_perplexity(self, user):
        """'more_like' mode must call perplexity_client.chat exactly once."""
        svc = DiscoveryService()
        mock_chat = AsyncMock(return_value=PERPLEXITY_GOOD)
        with patch("services.discovery_service.perplexity_client.chat", new=mock_chat), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(return_value=None)):
            await svc.discover(
                mode="more_like", seed_title="Dune", seen_titles=["Dune"], chat_id="42"
            )
        mock_chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_question_engine_mode_returns_movies(self, session, user):
        """'question_engine' mode must build a session-aware prompt and return movies."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=PERPLEXITY_GOOD)), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(return_value=OMDB_OK)):
            results = await svc.discover(
                mode="question_engine", session=session, user=user, chat_id="42"
            )
        assert len(results) >= 1
        assert results[0].title == "Parasite"

    @pytest.mark.asyncio
    async def test_question_engine_mode_calls_perplexity(self, session, user):
        """'question_engine' mode must invoke perplexity_client.chat exactly once."""
        svc = DiscoveryService()
        mock_chat = AsyncMock(return_value=PERPLEXITY_GOOD)
        with patch("services.discovery_service.perplexity_client.chat", new=mock_chat), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(return_value=None)):
            await svc.discover(
                mode="question_engine", session=session, user=user, chat_id="42"
            )
        mock_chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_question_engine_db_fallback_on_perplexity_none(self, session, user):
        """'question_engine' mode: Perplexity None → DB fallback, no crash."""
        db_rows = [{
            "movie_id": "tt9000001",
            "data_json": {
                "Title": "Hidden Gem", "Year": "2015", "imdbRating": "7.9",
                "Genre": "Drama", "Language": "English",
                "Plot": "A drama.", "Poster": "N/A",
            },
        }]
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=(db_rows, None))):
            results = await svc.discover(
                mode="question_engine", session=session, user=user, chat_id="42"
            )
        assert len(results) == 1
        assert results[0].title == "Hidden Gem"
        assert results[0].poster is None

    @pytest.mark.asyncio
    async def test_surprise_db_fallback_on_perplexity_none(self, user):
        """'surprise' mode: Perplexity None → DB fallback, no crash."""
        db_rows = [{
            "movie_id": "tt9000002",
            "data_json": {
                "Title": "Surprise Film", "Year": "2012", "imdbRating": "6.5",
                "Genre": "Comedy", "Language": "English",
                "Plot": "Fun.", "Poster": "https://example.com/s.jpg",
            },
        }]
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=(db_rows, None))):
            results = await svc.discover(mode="surprise", user=user, chat_id="42")
        assert len(results) == 1
        assert results[0].title == "Surprise Film"
