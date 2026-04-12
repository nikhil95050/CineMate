"""Feature 11: E2E Regression & Hardening test suite for CineMate / Antigravity.

Covers:
  - Webhook + queue + worker path (full dispatch chain)
  - Question engine (questioning intent lifecycle)
  - Each recommendation mode (movie, trending, surprise, more_like, more_suggestions, star)
  - History & watchlist (add, view, paginate, watched, save)
  - Feedback & taste profile (like, dislike, min_rating)
  - Star & share (star filmography, share card with/without recs)
  - Admin features (health, stats, clear_cache, errors, usage, broadcast, provider toggle)
  - Provider health & semantic routing (circuit-breaker, fallback, cache, recursion guard)
  - Error logging (LoggingService captures context, no stack trace leaks)
  - Reliability (graceful fallback on Redis down, Supabase down, provider down)
  - Rate-limiting / dedup at webhook layer
  - Normalizer edge cases (empty text, callback_query, missing fields)
  - Intent detection edge cases (admin commands, session-driven questioning, fallback)

Chat ID used for live test runs: 1878846631
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CHAT_ID = "1878846631"
USERNAME = "nikhil_test"


def _make_session(**kwargs) -> Dict[str, Any]:
    defaults = {
        "chat_id": CHAT_ID,
        "session_state": "idle",
        "last_recs_json": "[]",
        "question_index": 0,
        "answers": "{}",
        "filters": "{}",
    }
    defaults.update(kwargs)
    return defaults


def _make_user(**kwargs) -> Dict[str, Any]:
    defaults = {
        "chat_id": CHAT_ID,
        "username": USERNAME,
        "taste_profile": "{}",
        "tier": "user",
    }
    defaults.update(kwargs)
    return defaults


def _run(coro):
    """Helper to run async functions in sync test context."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ============================================================
# SECTION 1: Normalizer edge cases
# ============================================================

class TestNormalizer:

    def test_message_fields_extracted(self):
        from handlers.normalizer import normalize_input
        update = {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "date": 1700000000,
                "text": "/start",
                "chat": {"id": 1878846631},
                "from": {"username": "nikhil_test"},
            },
        }
        result = normalize_input(update)
        assert result["chat_id"] == 1878846631
        assert result["input_text"] == "/start"
        assert result["action_type"] == "message"
        assert result["username"] == "nikhil_test"
        assert result["sent_at"] is not None

    def test_callback_query_fields_extracted(self):
        from handlers.normalizer import normalize_input
        update = {
            "update_id": 2,
            "callback_query": {
                "id": "cq123",
                "data": "like_tt1234567",
                "from": {"username": "nikhil_test"},
                "message": {
                    "message_id": 20,
                    "date": 1700000000,
                    "chat": {"id": 1878846631},
                },
            },
        }
        result = normalize_input(update)
        assert result["chat_id"] == 1878846631
        assert result["input_text"] == "like_tt1234567"
        assert result["action_type"] == "callback"
        assert result["callback_query_id"] == "cq123"

    def test_unknown_update_type_returns_none_chat_id(self):
        from handlers.normalizer import normalize_input
        result = normalize_input({"update_id": 3, "edited_message": {}})
        assert result["chat_id"] is None

    def test_empty_update_safe(self):
        from handlers.normalizer import normalize_input
        result = normalize_input({})
        assert result["chat_id"] is None
        assert result["input_text"] == ""

    def test_missing_from_field_defaults_empty_username(self):
        from handlers.normalizer import normalize_input
        update = {
            "update_id": 4,
            "message": {
                "message_id": 5,
                "date": 1700000000,
                "text": "hello",
                "chat": {"id": 42},
            },
        }
        result = normalize_input(update)
        assert result["username"] == ""


# ============================================================
# SECTION 2: Intent detection
# ============================================================

