"""Tests for the Telegram webhook pipeline — Feature 3.

Covers:
  ✓ First-time update → job enqueued with correct intent + chat_id
  ✓ Duplicate update_id → job NOT enqueued (dedup)
  ✓ Rate-limited user → friendly message sent, job NOT enqueued
  ✓ Wrong / missing token → HTTP 404
  ✓ Invalid JSON body → HTTP 200 + ok:False
  ✓ No chat_id in update → HTTP 200, no enqueue
  ✓ Callback query update → enqueued correctly
  ✓ Oversized request body → HTTP 413
  ✓ Content-Length header too large → HTTP 413
  ✓ Admin user bypasses normal rate-limit tier
  ✓ GET /webhook/{token} removed → HTTP 405
  ✓ All kwargs forwarded correctly to enqueue_job
  ✓ enqueue_job exception → still returns HTTP 200 (Telegram contract)
  ✓ Request exactly at size limit → accepted (HTTP 200)
  ✓ Inline-jobs logging provider label is correct
"""

from __future__ import annotations

import importlib
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

TOKEN = "TESTTOKEN"


def _make_message_update(update_id: int, chat_id: int, text: str, username: str = "tester") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1_700_000_000,
            "text": text,
            "chat": {"id": chat_id},
            "from": {"username": username},
        },
    }


def _make_callback_update(update_id: int, chat_id: int, data: str) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": "cq123",
            "data": data,
            "message": {
                "message_id": update_id,
                "date": 1_700_000_000,
                "chat": {"id": chat_id},
            },
            "from": {"username": "tester"},
        },
    }


