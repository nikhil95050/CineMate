import os

import pytest

from services.queue_service import enqueue_job


# Dotted path to the worker entry function
RUN_INTENT_FUNC = "services.worker_service.run_intent_job"


def test_enqueue_job_inline_executes_function(monkeypatch):
    """When CINEMATE_INLINE_JOBS=1, enqueue_job should run the function directly."""
    calls = {}

    def fake_run_intent_job(**kwargs):  # type: ignore[override]
        calls["kwargs"] = kwargs

    monkeypatch.setenv("CINEMATE_INLINE_JOBS", "1")
    monkeypatch.setattr(
        "services.queue_service._resolve_callable", lambda name: fake_run_intent_job
    )

    enqueue_job(RUN_INTENT_FUNC, intent="test", chat_id="123")

    assert "kwargs" in calls
    assert calls["kwargs"]["intent"] == "test"
    assert calls["kwargs"]["chat_id"] == "123"


def test_enqueue_job_falls_back_to_inline_when_redis_missing(monkeypatch):
    """If Redis is not configured, enqueue_job should still execute inline."""
    calls = {}

    def fake_run_intent_job(**kwargs):  # type: ignore[override]
        calls["kwargs"] = kwargs

    monkeypatch.delenv("CINEMATE_INLINE_JOBS", raising=False)
    monkeypatch.setattr("services.queue_service._get_queue", lambda: None)
    monkeypatch.setattr(
        "services.queue_service._resolve_callable", lambda name: fake_run_intent_job
    )

    enqueue_job(RUN_INTENT_FUNC, intent="test2", chat_id="456")

    assert "kwargs" in calls
    assert calls["kwargs"]["intent"] == "test2"
    assert calls["kwargs"]["chat_id"] == "456"


@pytest.mark.integration
def test_rq_worker_path_if_redis_available(monkeypatch):
    """Basic integration: if Redis is configured AND RQ is importable,
    enqueue_job should push to the queue and a SimpleWorker should process it.

    This test is marked as 'integration' and will be skipped automatically when
    Redis is not available or RQ cannot be imported on this platform.
    """
    from config.redis_cache import get_redis

    redis_conn = get_redis()
    if not redis_conn:
        pytest.skip("Redis not configured; skipping RQ integration test")

    # Import RQ lazily and defensively, to avoid platform-specific failures
    try:
        from rq import Queue
        from rq.worker import SimpleWorker
    except Exception:
        pytest.skip("RQ not supported on this platform/Python (missing fork context)")

    queue_name = os.environ.get("CINEMATE_QUEUE_NAME", "cinemate_intent_jobs")
    q = Queue(queue_name, connection=redis_conn)

    # Clear queue before test
    while q.count:  # type: ignore[attr-defined]
        q.dequeue()

    enqueue_job(RUN_INTENT_FUNC, intent="integration", chat_id="999")

    worker = SimpleWorker([q], connection=redis_conn)
    worker.work(burst=True)