class TestDetectIntent:

    def test_start_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/start") == "start"

    def test_help_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/help") == "help"

    def test_movie_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/movie action thriller") == "movie"

    def test_search_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/search Inception") == "search"

    def test_trending_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/trending") == "trending"

    def test_trending_bare(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("trending") == "trending"

    def test_surprise_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/surprise") == "surprise"

    def test_history_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/history") == "history"

    def test_watchlist_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/watchlist") == "watchlist"

    def test_watchlist_pagination_callback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("watchlist_p2") == "watchlist"

    def test_history_pagination_callback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("history_p3") == "history"

    def test_rating_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/rating 7") == "min_rating"

    def test_min_rating_alias(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/min_rating 6") == "min_rating"

    def test_like_callback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("like_tt1234567") == "like"

    def test_dislike_callback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("dislike_tt9876543") == "dislike"

    def test_watched_callback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("watched_tt0000001") == "watched"

    def test_save_callback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("save_tt0000002") == "save"

    def test_more_like_callback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("more_like_tt1234") == "more_like"

    def test_more_suggestions_action(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("more_suggestions_action") == "more_suggestions"

    def test_q_reset_callback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("q_reset") == "reset"

    def test_q_more_recs_callback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("q_more_recs") == "more_suggestions"

    def test_q_answer_callback_routes_questioning(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("q_1") == "questioning"

    def test_admin_health_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/admin_health") == "admin_health"

    def test_admin_stats_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/admin_stats") == "admin_stats"

    def test_admin_broadcast_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/admin_broadcast hello everyone") == "admin_broadcast"

    def test_session_questioning_state_overrides_fallback(self):
        from handlers.normalizer import detect_intent
        session = {"session_state": "questioning"}
        assert detect_intent("I like comedies", session) == "questioning"

    def test_unknown_text_returns_fallback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("blah blah random words") == "fallback"

    def test_empty_text_returns_fallback(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("") == "fallback"

    def test_reset_command(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/reset") == "reset"

    def test_none_input_does_not_raise(self):
        from handlers.normalizer import detect_intent
        result = detect_intent(None)
        assert isinstance(result, str)

    def test_very_long_input_does_not_raise(self):
        from handlers.normalizer import detect_intent
        result = detect_intent("a" * 10000)
        assert isinstance(result, str)


# ============================================================
# SECTION 3: Webhook -> Queue -> Worker path
# ============================================================

class TestWebhookQueueWorker:

    def test_webhook_dispatches_start_intent(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN_E2E")
        from importlib import reload
        import main as main_module
        reload(main_module)
        from fastapi.testclient import TestClient
        test_client = TestClient(main_module.app)
        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)
        captured = {}
        def fake_enqueue(func_name, **kwargs):
            captured["func_name"] = func_name
            captured["intent"] = kwargs.get("intent")
            captured["chat_id"] = kwargs.get("chat_id")
        monkeypatch.setattr("services.enqueue_job", fake_enqueue)
        resp = test_client.post("/webhook/TESTTOKEN_E2E", json={
            "update_id": 500,
            "message": {
                "message_id": 1, "date": 1700000000,
                "text": "/start",
                "chat": {"id": int(CHAT_ID)},
                "from": {"username": USERNAME},
            },
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert captured.get("intent") == "start"
        assert captured.get("chat_id") == CHAT_ID

    def test_webhook_dispatches_movie_intent(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN_MOVIE")
        from importlib import reload
        import main as main_module
        reload(main_module)
        from fastapi.testclient import TestClient
        test_client = TestClient(main_module.app)
        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)
        captured = {}
        monkeypatch.setattr("services.enqueue_job", lambda fn, **kw: captured.update(kw))
        resp = test_client.post("/webhook/TESTTOKEN_MOVIE", json={
            "update_id": 501,
            "message": {
                "message_id": 2, "date": 1700000000,
                "text": "/movie thriller",
                "chat": {"id": int(CHAT_ID)},
                "from": {"username": USERNAME},
            },
        })
        assert resp.status_code == 200
        assert captured.get("intent") == "movie"

    def test_webhook_duplicate_update_dropped(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN_DUP")
        from importlib import reload
        import main as main_module
        reload(main_module)
        from fastapi.testclient import TestClient
        test_client = TestClient(main_module.app)
        from config import redis_cache
        seen = set()
        def mark(uid):
            if uid in seen:
                return False
            seen.add(uid)
            return True
        monkeypatch.setattr(redis_cache, "mark_processed_update", mark)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)
        calls = {"n": 0}
        monkeypatch.setattr("services.enqueue_job", lambda fn, **kw: calls.update(n=calls["n"] + 1))
        payload = {
            "update_id": 502,
            "message": {"message_id": 3, "date": 1700000000,
                        "text": "/help", "chat": {"id": int(CHAT_ID)},
                        "from": {"username": USERNAME}},
        }
        test_client.post("/webhook/TESTTOKEN_DUP", json=payload)
        test_client.post("/webhook/TESTTOKEN_DUP", json=payload)
        assert calls["n"] == 1

    def test_webhook_rate_limited_does_not_enqueue(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN_RATE")
        from importlib import reload
        import main as main_module
        reload(main_module)
        from fastapi.testclient import TestClient
        test_client = TestClient(main_module.app)
        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": True)
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message_safely", AsyncMock(return_value=None))
        calls = {"n": 0}
        monkeypatch.setattr("services.enqueue_job", lambda fn, **kw: calls.update(n=calls["n"] + 1))
        test_client.post("/webhook/TESTTOKEN_RATE", json={
            "update_id": 503,
            "message": {"message_id": 4, "date": 1700000000,
                        "text": "/movie", "chat": {"id": int(CHAT_ID)},
                        "from": {"username": USERNAME}},
        })
        assert calls["n"] == 0

    def test_webhook_empty_body_returns_200(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN_NEG1")
        from importlib import reload
        import main as main_module
        reload(main_module)
        from fastapi.testclient import TestClient
        test_client = TestClient(main_module.app)
        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)
        resp = test_client.post("/webhook/TESTTOKEN_NEG1", json={})
        assert resp.status_code == 200

    def test_webhook_message_without_text_handled(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN_NEG2")
        from importlib import reload
        import main as main_module
        reload(main_module)
        from fastapi.testclient import TestClient
        test_client = TestClient(main_module.app)
        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)
        monkeypatch.setattr("services.enqueue_job", lambda fn, **kw: None)
        resp = test_client.post("/webhook/TESTTOKEN_NEG2", json={
            "update_id": 600,
            "message": {
                "message_id": 50, "date": 1700000000,
                "chat": {"id": int(CHAT_ID)},
                "from": {"username": USERNAME},
                # No "text" field
            },
        })
        assert resp.status_code == 200

    # ---- Worker routing tests ----

    def _run_worker(self, intent, input_text, monkeypatch, patch_path, handler_key):
        called = {}
        async def fake_handler(**kwargs):
            called["ok"] = True
        monkeypatch.setattr(patch_path, fake_handler)
        from services.worker_service import run_intent_job
        _run(run_intent_job(
            intent=intent, chat_id=CHAT_ID, username=USERNAME,
            input_text=input_text,
            session=_make_session(), user=_make_user(),
            request_id=f"req-{intent}",
        ))
        return called

    def test_worker_routes_help(self, monkeypatch):
        assert self._run_worker("help", "/help", monkeypatch, "handlers.user_handlers.handle_help", "help").get("ok")

    def test_worker_routes_trending(self, monkeypatch):
        assert self._run_worker("trending", "/trending", monkeypatch, "handlers.movie_handlers.handle_trending", "trending").get("ok")

    def test_worker_routes_surprise(self, monkeypatch):
        assert self._run_worker("surprise", "/surprise", monkeypatch, "handlers.movie_handlers.handle_surprise", "surprise").get("ok")

    def test_worker_routes_watchlist(self, monkeypatch):
        assert self._run_worker("watchlist", "/watchlist", monkeypatch, "handlers.history_handlers.handle_watchlist", "watchlist").get("ok")

    def test_worker_routes_history(self, monkeypatch):
        assert self._run_worker("history", "/history", monkeypatch, "handlers.history_handlers.handle_history", "history").get("ok")

    def test_worker_routes_star(self, monkeypatch):
        assert self._run_worker("star", "/star Leo", monkeypatch, "handlers.discovery_handlers.handle_star", "star").get("ok")

    def test_worker_routes_share(self, monkeypatch):
        assert self._run_worker("share", "/share", monkeypatch, "handlers.discovery_handlers.handle_share", "share").get("ok")

    def test_worker_routes_admin_health(self, monkeypatch):
        assert self._run_worker("admin_health", "/admin_health", monkeypatch, "handlers.admin.handle_admin_health", "admin_health").get("ok")

    def test_worker_routes_admin_stats(self, monkeypatch):
        assert self._run_worker("admin_stats", "/admin_stats", monkeypatch, "handlers.admin.handle_admin_stats", "admin_stats").get("ok")

    def test_worker_routes_admin_clear_cache(self, monkeypatch):
        assert self._run_worker("admin_clear_cache", "/admin_clear_cache", monkeypatch, "handlers.admin.handle_admin_clear_cache", "admin_clear_cache").get("ok")

    def test_worker_routes_admin_errors(self, monkeypatch):
        assert self._run_worker("admin_errors", "/admin_errors", monkeypatch, "handlers.admin.handle_admin_errors", "admin_errors").get("ok")

    def test_worker_routes_admin_usage(self, monkeypatch):
        assert self._run_worker("admin_usage", "/admin_usage", monkeypatch, "handlers.admin.handle_admin_usage", "admin_usage").get("ok")

    def test_worker_routes_admin_broadcast(self, monkeypatch):
        assert self._run_worker("admin_broadcast", "/admin_broadcast test", monkeypatch, "handlers.admin.handle_admin_broadcast", "admin_broadcast").get("ok")

    def test_worker_routes_admin_broadcast_confirm(self, monkeypatch):
        assert self._run_worker("admin_broadcast_confirm", "admin_broadcast_confirm", monkeypatch, "handlers.admin.handle_admin_broadcast_confirm", "admin_broadcast_confirm").get("ok")

    def test_worker_routes_admin_broadcast_cancel(self, monkeypatch):
        assert self._run_worker("admin_broadcast_cancel", "admin_broadcast_cancel", monkeypatch, "handlers.admin.handle_admin_broadcast_cancel", "admin_broadcast_cancel").get("ok")

    def test_worker_routes_admin_disable_provider(self, monkeypatch):
        assert self._run_worker("admin_disable_provider", "/admin_disable_provider omdb", monkeypatch, "handlers.admin.handle_admin_disable_provider", "admin_disable_provider").get("ok")

    def test_worker_routes_admin_enable_provider(self, monkeypatch):
        assert self._run_worker("admin_enable_provider", "/admin_enable_provider omdb", monkeypatch, "handlers.admin.handle_admin_enable_provider", "admin_enable_provider").get("ok")

    def test_worker_routes_like(self, monkeypatch):
        assert self._run_worker("like", "like_tt123", monkeypatch, "handlers.feedback_handlers.handle_like", "like").get("ok")

    def test_worker_routes_dislike(self, monkeypatch):
        assert self._run_worker("dislike", "dislike_tt123", monkeypatch, "handlers.feedback_handlers.handle_dislike", "dislike").get("ok")

    def test_worker_routes_min_rating(self, monkeypatch):
        assert self._run_worker("min_rating", "/rating 7", monkeypatch, "handlers.feedback_handlers.handle_min_rating", "min_rating").get("ok")

    def test_worker_routes_watched(self, monkeypatch):
        assert self._run_worker("watched", "watched_tt123", monkeypatch, "handlers.history_handlers.handle_watched", "watched").get("ok")

    def test_worker_routes_save(self, monkeypatch):
        assert self._run_worker("save", "save_tt123", monkeypatch, "handlers.history_handlers.handle_save", "save").get("ok")

    def test_worker_routes_more_like(self, monkeypatch):
        assert self._run_worker("more_like", "more_like_tt123", monkeypatch, "handlers.movie_handlers.handle_more_like", "more_like").get("ok")

    def test_worker_routes_more_suggestions(self, monkeypatch):
        assert self._run_worker("more_suggestions", "more_suggestions_action", monkeypatch, "handlers.movie_handlers.handle_more_suggestions", "more_suggestions").get("ok")

    def test_worker_routes_questioning(self, monkeypatch):
        assert self._run_worker("questioning", "q_1", monkeypatch, "handlers.rec_handlers.handle_questioning", "questioning").get("ok")

    def test_worker_routes_search_to_movie_handler(self, monkeypatch):
        assert self._run_worker("search", "/search Inception", monkeypatch, "handlers.movie_handlers.handle_movie", "search").get("ok")

    def test_worker_routes_movie_search_to_movie_handler(self, monkeypatch):
        assert self._run_worker("movie_search", "Tell me about The Matrix", monkeypatch, "handlers.movie_handlers.handle_movie", "movie_search").get("ok")

    def test_worker_unknown_intent_does_not_raise(self, monkeypatch):
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", AsyncMock())
        from services.worker_service import run_intent_job
        # Must not raise
        _run(run_intent_job(
            intent="this_intent_does_not_exist",
            chat_id=CHAT_ID, username=USERNAME,
            input_text="garbage",
            session=_make_session(), user=_make_user(),
            request_id="req-neg-001",
        ))

    def test_worker_fallback_short_text_skips_semantic(self, monkeypatch):
        """Messages shorter than _SEMANTIC_MIN_LEN (8) must NOT call SemanticService."""
        semantic_called = {"n": 0}
        async def fake_classify(text):
            semantic_called["n"] += 1
            return "unknown"
        monkeypatch.setattr("services.worker_service._semantic_classify", fake_classify)
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", AsyncMock())
        from services.worker_service import run_intent_job
        _run(run_intent_job(
            intent="fallback", chat_id=CHAT_ID, username=USERNAME,
            input_text="hi",  # 2 chars < 8
            session=_make_session(), user=_make_user(),
            request_id="req-sem-001",
        ))
        assert semantic_called["n"] == 0

    def test_worker_fallback_long_text_calls_semantic_once(self, monkeypatch):
        """Messages >= 8 chars trigger semantic routing exactly once."""
        semantic_called = {"n": 0}
        async def fake_classify(text):
            semantic_called["n"] += 1
            return "trending"
        monkeypatch.setattr("services.worker_service._semantic_classify", fake_classify)
        called = {}
        async def fake_handle_trending(**kwargs):
            called["ok"] = True
        monkeypatch.setattr("handlers.movie_handlers.handle_trending", fake_handle_trending)
        from services.worker_service import run_intent_job
        _run(run_intent_job(
            intent="fallback", chat_id=CHAT_ID, username=USERNAME,
            input_text="what is trending today",
            session=_make_session(), user=_make_user(),
            request_id="req-sem-002",
        ))
        assert semantic_called["n"] == 1
        assert called.get("ok") is True

    def test_worker_semantic_recursion_guard_prevents_double_call(self, monkeypatch):
        """_semantic_attempted=True must prevent re-entry."""
        semantic_called = {"n": 0}
        async def fake_classify(text):
            semantic_called["n"] += 1
            return "trending"
        monkeypatch.setattr("services.worker_service._semantic_classify", fake_classify)
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", AsyncMock())
        from services.worker_service import run_intent_job
        _run(run_intent_job(
            intent="fallback", chat_id=CHAT_ID, username=USERNAME,
            input_text="what is trending today",
            session=_make_session(), user=_make_user(),
            request_id="req-sem-003",
            _semantic_attempted=True,
        ))
        assert semantic_called["n"] == 0


# ============================================================
# SECTION 4: Question engine
# ============================================================

class TestQuestionEngine:

    def test_questioning_handler_dispatched(self, monkeypatch):
        called = {}
        async def fake_handle_questioning(**kwargs):
            called["ok"] = True
        monkeypatch.setattr("handlers.rec_handlers.handle_questioning", fake_handle_questioning)
        from services.worker_service import run_intent_job
        _run(run_intent_job(
            intent="questioning", chat_id=CHAT_ID, username=USERNAME,
            input_text="q_1",
            session=_make_session(session_state="questioning", question_index=1),
            user=_make_user(), request_id="req-q-001",
        ))
        assert called.get("ok") is True

    def test_session_state_questioning_routes_freetext(self):
        from handlers.normalizer import detect_intent
        session = {"session_state": "questioning"}
        assert detect_intent("drama", session) == "questioning"
        assert detect_intent("I love thrillers", session) == "questioning"

    def test_q_reset_routes_to_reset(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("q_reset") == "reset"

    def test_q_more_recs_routes_to_more_suggestions(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("q_more_recs") == "more_suggestions"


# ============================================================
# SECTION 5: History & Watchlist
# ============================================================

class TestHistoryAndWatchlist:

    def test_history_in_memory_add_and_get(self):
        from services.movie_service import HistoryService
        from repositories.history_repository import HistoryRepository
        from models.domain import MovieModel
        repo = HistoryRepository()
        svc = HistoryService(history_repo=repo)
        movie = MovieModel(
            title="Test Movie", year="2024", imdb_id="tt_test_01",
            rating=8.0, genres="Drama", reason="Test reason", streaming="Netflix",
        )
        svc.add_to_history(CHAT_ID, movie)
        history = svc.get_history(CHAT_ID)
        assert any(m.get("imdb_id") == "tt_test_01" for m in history)

    def test_watchlist_in_memory_add_and_get(self):
        from services.movie_service import WatchlistService
        from repositories.watchlist_repository import WatchlistRepository
        from models.domain import MovieModel
        repo = WatchlistRepository()
        svc = WatchlistService(watchlist_repo=repo)
        movie = MovieModel(
            title="Watchlist Movie", year="2023", imdb_id="tt_wl_01",
            rating=7.5, genres="Action", reason="Looks great", streaming="Prime",
        )
        svc.add_to_watchlist(CHAT_ID, movie)
        wl = svc.get_watchlist(CHAT_ID)
        assert any(m.get("imdb_id") == "tt_wl_01" for m in wl)

    def test_history_pagination_intent(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("history_p2") == "history"
        assert detect_intent("history_p10") == "history"

    def test_watchlist_pagination_intent(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("watchlist_p3") == "watchlist"

    def test_watched_callback_handled(self, monkeypatch):
        called = {}
        async def fake_handle_watched(**kwargs):
            called["ok"] = True
        monkeypatch.setattr("handlers.history_handlers.handle_watched", fake_handle_watched)
        from services.worker_service import run_intent_job
        _run(run_intent_job(
            intent="watched", chat_id=CHAT_ID, username=USERNAME,
            input_text="watched_tt1234567",
            session=_make_session(), user=_make_user(), request_id="req-hist-002",
        ))
        assert called.get("ok") is True

    def test_save_callback_handled(self, monkeypatch):
        called = {}
        async def fake_handle_save(**kwargs):
            called["ok"] = True
        monkeypatch.setattr("handlers.history_handlers.handle_save", fake_handle_save)
        from services.worker_service import run_intent_job
        _run(run_intent_job(
            intent="save", chat_id=CHAT_ID, username=USERNAME,
            input_text="save_tt9876543",
            session=_make_session(), user=_make_user(), request_id="req-wl-002",
        ))
        assert called.get("ok") is True


# ============================================================
# SECTION 6: Feedback & Taste Profile
# ============================================================

class TestFeedbackAndTasteProfile:

    def test_feedback_repo_in_memory(self):
        from repositories.feedback_repository import FeedbackRepository
        repo = FeedbackRepository()
        repo.upsert_feedback(CHAT_ID, "tt_test_fb", "like")
        results = repo.get_feedback(CHAT_ID)
        assert any(r.get("imdb_id") == "tt_test_fb" for r in results)

    def test_min_rating_intent_detected(self):
        from handlers.normalizer import detect_intent
        assert detect_intent("/rating 6") == "min_rating"
        assert detect_intent("/min_rating 8") == "min_rating"


# ============================================================
# SECTION 7: Star & Share
# ============================================================

class TestStarAndShare:

    def test_star_no_name_sends_usage_hint(self, monkeypatch):
        sent = {}
        async def fake_send_message(chat_id, text, **kwargs):
            sent["text"] = text
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", fake_send_message)
        monkeypatch.setattr(tg, "show_typing", AsyncMock())
        from handlers.discovery_handlers import handle_star
        _run(handle_star(chat_id=CHAT_ID, input_text="/star", session=_make_session(), user=_make_user()))
        assert "/star" in sent.get("text", "") or "Star Filmography" in sent.get("text", "")

    def test_star_with_name_calls_discovery_and_history(self, monkeypatch):
        from models.domain import MovieModel
        fake_movies = [MovieModel(
            title="Inception", year="2010", imdb_id="tt1375666",
            rating=8.8, genres="Sci-Fi", reason="Best Nolan", streaming="Netflix"
        )]
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", AsyncMock())
        monkeypatch.setattr(tg, "show_typing", AsyncMock())
        monkeypatch.setattr("clients.telegram_card.send_movies_async", AsyncMock())
        from services.container import discovery_service, history_service
        monkeypatch.setattr(discovery_service, "get_star_movies", AsyncMock(return_value=fake_movies))
        monkeypatch.setattr(history_service, "add_to_history", MagicMock())
        from handlers.discovery_handlers import handle_star
        _run(handle_star(chat_id=CHAT_ID, input_text="/star Christopher Nolan", session=_make_session(), user=_make_user()))
        history_service.add_to_history.assert_called()

    def test_star_empty_result_sends_fallback(self, monkeypatch):
        sent_texts = []
        async def fake_send_message(chat_id, text, **kwargs):
            sent_texts.append(text)
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", fake_send_message)
        monkeypatch.setattr(tg, "show_typing", AsyncMock())
        from services.container import discovery_service
        monkeypatch.setattr(discovery_service, "get_star_movies", AsyncMock(return_value=[]))
        from handlers.discovery_handlers import handle_star
        _run(handle_star(chat_id=CHAT_ID, input_text="/star Xyzabc NotARealPerson", session=_make_session(), user=_make_user()))
        assert any("couldn't find" in t or "Sorry" in t for t in sent_texts)

    def test_star_service_exception_sends_fallback(self, monkeypatch):
        sent_texts = []
        async def fake_send_message(chat_id, text, **kwargs):
            sent_texts.append(text)
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", fake_send_message)
        monkeypatch.setattr(tg, "show_typing", AsyncMock())
        from services.container import discovery_service
        monkeypatch.setattr(discovery_service, "get_star_movies", AsyncMock(side_effect=RuntimeError("API exploded")))
        from handlers.discovery_handlers import handle_star
        _run(handle_star(chat_id=CHAT_ID, input_text="/star Meryl Streep", session=_make_session(), user=_make_user()))
        assert any("couldn't find" in t or "Sorry" in t for t in sent_texts)

    def test_share_empty_recs_sends_fallback(self, monkeypatch):
        sent_texts = []
        async def fake_send_message(chat_id, text, **kwargs):
            sent_texts.append(text)
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", fake_send_message)
        from services.container import session_service
        from models.domain import SessionModel
        monkeypatch.setattr(session_service, "get_session", MagicMock(return_value=SessionModel(chat_id=CHAT_ID, last_recs_json="[]")))
        from handlers.discovery_handlers import handle_share
        _run(handle_share(chat_id=CHAT_ID, input_text="/share", session=_make_session()))
        assert any("Nothing to share" in t or "no" in t.lower() for t in sent_texts)

    def test_share_with_recs_builds_card(self, monkeypatch):
        sent_texts = []
        async def fake_send_message(chat_id, text, **kwargs):
            sent_texts.append(text)
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", fake_send_message)
        recs = [{"title": "Inception", "year": "2010", "rating": 8.8,
                 "genres": "Sci-Fi", "reason": "Classic", "streaming": "Netflix"}]
        from services.container import session_service
        from models.domain import SessionModel
        monkeypatch.setattr(session_service, "get_session", MagicMock(
            return_value=SessionModel(chat_id=CHAT_ID, last_recs_json=json.dumps(recs))))
        from handlers.discovery_handlers import handle_share
        _run(handle_share(chat_id=CHAT_ID, input_text="/share", session=_make_session()))
        assert any("Inception" in t for t in sent_texts)
        assert any("CineMate" in t for t in sent_texts)

    def test_share_card_caps_at_5_items(self):
        from handlers.discovery_handlers import _build_share_card
        recs = [{"title": f"Movie {i}", "year": "2020", "rating": 7.0,
                 "genres": "Drama", "reason": "Good", "streaming": "Netflix"} for i in range(10)]
        card = _build_share_card(recs)
        assert "Movie 6" not in card

    def test_share_card_skips_na_streaming(self):
        from handlers.discovery_handlers import _build_share_card
        recs = [{"title": "Test", "year": "2020", "rating": 7.0,
                 "genres": "Drama", "reason": "OK", "streaming": "N/A"}]
        card = _build_share_card(recs)
        assert "N/A" not in card

    def test_share_corrupt_session_json_sends_fallback(self, monkeypatch):
        sent_texts = []
        async def fake_send_message(chat_id, text, **kwargs):
            sent_texts.append(text)
        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message", fake_send_message)
        from services.container import session_service
        from models.domain import SessionModel
        monkeypatch.setattr(session_service, "get_session", MagicMock(
            return_value=SessionModel(chat_id=CHAT_ID, last_recs_json="NOT_VALID_JSON{{{{")))
        from handlers.discovery_handlers import handle_share
        _run(handle_share(chat_id=CHAT_ID, input_text="/share", session=_make_session()))
        assert len(sent_texts) >= 1


# ============================================================
# SECTION 8: Provider Health & Semantic Routing
# ============================================================

class TestProviderHealth:

    def _repo(self):
        from repositories.admin_repository import AdminRepository
        return AdminRepository()

    def test_healthy_provider_closed(self):
        from services.health_service import HealthService
        assert HealthService(admin_repo=self._repo()).is_healthy("omdb") is True

    def test_manual_disable_returns_unhealthy(self):
        from services.health_service import HealthService
        repo = self._repo()
        repo.set_config("provider.omdb.enabled", "false")
        assert HealthService(admin_repo=repo).is_healthy("omdb") is False

    def test_manual_enable_restores_health(self):
        from services.health_service import HealthService
        repo = self._repo()
        repo.set_config("provider.omdb.enabled", "false")
        repo.set_config("provider.omdb.enabled", "true")
        assert HealthService(admin_repo=repo).is_healthy("omdb") is True

    def test_three_failures_open_circuit(self):
        from services.health_service import HealthService, FAILURE_THRESHOLD
        repo = self._repo()
        hs = HealthService(admin_repo=repo)
        for _ in range(FAILURE_THRESHOLD):
            hs.report_failure("perplexity")
        assert hs.is_healthy("perplexity") is False

    def test_success_after_failures_closes_circuit(self):
        from services.health_service import HealthService, FAILURE_THRESHOLD
        repo = self._repo()
        hs = HealthService(admin_repo=repo)
        for _ in range(FAILURE_THRESHOLD):
            hs.report_failure("omdb")
        hs.report_success("omdb")
        assert hs.is_healthy("omdb") is True

    def test_provider_status_structure(self):
        from services.health_service import HealthService
        status = HealthService(admin_repo=self._repo()).get_provider_status("omdb")
        for key in ("state", "failure_count", "daily_calls_today", "provider"):
            assert key in status

    def test_daily_budget_exceeded_returns_unhealthy(self):
        from services.health_service import HealthService, DAILY_BUDGET
        from datetime import datetime, timezone
        repo = self._repo()
        hs = HealthService(admin_repo=repo)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        budget = DAILY_BUDGET.get("omdb", 1000)
        repo.set_config(f"provider.omdb.calls.{today}", str(budget))
        assert hs.is_healthy("omdb") is False

    def test_daily_call_increment(self):
        from services.health_service import HealthService
        from datetime import datetime, timezone
        repo = self._repo()
        hs = HealthService(admin_repo=repo)
        hs.increment_daily_calls("watchmode")
        hs.increment_daily_calls("watchmode")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        val = int(repo.get_config(f"provider.watchmode.calls.{today}") or 0)
        assert val == 2


class TestSemanticService:

    def test_unknown_for_short_text(self):
        from services.semantic_service import SemanticService
        assert _run(SemanticService().classify_intent("hi")) == "unknown"

    def test_unknown_when_provider_unhealthy(self, monkeypatch):
        from services.health_service import HealthService
        from repositories.admin_repository import AdminRepository
        from services.semantic_service import SemanticService
        repo = AdminRepository()
        hs = HealthService(admin_repo=repo)
        repo.set_config("provider.perplexity.enabled", "false")
        assert _run(SemanticService(health_service=hs).classify_intent("what movies are trending this week")) == "unknown"

    def test_caches_result(self, monkeypatch):
        from services.semantic_service import SemanticService
        n = {"count": 0}
        async def fake_llm(self_inner, text):
            n["count"] += 1
            return "trending"
        monkeypatch.setattr(SemanticService, "_call_llm", fake_llm)
        svc = SemanticService()
        text = "what is popular at cinemas right now x7z"
        assert _run(svc.classify_intent(text)) == "trending"
        assert _run(svc.classify_intent(text)) == "trending"
        assert n["count"] == 1

    def test_rejects_invalid_llm_label(self, monkeypatch):
        from services.semantic_service import SemanticService
        async def fake_llm(self_inner, text):
            return "delete_everything"
        monkeypatch.setattr(SemanticService, "_call_llm", fake_llm)
        assert _run(SemanticService().classify_intent("do something dangerous")) == "unknown"

    def test_handles_llm_exception_gracefully(self, monkeypatch):
        from services.semantic_service import SemanticService
        async def fake_llm(self_inner, text):
            raise RuntimeError("Perplexity API is down")
        monkeypatch.setattr(SemanticService, "_call_llm", fake_llm)
        assert _run(SemanticService().classify_intent("recommend me something dark")) == "unknown"

    def test_all_valid_intents_accepted(self, monkeypatch):
        from services.semantic_service import SemanticService, VALID_INTENTS
        for intent in VALID_INTENTS:
            async def fake_llm(self_inner, text, _i=intent):
                return _i
            monkeypatch.setattr(SemanticService, "_call_llm", fake_llm)
            svc = SemanticService()
            result = _run(svc.classify_intent(f"unique query for {intent} {id(intent)}"))
            assert result == intent

    def test_safe_when_redis_unavailable(self, monkeypatch):
        import config.redis_cache as rc
        monkeypatch.setattr(rc, "get_redis", lambda: None)
        rc.clear_local_cache()
        from services.semantic_service import SemanticService
        async def fake_llm(self_inner, text):
            return "trending"
        monkeypatch.setattr(SemanticService, "_call_llm", fake_llm)
        assert _run(SemanticService().classify_intent("what is popular this week")) == "trending"


# ============================================================
# SECTION 9: Error Logging & UX
# ============================================================

class TestLoggingService:

    def test_log_event_success_does_not_raise(self):
        from services.logging_service import LoggingService
        LoggingService.log_event(
            chat_id=CHAT_ID, intent="movie", step="fetch_movies",
            request_id="req-log-001", provider="omdb",
            latency_ms=120, status="success",
        )

    def test_log_event_error_emits_to_error_batcher(self, monkeypatch):
        emitted = []
        from services import logging_service as ls
        monkeypatch.setattr(ls.error_batcher, "emit", lambda item: emitted.append(item))
        ls.LoggingService.log_event(
            chat_id=CHAT_ID, intent="movie", step="fetch_movies",
            request_id="req-log-002", provider="omdb",
            latency_ms=5000, status="error", error_type="ProviderTimeout",
            extra={"detail": "omdb timed out"},
        )
        assert len(emitted) == 1
        assert emitted[0]["chat_id"] == CHAT_ID
        assert emitted[0]["error_type"] == "ProviderTimeout"
        assert "request_id" in emitted[0]

    def test_log_event_slow_response_does_not_raise(self):
        from services.logging_service import LoggingService
        LoggingService.log_event(
            chat_id=CHAT_ID, intent="trending", step="api_call",
            latency_ms=3000, status="success",
        )

    def test_log_interaction_emits_to_batcher(self, monkeypatch):
        emitted = []
        from services import logging_service as ls
        monkeypatch.setattr(ls.interaction_batcher, "emit", lambda item: emitted.append(item))
        ls.LoggingService.log_interaction(
            chat_id=CHAT_ID, input_text="/trending",
            response_text="Here are today's trending movies...",
            intent="trending", latency_ms=450,
            username=USERNAME, request_id="req-log-003",
        )
        assert len(emitted) == 1
        assert emitted[0]["chat_id"] == CHAT_ID
        assert emitted[0]["intent"] == "trending"
        assert emitted[0]["username"] == USERNAME

    def test_log_interaction_truncates_long_fields(self, monkeypatch):
        emitted = []
        from services import logging_service as ls
        monkeypatch.setattr(ls.interaction_batcher, "emit", lambda item: emitted.append(item))
        ls.LoggingService.log_interaction(
            chat_id=CHAT_ID, input_text="a" * 2000,
            response_text="b" * 5000, intent="movie",
            latency_ms=100, username=USERNAME, request_id="req-log-004",
        )
        assert len(emitted[0]["input_text"]) <= 1000
        assert len(emitted[0]["bot_response"]) <= 2000

    def test_batch_logger_shutdown_graceful(self):
        from services.logging_service import BatchLogger
        bl = BatchLogger("test_table", batch_size=10, flush_interval=60)
        bl.emit({"test": "item1"})
        bl.emit({"test": "item2"})
        bl.shutdown()  # Must not raise

    def test_error_batcher_handles_supabase_error_silently(self, monkeypatch):
        from services import logging_service as ls
        with patch("config.supabase_client.is_configured", return_value=True):
            with patch("config.supabase_client.insert_rows", return_value=(None, "DB error")):
                ls.error_batcher.emit({
                    "chat_id": CHAT_ID, "error_type": "Test",
                    "error_message": "test", "workflow_step": "test",
                    "intent": "test", "request_id": "r1",
                    "raw_payload": "{}", "timestamp": "2024-01-01"
                })
                ls.error_batcher.flush()  # Must not raise


# ============================================================
# SECTION 10: Reliability — Redis / Supabase fallback
# ============================================================

class TestReliability:

    def test_redis_get_json_returns_none_when_down(self, monkeypatch):
        import config.redis_cache as rc
        monkeypatch.setattr(rc, "get_redis", lambda: None)
        rc.clear_local_cache()
        assert rc.get_json("nonexistent_key_12345") is None

    def test_redis_set_json_does_not_raise_when_down(self, monkeypatch):
        import config.redis_cache as rc
        monkeypatch.setattr(rc, "get_redis", lambda: None)
        rc.set_json("test_key", {"data": 1}, ttl=60)  # Must not raise

    def test_redis_dedup_falls_back_to_memory(self, monkeypatch):
        import config.redis_cache as rc
        monkeypatch.setattr(rc, "get_redis", lambda: None)
        assert rc.mark_processed_update("unique_update_fallback_999") is True
        assert rc.mark_processed_update("unique_update_fallback_999") is False

    def test_redis_rate_limit_user_tier_enforced_in_memory(self, monkeypatch):
        import config.redis_cache as rc
        monkeypatch.setattr(rc, "get_redis", lambda: None)
        rc.clear_local_cache()
        key = "rl_enforce_test_user_e2e"
        for _ in range(12):
            rc.is_rate_limited(key, user_tier="user")
        assert rc.is_rate_limited(key, user_tier="user") is True

    def test_redis_admin_tier_never_rate_limited(self, monkeypatch):
        import config.redis_cache as rc
        monkeypatch.setattr(rc, "get_redis", lambda: None)
        rc.clear_local_cache()
        key = "rl_admin_e2e"
        for _ in range(100):
            result = rc.is_rate_limited(key, user_tier="admin")
        assert result is False

    def test_history_service_works_without_supabase(self):
        from services.movie_service import HistoryService
        from repositories.history_repository import HistoryRepository
        from models.domain import MovieModel
        repo = HistoryRepository()
        svc = HistoryService(history_repo=repo)
        movie = MovieModel(
            title="No-DB Movie", year="2022", imdb_id="tt_nodb_01",
            rating=6.5, genres="Horror", reason="Scary good", streaming="Hulu",
        )
        svc.add_to_history(CHAT_ID, movie)
        assert len(svc.get_history(CHAT_ID)) >= 1

    def test_watchlist_service_works_without_supabase(self):
        from services.movie_service import WatchlistService
        from repositories.watchlist_repository import WatchlistRepository
        from models.domain import MovieModel
        repo = WatchlistRepository()
        svc = WatchlistService(watchlist_repo=repo)
        movie = MovieModel(
            title="No-DB Watchlist", year="2021", imdb_id="tt_nodb_wl_01",
            rating=7.0, genres="Comedy", reason="Fun", streaming="Disney+",
        )
        svc.add_to_watchlist(CHAT_ID, movie)
        assert len(svc.get_watchlist(CHAT_ID)) >= 1

    def test_admin_repo_config_in_memory(self):
        from repositories.admin_repository import AdminRepository
        repo = AdminRepository()
        repo.set_config("test.key.e2e", "hello")
        assert repo.get_config("test.key.e2e") == "hello"

    def test_admin_repo_get_nonexistent_returns_none(self):
        from repositories.admin_repository import AdminRepository
        assert AdminRepository().get_config("does.not.exist.xyz") is None

    def test_queue_inline_mode_executes_job(self, monkeypatch):
        import os
        monkeypatch.setenv("CINEMATE_INLINE_JOBS", "1")
        import config.redis_cache as rc
        monkeypatch.setattr(rc, "get_redis", lambda: None)
        executed = {}
        async def fake_run_intent_job(**kwargs):
            executed["ok"] = True
        monkeypatch.setattr("services.worker_service.run_intent_job", fake_run_intent_job)
        from services.queue_service import enqueue_job
        enqueue_job(
            "services.worker_service.run_intent_job",
            intent="help", chat_id=CHAT_ID, username=USERNAME,
            input_text="/help", session=_make_session(), user=_make_user(),
            request_id="req-queue-inline",
        )
        assert executed.get("ok") is True
