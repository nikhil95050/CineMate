from fastapi.testclient import TestClient

from main import app
from services.queue_service import enqueue_job


client = TestClient(app)


def test_webhook_enqueues_first_time(monkeypatch):
    calls = {}

    def fake_enqueue(func_name, **kwargs):  # type: ignore[override]
        calls["func_name"] = func_name
        calls["kwargs"] = kwargs

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN")

    # Re-import app to pick up updated env var
    from importlib import reload
    import main as main_module

    reload(main_module)
    test_client = TestClient(main_module.app)

    # --- Stub all Redis helpers so the test never depends on real Redis state ---
    from config import redis_cache
    monkeypatch.setattr(redis_cache, "mark_processed_update", lambda update_id: True)
    monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)
    # Stub enqueue so we capture the call without touching the queue
    monkeypatch.setattr("services.enqueue_job", fake_enqueue)

    update = {
        "update_id": 100,
        "message": {
            "message_id": 1,
            "date": 1700000000,
            "text": "/start",
            "chat": {"id": 999},
            "from": {"username": "tester"},
        },
    }

    resp = test_client.post("/webhook/TESTTOKEN", json=update)
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    assert calls["func_name"] == "services.worker_service.run_intent_job"
    assert calls["kwargs"]["intent"] == "start"
    assert calls["kwargs"]["chat_id"] == "999"


def test_webhook_skips_duplicate(monkeypatch):
    from config import redis_cache

    # Track which update_ids have been processed in this test
    seen = set()

    def fake_mark_processed(update_id: str):  # type: ignore[override]
        if update_id in seen:
            return False
        seen.add(update_id)
        return True

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN2")
    from importlib import reload
    import main as main_module

    reload(main_module)
    test_client = TestClient(main_module.app)

    monkeypatch.setattr(redis_cache, "mark_processed_update", fake_mark_processed)
    # Always allow — we're testing dedup, not rate limiting
    monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": False)

    enqueue_calls = {"count": 0}

    def fake_enqueue(func_name, **kwargs):  # type: ignore[override]
        enqueue_calls["count"] += 1

    monkeypatch.setattr("services.enqueue_job", fake_enqueue)

    update = {
        "update_id": 200,
        "message": {
            "message_id": 2,
            "date": 1700000000,
            "text": "/start",
            "chat": {"id": 777},
            "from": {"username": "tester"},
        },
    }

    # First call should enqueue
    resp1 = test_client.post("/webhook/TESTTOKEN2", json=update)
    assert resp1.status_code == 200
    assert enqueue_calls["count"] == 1

    # Second call with same update_id must be silently dropped
    resp2 = test_client.post("/webhook/TESTTOKEN2", json=update)
    assert resp2.status_code == 200
    assert enqueue_calls["count"] == 1


def test_webhook_rate_limited(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TESTTOKEN3")
    from importlib import reload
    import main as main_module

    reload(main_module)
    test_client = TestClient(main_module.app)

    from config import redis_cache

    # Dedup always passes — we're testing rate limiting here
    monkeypatch.setattr(redis_cache, "mark_processed_update", lambda update_id: True)
    # Rate limiter always blocks
    monkeypatch.setattr(redis_cache, "is_rate_limited", lambda key, user_tier="user": True)

    enqueue_calls = {"count": 0}

    def fake_enqueue(func_name, **kwargs):  # type: ignore[override]
        enqueue_calls["count"] += 1

    monkeypatch.setattr("services.enqueue_job", fake_enqueue)

    # Stub Telegram so no real HTTP calls go out
    import clients.telegram_helpers as tg

    async def fake_send_message(chat_id, text):  # type: ignore[override]
        return None

    monkeypatch.setattr(tg, "send_message_safely", fake_send_message)

    update = {
        "update_id": 300,
        "message": {
            "message_id": 3,
            "date": 1700000000,
            "text": "/start",
            "chat": {"id": 555},
            "from": {"username": "tester"},
        },
    }

    resp = test_client.post("/webhook/TESTTOKEN3", json=update)
    assert resp.status_code == 200
    assert enqueue_calls["count"] == 0