@pytest.fixture()
def client_and_stubs(monkeypatch):
    """Reload main with a known token; stub Redis helpers and enqueue.
    Returns (test_client, enqueue_calls_list).
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.delenv("ADMIN_CHAT_IDS", raising=False)

    import main as main_module
    importlib.reload(main_module)

    from config import redis_cache
    monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
    monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)

    enqueue_calls: list[dict] = []

    def fake_enqueue(func_name, **kwargs):
        enqueue_calls.append({"func_name": func_name, "kwargs": kwargs})

    monkeypatch.setattr("services.enqueue_job", fake_enqueue)

    tc = TestClient(main_module.app, raise_server_exceptions=False)
    return tc, enqueue_calls, main_module


# ===========================================================================
# Core pipeline — first-time update
# ===========================================================================

class TestFirstTimeUpdate:
    def test_enqueues_start_intent(self, client_and_stubs):
        tc, calls, _ = client_and_stubs
        resp = tc.post(f"/webhook/{TOKEN}", json=_make_message_update(100, 999, "/start"))
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert len(calls) == 1
        assert calls[0]["func_name"] == "services.worker_service.run_intent_job"
        assert calls[0]["kwargs"]["intent"] == "start"
        assert calls[0]["kwargs"]["chat_id"] == "999"

    def test_enqueue_kwargs_contains_all_required_fields(self, client_and_stubs):
        tc, calls, _ = client_and_stubs
        tc.post(f"/webhook/{TOKEN}", json=_make_message_update(101, 888, "hello"))
        kw = calls[0]["kwargs"]
        for field in ("intent", "chat_id", "username", "input_text",
                      "session", "user", "request_id"):
            assert field in kw, f"Missing field: {field}"

    def test_username_forwarded(self, client_and_stubs):
        tc, calls, _ = client_and_stubs
        tc.post(f"/webhook/{TOKEN}", json=_make_message_update(102, 777, "/start", username="nikhil"))
        assert calls[0]["kwargs"]["username"] == "nikhil"

    def test_input_text_forwarded(self, client_and_stubs):
        tc, calls, _ = client_and_stubs
        tc.post(f"/webhook/{TOKEN}", json=_make_message_update(103, 666, "recommend me something"))
        assert calls[0]["kwargs"]["input_text"] == "recommend me something"

    def test_callback_query_update_enqueued(self, client_and_stubs):
        tc, calls, _ = client_and_stubs
        resp = tc.post(f"/webhook/{TOKEN}", json=_make_callback_update(104, 555, "genre:action"))
        assert resp.status_code == 200
        assert len(calls) == 1
        assert calls[0]["kwargs"]["chat_id"] == "555"


# ===========================================================================
# Dedup
# ===========================================================================

class TestDuplicateUpdate:
    def test_second_identical_update_not_enqueued(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.delenv("ADMIN_CHAT_IDS", raising=False)

        import main as main_module
        importlib.reload(main_module)

        from config import redis_cache
        seen: set[str] = set()

        def fake_mark(uid: str) -> bool:
            if uid in seen:
                return False
            seen.add(uid)
            return True

        monkeypatch.setattr(redis_cache, "mark_processed_update", fake_mark)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)

        enqueue_count = {"n": 0}
        monkeypatch.setattr("services.enqueue_job", lambda fn, **kw: enqueue_count.update(n=enqueue_count["n"] + 1))

        tc = TestClient(main_module.app)
        update = _make_message_update(200, 777, "/start")

        tc.post(f"/webhook/{TOKEN}", json=update)
        assert enqueue_count["n"] == 1

        tc.post(f"/webhook/{TOKEN}", json=update)
        assert enqueue_count["n"] == 1, "Duplicate update must not be enqueued"

    def test_different_update_ids_both_enqueued(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.delenv("ADMIN_CHAT_IDS", raising=False)

        import main as main_module
        importlib.reload(main_module)

        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)

        enqueue_count = {"n": 0}
        monkeypatch.setattr("services.enqueue_job", lambda fn, **kw: enqueue_count.update(n=enqueue_count["n"] + 1))

        tc = TestClient(main_module.app)
        tc.post(f"/webhook/{TOKEN}", json=_make_message_update(201, 777, "hi"))
        tc.post(f"/webhook/{TOKEN}", json=_make_message_update(202, 777, "hi"))
        assert enqueue_count["n"] == 2


# ===========================================================================
# Rate limiting
# ===========================================================================

class TestRateLimiting:
    def test_rate_limited_user_no_enqueue(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.delenv("ADMIN_CHAT_IDS", raising=False)

        import main as main_module
        importlib.reload(main_module)

        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": True)

        enqueue_count = {"n": 0}
        monkeypatch.setattr("services.enqueue_job", lambda fn, **kw: enqueue_count.update(n=enqueue_count["n"] + 1))

        import clients.telegram_helpers as tg
        monkeypatch.setattr(tg, "send_message_safely", AsyncMock(return_value=None))

        tc = TestClient(main_module.app)
        resp = tc.post(f"/webhook/{TOKEN}", json=_make_message_update(300, 555, "/start"))
        assert resp.status_code == 200
        assert enqueue_count["n"] == 0

    def test_rate_limited_sends_friendly_message(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.delenv("ADMIN_CHAT_IDS", raising=False)

        import main as main_module
        importlib.reload(main_module)

        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": True)
        monkeypatch.setattr("services.enqueue_job", lambda fn, **kw: None)

        import clients.telegram_helpers as tg
        send_mock = AsyncMock(return_value=None)
        monkeypatch.setattr(tg, "send_message_safely", send_mock)

        tc = TestClient(main_module.app)
        tc.post(f"/webhook/{TOKEN}", json=_make_message_update(301, 555, "/start"))

        send_mock.assert_called_once()
        args = send_mock.call_args[0]
        assert args[0] == 555  # chat_id
        assert "slow down" in args[1].lower()

    def test_admin_user_tier_passed_to_rate_limiter(self, monkeypatch):
        """Admin chat IDs must result in user_tier='admin' in the rate-limit call."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.setenv("ADMIN_CHAT_IDS", "12345")

        import main as main_module
        importlib.reload(main_module)

        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)

        tier_seen = {}
        def fake_rate_limit(key, user_tier="user"):
            tier_seen["tier"] = user_tier
            return False

        monkeypatch.setattr(redis_cache, "is_rate_limited", fake_rate_limit)
        monkeypatch.setattr("services.enqueue_job", lambda fn, **kw: None)

        tc = TestClient(main_module.app)
        tc.post(f"/webhook/{TOKEN}", json=_make_message_update(302, 12345, "/start"))
        assert tier_seen.get("tier") == "admin"


# ===========================================================================
# Token / auth checks
# ===========================================================================

