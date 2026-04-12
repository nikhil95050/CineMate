"""
Tests for the three targeted bug fixes:

  1. HTTP-200 / enqueue_job wiring         (main.py)
  2. MovieModel consistency in movie_service (services/movie_service.py)
  3. seen_titles forwarded in handle_more_like (handlers/movie_handlers.py)

Run with:
    python -m pytest tests/test_three_fixes.py -v
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(last_recs=None):
    s = MagicMock()
    s.last_recs_json = json.dumps(last_recs or [])
    s.overflow_buffer_json = "[]"
    s.to_row.return_value = {}
    return s


def _make_user():
    u = MagicMock()
    u.to_row.return_value = {}
    return u


# ---------------------------------------------------------------------------
# Fix 1 -- HTTP-200 always returned; enqueue_job wired correctly
# ---------------------------------------------------------------------------

class TestFix1EnqueueJob:
    """
    main.py must call services.enqueue_job (not fire-and-forget asyncio) and
    ALWAYS return HTTP 200, even when enqueue_job raises.
    """

    def _build_fake_update(self, chat_id=42, input_text="/start"):
        return {
            "update_id": 9001,
            "message": {
                "message_id": 1,
                "from": {"id": chat_id, "username": "tester"},
                "chat": {"id": chat_id},
                "text": input_text,
                "date": 0,
            },
        }

    def test_returns_200_when_enqueue_succeeds(self):
        """Webhook returns ok:true when enqueue_job succeeds."""
        from fastapi.testclient import TestClient

        captured = []

        def fake_enqueue(func_path, **kwargs):
            captured.append((func_path, kwargs))

        with (
            patch("services.enqueue_job", side_effect=fake_enqueue),
            patch("config.redis_cache.mark_processed_update", return_value=True),
            patch("config.redis_cache.is_rate_limited", return_value=False),
            patch("services.container.session_service") as mock_ss,
            patch("services.container.user_service") as mock_us,
        ):
            mock_ss.get_session.return_value = _make_session()
            mock_us.get_user.return_value = _make_user()

            import importlib
            import main as _main
            importlib.reload(_main)
            _main.BOT_TOKEN = "secret"

            client = TestClient(_main.app, raise_server_exceptions=False)
            resp = client.post("/webhook/secret", json=self._build_fake_update())

        assert resp.status_code == 200
        assert resp.json().get("ok") is True
        assert len(captured) == 1, "enqueue_job must be called exactly once"
        assert captured[0][0] == "services.worker_service.run_intent_job"

    def test_returns_200_even_when_enqueue_raises(self):
        """Webhook must return ok:true even if enqueue_job blows up (Redis down)."""
        from fastapi.testclient import TestClient

        def exploding_enqueue(func_path, **kwargs):
            raise RuntimeError("Redis is down")

        with (
            patch("services.enqueue_job", side_effect=exploding_enqueue),
            patch("config.redis_cache.mark_processed_update", return_value=True),
            patch("config.redis_cache.is_rate_limited", return_value=False),
            patch("services.container.session_service") as mock_ss,
            patch("services.container.user_service") as mock_us,
        ):
            mock_ss.get_session.return_value = _make_session()
            mock_us.get_user.return_value = _make_user()

            import importlib
            import main as _main
            importlib.reload(_main)
            _main.BOT_TOKEN = "token"

            client = TestClient(_main.app, raise_server_exceptions=False)
            resp = client.post("/webhook/token", json=self._build_fake_update())

        assert resp.status_code == 200
        assert resp.json().get("ok") is True

    def test_user_row_populated_from_user_service(self):
        """CC-2 guard: the user dict passed to enqueue_job must not be empty."""
        from fastapi.testclient import TestClient

        captured = []

        def fake_enqueue(func_path, **kwargs):
            captured.append(kwargs)

        fake_user = MagicMock()
        fake_user.to_row.return_value = {"chat_id": "42", "preferred_genres": ["Action"]}

        with (
            patch("services.enqueue_job", side_effect=fake_enqueue),
            patch("config.redis_cache.mark_processed_update", return_value=True),
            patch("config.redis_cache.is_rate_limited", return_value=False),
            patch("services.container.session_service") as mock_ss,
            patch("services.container.user_service") as mock_us,
        ):
            mock_ss.get_session.return_value = _make_session()
            mock_us.get_user.return_value = fake_user

            import importlib
            import main as _main
            importlib.reload(_main)
            _main.BOT_TOKEN = "tok"

            client = TestClient(_main.app, raise_server_exceptions=False)
            client.post("/webhook/tok", json=self._build_fake_update())

        assert captured, "enqueue_job was not called"
        user_kwarg = captured[0].get("user", {})
        assert user_kwarg.get("preferred_genres") == ["Action"], (
            "user payload must come from user_service, not an empty dict"
        )


# ---------------------------------------------------------------------------
# Fix 2 -- MovieModel consistency in movie_service
# ---------------------------------------------------------------------------

class TestFix2MovieServiceMovieModel:
    """
    MovieService.get_movie_from_history and get_random_watchlist_reminder must
    return Optional[MovieModel], not raw dicts.
    WatchlistService.add and HistoryService.add must accept MovieModel instances.
    """

    def _history_row(self):
        return {
            "movie_id": "tt0114709",
            "title": "Toy Story",
            "year": "1995",
            "rating": "8.3",
            "genres": "Animation, Adventure, Comedy",
            "language": "English",
        }

    def test_get_movie_from_history_returns_movie_model(self):
        from services.movie_service import MovieService
        from models.domain import MovieModel

        repo = MagicMock()
        repo.get_by_movie_id.return_value = self._history_row()
        svc = MovieService(history_repo=repo)

        result = svc.get_movie_from_history("42", "tt0114709")

        assert isinstance(result, MovieModel), f"Expected MovieModel, got {type(result)}"
        assert result.title == "Toy Story"
        assert result.rating == 8.3

    def test_get_movie_from_history_returns_none_on_missing(self):
        from services.movie_service import MovieService

        repo = MagicMock()
        repo.get_by_movie_id.return_value = None
        svc = MovieService(history_repo=repo)

        assert svc.get_movie_from_history("42", "tt_missing") is None

    def test_get_movie_from_history_returns_none_on_exception(self):
        from services.movie_service import MovieService

        repo = MagicMock()
        repo.get_by_movie_id.side_effect = RuntimeError("DB error")
        svc = MovieService(history_repo=repo)

        assert svc.get_movie_from_history("42", "tt0114709") is None, (
            "Exceptions should be swallowed and None returned"
        )

    def test_get_random_watchlist_reminder_returns_movie_model(self):
        from services.movie_service import MovieService
        from models.domain import MovieModel

        repo = MagicMock()
        repo.get_watchlist.return_value = [self._history_row()]
        svc = MovieService(watchlist_repo=repo)

        result = svc.get_random_watchlist_reminder("42")

        assert isinstance(result, MovieModel), f"Expected MovieModel, got {type(result)}"
        assert result.movie_id == "tt0114709"

    def test_get_random_watchlist_reminder_returns_none_when_empty(self):
        from services.movie_service import MovieService

        repo = MagicMock()
        repo.get_watchlist.return_value = []
        svc = MovieService(watchlist_repo=repo)

        assert svc.get_random_watchlist_reminder("42") is None

    def test_add_to_watchlist_accepts_movie_model(self):
        """WatchlistService.add must accept a MovieModel, not a dict."""
        from services.movie_service import WatchlistService
        from models.domain import MovieModel

        repo = MagicMock()
        repo.add_to_watchlist.return_value = True
        svc = WatchlistService(watchlist_repo=repo)

        movie = MovieModel(movie_id="tt0114709", title="Toy Story", year="1995")
        # asyncio.run() replaces deprecated asyncio.get_event_loop().run_until_complete()
        asyncio.run(svc.add("42", movie))
        repo.add_to_watchlist.assert_called_once()

    def test_add_to_watchlist_with_dict_raises(self):
        """Passing a raw dict (old, incorrect usage) must fail at the type boundary."""
        from services.movie_service import MovieService

        repo = MagicMock()
        repo.add_to_watchlist.return_value = True
        svc = MovieService(watchlist_repo=repo)

        with pytest.raises((AttributeError, TypeError)):
            svc.add_to_watchlist("42", {"movie_id": "tt0114709", "title": "Toy Story"})  # type: ignore[arg-type]

    def test_history_service_add_accepts_movie_model_list(self):
        """HistoryService.add must accept a list of MovieModel objects."""
        from services.movie_service import HistoryService
        from models.domain import MovieModel

        repo = MagicMock()
        repo.log_recommendations.return_value = None
        svc = HistoryService(history_repo=repo)

        movies = [MovieModel(movie_id="tt0114709", title="Toy Story")]
        asyncio.run(svc.add("42", movies))
        repo.log_recommendations.assert_called_once()


# ---------------------------------------------------------------------------
# Fix 3 -- seen_titles forwarded in handle_more_like (P5-1)
# ---------------------------------------------------------------------------

class TestFix3SeenTitlesInMoreLike:
    """
    handle_more_like must pass seen_titles to rec_service.get_recommendations
    so that previously shown movies are excluded.
    """

    def _last_recs(self):
        return [
            {"movie_id": "tt0114709", "title": "Toy Story"},
            {"movie_id": "tt0435761", "title": "Toy Story 3"},
        ]

    @pytest.mark.asyncio
    async def test_seen_titles_forwarded_to_rec_service(self):
        """get_recommendations must receive seen_titles matching last_recs titles."""
        captured_kwargs = {}

        async def fake_get_recommendations(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return []

        session = _make_session(last_recs=self._last_recs())
        user = _make_user()

        with (
            patch("handlers.movie_handlers.session_service") as mock_ss,
            patch("handlers.movie_handlers.user_service") as mock_us,
            patch("handlers.movie_handlers.rec_service") as mock_rec,
            patch("handlers.movie_handlers.send_movies_async", new_callable=AsyncMock),
            patch("handlers.movie_handlers.send_message", new_callable=AsyncMock),
            patch("handlers.movie_handlers.show_typing", new_callable=AsyncMock),
        ):
            mock_ss.get_session.return_value = session
            mock_us.get_user.return_value = user
            mock_rec.get_recommendations = AsyncMock(side_effect=fake_get_recommendations)

            from handlers.movie_handlers import handle_more_like
            await handle_more_like(chat_id=42, input_text="more_like_tt0114709")

        assert "seen_titles" in captured_kwargs, (
            "seen_titles kwarg was not forwarded to get_recommendations"
        )
        seen = captured_kwargs["seen_titles"]
        assert "Toy Story" in seen, "Seed movie title must be in seen_titles"
        assert "Toy Story 3" in seen, "Other last_recs titles must be in seen_titles"

    @pytest.mark.asyncio
    async def test_seen_titles_empty_on_fresh_session(self):
        """With an empty session, seen_titles must be [] and not crash."""
        captured_kwargs = {}

        async def fake_get_recommendations(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return []

        session = _make_session(last_recs=[])
        user = _make_user()

        with (
            patch("handlers.movie_handlers.session_service") as mock_ss,
            patch("handlers.movie_handlers.user_service") as mock_us,
            patch("handlers.movie_handlers.rec_service") as mock_rec,
            patch("handlers.movie_handlers.send_movies_async", new_callable=AsyncMock),
            patch("handlers.movie_handlers.send_message", new_callable=AsyncMock),
            patch("handlers.movie_handlers.show_typing", new_callable=AsyncMock),
        ):
            mock_ss.get_session.return_value = session
            mock_us.get_user.return_value = user
            mock_rec.get_recommendations = AsyncMock(side_effect=fake_get_recommendations)

            from handlers.movie_handlers import handle_more_like
            await handle_more_like(chat_id=42, input_text="more_like_tt0114709")

        assert captured_kwargs.get("seen_titles") == []

    @pytest.mark.asyncio
    async def test_no_duplicates_when_service_respects_seen_titles(self):
        """
        Integration-style: if seen_titles are correctly forwarded, a rec_service
        that respects them returns zero overlap with the previous batch.
        """
        prev_recs = self._last_recs()

        async def deduplicating_service(*args, **kwargs):
            seen = {t.lower() for t in kwargs.get("seen_titles", [])}
            all_movies = [
                {"movie_id": "tt0114709", "title": "Toy Story"},    # excluded
                {"movie_id": "tt0435761", "title": "Toy Story 3"},  # excluded
                {"movie_id": "tt0266543", "title": "Finding Nemo"}, # new
            ]
            return [m for m in all_movies if m["title"].lower() not in seen]

        session = _make_session(last_recs=prev_recs)
        user = _make_user()

        with (
            patch("handlers.movie_handlers.session_service") as mock_ss,
            patch("handlers.movie_handlers.user_service") as mock_us,
            patch("handlers.movie_handlers.rec_service") as mock_rec,
            patch("handlers.movie_handlers.send_movies_async", new_callable=AsyncMock) as mock_send,
            patch("handlers.movie_handlers.send_message", new_callable=AsyncMock),
            patch("handlers.movie_handlers.show_typing", new_callable=AsyncMock),
        ):
            mock_ss.get_session.return_value = session
            mock_us.get_user.return_value = user
            mock_rec.get_recommendations = AsyncMock(side_effect=deduplicating_service)

            from handlers.movie_handlers import handle_more_like
            await handle_more_like(chat_id=42, input_text="more_like_tt0114709")

        sent_movies = mock_send.call_args[0][1]
        titles = [m["title"] for m in sent_movies]
        assert "Toy Story" not in titles
        assert "Toy Story 3" not in titles
        assert "Finding Nemo" in titles