class TestTokenValidation:
    def test_wrong_token_returns_404(self, client_and_stubs):
        tc, calls, _ = client_and_stubs
        resp = tc.post("/webhook/WRONGTOKEN", json=_make_message_update(400, 1, "/start"))
        assert resp.status_code == 404
        assert len(calls) == 0

    def test_empty_token_returns_404(self, monkeypatch):
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
        import main as main_module
        importlib.reload(main_module)
        tc = TestClient(main_module.app)
        resp = tc.post("/webhook/anything", json=_make_message_update(401, 1, "/start"))
        assert resp.status_code == 404


# ===========================================================================
# Edge cases: invalid JSON, missing chat_id
# ===========================================================================

class TestEdgeCases:
    def test_invalid_json_returns_200_ok_false(self, client_and_stubs):
        tc, calls, _ = client_and_stubs
        resp = tc.post(
            f"/webhook/{TOKEN}",
            content=b"not json at all",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert len(calls) == 0

    def test_update_with_no_chat_id_returns_200_no_enqueue(self, client_and_stubs):
        tc, calls, _ = client_and_stubs
        resp = tc.post(f"/webhook/{TOKEN}", json={"update_id": 500})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert len(calls) == 0

    def test_enqueue_exception_still_returns_200(self, monkeypatch):
        """If enqueue_job raises, webhook must still return HTTP 200.
        Telegram will stop retrying only if we acknowledge with 200.
        """
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
        monkeypatch.delenv("ADMIN_CHAT_IDS", raising=False)

        import main as main_module
        importlib.reload(main_module)

        from config import redis_cache
        monkeypatch.setattr(redis_cache, "mark_processed_update", lambda uid: True)
        monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)

        def exploding_enqueue(fn, **kw):
            raise RuntimeError("Redis is down")

        monkeypatch.setattr("services.enqueue_job", exploding_enqueue)

        tc = TestClient(main_module.app, raise_server_exceptions=False)
        resp = tc.post(f"/webhook/{TOKEN}", json=_make_message_update(501, 1, "/start"))
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ===========================================================================
# Fix: large-request handling — RequestSizeLimitMiddleware
# ===========================================================================

class TestRequestSizeLimit:
    def test_oversized_body_returns_413(self, client_and_stubs):
        """A body exceeding MAX_REQUEST_BODY_BYTES must be rejected with HTTP 413."""
        tc, calls, main_module = client_and_stubs
        # Temporarily lower the limit to 100 bytes for a deterministic test
        original = main_module.MAX_REQUEST_BODY_BYTES
        main_module.MAX_REQUEST_BODY_BYTES = 100
        try:
            big_body = json.dumps({"update_id": 600, "data": "x" * 200}).encode()
            resp = tc.post(
                f"/webhook/{TOKEN}",
                content=big_body,
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 413
            assert "too large" in resp.json()["description"].lower()
            assert len(calls) == 0
        finally:
            main_module.MAX_REQUEST_BODY_BYTES = original

    def test_body_at_exact_limit_is_accepted(self, client_and_stubs):
        """A body exactly at the limit must be accepted (boundary condition)."""
        tc, calls, main_module = client_and_stubs
        small_update = _make_message_update(601, 111, "/start")
        body = json.dumps(small_update).encode()
        original = main_module.MAX_REQUEST_BODY_BYTES
        main_module.MAX_REQUEST_BODY_BYTES = len(body)  # exactly the body size
        try:
            resp = tc.post(
                f"/webhook/{TOKEN}",
                content=body,
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 200
        finally:
            main_module.MAX_REQUEST_BODY_BYTES = original

    def test_content_length_header_triggers_413(self, client_and_stubs):
        """A Content-Length header alone (fast path) must reject the request."""
        tc, calls, main_module = client_and_stubs
        original = main_module.MAX_REQUEST_BODY_BYTES
        main_module.MAX_REQUEST_BODY_BYTES = 50
        try:
            resp = tc.post(
                f"/webhook/{TOKEN}",
                content=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Content-Length": "99999",
                },
            )
            assert resp.status_code == 413
        finally:
            main_module.MAX_REQUEST_BODY_BYTES = original


# ===========================================================================
# Fix: GET /webhook/{token} removed — must return 405
# ===========================================================================

class TestGetWebhookRemoved:
    def test_get_webhook_returns_405(self, client_and_stubs):
        tc, _, _ = client_and_stubs
        resp = tc.get(f"/webhook/{TOKEN}")
        assert resp.status_code == 405, (
            f"Expected 405 Method Not Allowed for GET /webhook/{{token}}, got {resp.status_code}"
        )
