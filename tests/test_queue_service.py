"""Tests for services/queue_service.py — Feature 2: Job Queue Framework (RQ).

Covers all four issues identified in the spec review:
  Fix 1 — worker_runner.py deprecation stub (tested separately via import).
  Fix 2 — Comprehensive unit + integration tests (this file).
  Fix 3 — Inline-in-production warning guard.
  Fix 4 — enqueue_call → queue.enqueue migration.

Windows note
------------
RQ imports rq.scheduler at module level, which calls
  multiprocessing.get_context('fork')
Windows only supports 'spawn' and 'forkserver', so importing rq on Windows
raises ValueError: cannot find context for 'fork'.

Any test that would trigger a real `from rq import ...` is wrapped in a
try/except and skipped automatically when running on Windows.
"""

from __future__ import annotations

import os
import sys
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from services.queue_service import (
    enqueue_job,
    _get_queue,
    _resolve_callable,
    _run_inline_async,
    _schedule_async_task,
    _warn_if_inline_in_production,
)

RUN_INTENT_FUNC = "services.worker_service.run_intent_job"

# True when the current platform cannot import rq (Windows — no fork context)
_RQ_UNAVAILABLE: bool
try:
    import rq as _rq  # noqa: F401
    _RQ_UNAVAILABLE = False
except (ValueError, ImportError, OSError):
    _RQ_UNAVAILABLE = True

rq_skip = pytest.mark.skipif(
    _RQ_UNAVAILABLE,
    reason="RQ cannot be imported on this platform (no 'fork' multiprocessing context — Windows)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_func(calls: dict):
    def fake(**kwargs):
        calls["kwargs"] = kwargs
    return fake


# ===========================================================================
# Fix 4 — enqueue uses queue.enqueue(func, **kwargs), NOT enqueue_call
# ===========================================================================

class TestEnqueueUsesModernAPI:
    """queue.enqueue(func, **kwargs) must be used — not the deprecated enqueue_call."""

    def test_enqueue_calls_queue_enqueue_not_enqueue_call(self, monkeypatch):
        calls = {}
        fake_func = _make_fake_func(calls)
        fake_job = MagicMock()
        fake_job.id = "job-abc"

        fake_queue = MagicMock()
        fake_queue.enqueue.return_value = fake_job

        monkeypatch.delenv("CINEMATE_INLINE_JOBS", raising=False)
        monkeypatch.setattr("services.queue_service._get_queue", lambda: fake_queue)
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: fake_func)

        enqueue_job(RUN_INTENT_FUNC, intent="watch", chat_id="1")

        fake_queue.enqueue.assert_called_once_with(fake_func, intent="watch", chat_id="1")
        fake_queue.enqueue_call.assert_not_called()

    def test_enqueue_returns_job_with_correct_kwargs(self, monkeypatch):
        fake_func = MagicMock()
        fake_job = MagicMock()
        fake_job.id = "job-xyz"

        fake_queue = MagicMock()
        fake_queue.enqueue.return_value = fake_job

        monkeypatch.delenv("CINEMATE_INLINE_JOBS", raising=False)
        monkeypatch.setattr("services.queue_service._get_queue", lambda: fake_queue)
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: fake_func)

        enqueue_job(RUN_INTENT_FUNC, chat_id="99", intent="rate")

        _, kwargs = fake_queue.enqueue.call_args
        assert kwargs["chat_id"] == "99"
        assert kwargs["intent"] == "rate"


# ===========================================================================
# Fix 3 — Inline-in-production warning
# ===========================================================================

class TestInlineProductionWarning:
    """CINEMATE_INLINE_JOBS in production must trigger a warning log."""

    def test_warning_emitted_when_inline_and_prod(self, monkeypatch):
        import services.queue_service as qs
        monkeypatch.setattr(qs, "_INLINE_PROD_WARNED", False)
        monkeypatch.setenv("CINEMATE_ENV", "production")

        with patch.object(qs.logger, "warning") as mock_warn:
            qs._warn_if_inline_in_production()

        assert mock_warn.called
        msg = mock_warn.call_args[0][0]
        assert "CINEMATE_INLINE_JOBS" in msg
        assert "PRODUCTION" in msg

    def test_warning_not_emitted_in_dev(self, monkeypatch):
        import services.queue_service as qs
        monkeypatch.setattr(qs, "_INLINE_PROD_WARNED", False)
        monkeypatch.setenv("CINEMATE_ENV", "development")

        with patch.object(qs.logger, "warning") as mock_warn:
            qs._warn_if_inline_in_production()

        mock_warn.assert_not_called()

    def test_warning_emitted_only_once(self, monkeypatch):
        import services.queue_service as qs
        monkeypatch.setattr(qs, "_INLINE_PROD_WARNED", False)
        monkeypatch.setenv("CINEMATE_ENV", "prod")

        with patch.object(qs.logger, "warning") as mock_warn:
            qs._warn_if_inline_in_production()
            qs._warn_if_inline_in_production()
            qs._warn_if_inline_in_production()

        assert mock_warn.call_count == 1

    def test_warning_not_emitted_when_env_missing(self, monkeypatch):
        import services.queue_service as qs
        monkeypatch.setattr(qs, "_INLINE_PROD_WARNED", False)
        monkeypatch.delenv("CINEMATE_ENV", raising=False)

        with patch.object(qs.logger, "warning") as mock_warn:
            qs._warn_if_inline_in_production()

        mock_warn.assert_not_called()

    def test_enqueue_job_triggers_prod_warning_when_inline_and_prod(self, monkeypatch):
        import services.queue_service as qs
        monkeypatch.setattr(qs, "_INLINE_PROD_WARNED", False)
        monkeypatch.setenv("CINEMATE_INLINE_JOBS", "1")
        monkeypatch.setenv("CINEMATE_ENV", "production")

        calls = {}
        fake_func = _make_fake_func(calls)
        monkeypatch.setattr(qs, "_resolve_callable", lambda _: fake_func)

        with patch.object(qs.logger, "warning") as mock_warn:
            enqueue_job(RUN_INTENT_FUNC, chat_id="7")

        assert any("CINEMATE_INLINE_JOBS" in str(c) for c in mock_warn.call_args_list)


# ===========================================================================
# Fix 2 — Unit tests: inline dispatch, fallback, payloads
# ===========================================================================

class TestEnqueueInlineMode:
    """CINEMATE_INLINE_JOBS=1 must execute the function directly."""

    def test_inline_executes_function(self, monkeypatch):
        calls = {}
        fake_func = _make_fake_func(calls)
        monkeypatch.setenv("CINEMATE_INLINE_JOBS", "1")
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: fake_func)

        enqueue_job(RUN_INTENT_FUNC, intent="test", chat_id="123")

        assert calls["kwargs"] == {"intent": "test", "chat_id": "123"}

    def test_inline_true_string(self, monkeypatch):
        calls = {}
        monkeypatch.setenv("CINEMATE_INLINE_JOBS", "true")
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: _make_fake_func(calls))
        enqueue_job(RUN_INTENT_FUNC, chat_id="1")
        assert "kwargs" in calls

    def test_inline_yes_string(self, monkeypatch):
        calls = {}
        monkeypatch.setenv("CINEMATE_INLINE_JOBS", "yes")
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: _make_fake_func(calls))
        enqueue_job(RUN_INTENT_FUNC, chat_id="2")
        assert "kwargs" in calls

    def test_inline_on_string(self, monkeypatch):
        calls = {}
        monkeypatch.setenv("CINEMATE_INLINE_JOBS", "on")
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: _make_fake_func(calls))
        enqueue_job(RUN_INTENT_FUNC, chat_id="3")
        assert "kwargs" in calls

    def test_inline_0_falls_back_via_get_queue(self, monkeypatch):
        """CINEMATE_INLINE_JOBS=0 must NOT trigger inline_mode path;
        falls back through the _get_queue=None fallback path instead."""
        calls = {}
        monkeypatch.setenv("CINEMATE_INLINE_JOBS", "0")
        monkeypatch.setattr("services.queue_service._get_queue", lambda: None)
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: _make_fake_func(calls))
        enqueue_job(RUN_INTENT_FUNC, chat_id="4")
        assert "kwargs" in calls

    def test_inline_preserves_all_kwargs(self, monkeypatch):
        calls = {}
        monkeypatch.setenv("CINEMATE_INLINE_JOBS", "1")
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: _make_fake_func(calls))

        enqueue_job(RUN_INTENT_FUNC, chat_id="42", intent="recommend", extra="data")

        assert calls["kwargs"]["chat_id"] == "42"
        assert calls["kwargs"]["intent"] == "recommend"
        assert calls["kwargs"]["extra"] == "data"


class TestEnqueueFallbackToInlineWhenRedisUnavailable:
    """When _get_queue() returns None, jobs must still execute via inline fallback."""

    def test_fallback_executes_function(self, monkeypatch):
        calls = {}
        fake_func = _make_fake_func(calls)
        monkeypatch.delenv("CINEMATE_INLINE_JOBS", raising=False)
        monkeypatch.setattr("services.queue_service._get_queue", lambda: None)
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: fake_func)

        enqueue_job(RUN_INTENT_FUNC, intent="fallback", chat_id="456")

        assert calls["kwargs"]["intent"] == "fallback"
        assert calls["kwargs"]["chat_id"] == "456"

    def test_fallback_logs_warning(self, monkeypatch):
        import services.queue_service as qs
        monkeypatch.delenv("CINEMATE_INLINE_JOBS", raising=False)
        monkeypatch.setattr(qs, "_get_queue", lambda: None)
        monkeypatch.setattr(qs, "_resolve_callable", lambda _: _make_fake_func({}))

        with patch.object(qs.logger, "warning") as mock_warn:
            enqueue_job(RUN_INTENT_FUNC, chat_id="5")

        assert mock_warn.called
        assert "unavailable" in mock_warn.call_args[0][0].lower()


class TestEnqueueRQPath:
    """When _get_queue() returns a valid queue, job must be pushed to RQ."""

    def test_job_id_logged(self, monkeypatch):
        import services.queue_service as qs
        fake_func = MagicMock()
        fake_job = MagicMock()
        fake_job.id = "test-job-id-001"
        fake_queue = MagicMock()
        fake_queue.enqueue.return_value = fake_job

        monkeypatch.delenv("CINEMATE_INLINE_JOBS", raising=False)
        monkeypatch.setattr(qs, "_get_queue", lambda: fake_queue)
        monkeypatch.setattr(qs, "_resolve_callable", lambda _: fake_func)

        with patch.object(qs.logger, "info") as mock_info:
            enqueue_job(RUN_INTENT_FUNC, chat_id="88")

        log_args = " ".join(str(a) for c in mock_info.call_args_list for a in c[0])
        assert "test-job-id-001" in log_args

    def test_enqueue_called_with_func_and_kwargs(self, monkeypatch):
        fake_func = MagicMock()
        fake_job = MagicMock()
        fake_job.id = "j1"
        fake_queue = MagicMock()
        fake_queue.enqueue.return_value = fake_job

        monkeypatch.delenv("CINEMATE_INLINE_JOBS", raising=False)
        monkeypatch.setattr("services.queue_service._get_queue", lambda: fake_queue)
        monkeypatch.setattr("services.queue_service._resolve_callable", lambda _: fake_func)

        enqueue_job(RUN_INTENT_FUNC, chat_id="10", intent="search")

        fake_queue.enqueue.assert_called_once_with(fake_func, chat_id="10", intent="search")


# ===========================================================================
# _resolve_callable tests
# ===========================================================================

class TestResolveCallable:
    def test_resolves_os_getcwd(self):
        import os as _os
        result = _resolve_callable("os.getcwd")
        assert result is _os.getcwd

    def test_resolves_asyncio_sleep(self):
        result = _resolve_callable("asyncio.sleep")
        assert result is asyncio.sleep

    def test_raises_on_missing_attr(self):
        with pytest.raises(AttributeError):
            _resolve_callable("os.nonexistent_func_xyz")

    def test_raises_on_missing_module(self):
        with pytest.raises(ModuleNotFoundError):
            _resolve_callable("nonexistent_module_xyz.func")


# ===========================================================================
# _get_queue tests
# ===========================================================================

class TestGetQueue:
    def test_returns_none_when_redis_not_configured(self, monkeypatch):
        monkeypatch.setattr("services.queue_service.get_redis", lambda: None)
        monkeypatch.setattr("services.queue_service.is_redis_configured", lambda: False)
        assert _get_queue() is None

    def test_returns_none_when_get_redis_none(self, monkeypatch):
        monkeypatch.setattr("services.queue_service.get_redis", lambda: None)
        monkeypatch.setattr("services.queue_service.is_redis_configured", lambda: True)
        assert _get_queue() is None

    @rq_skip
    def test_returns_queue_when_configured(self, monkeypatch):
        """Skipped on Windows: RQ's scheduler imports multiprocessing.get_context('fork')
        which is not available on Windows (only 'spawn'/'forkserver' are supported).
        This test passes on Linux/macOS where 'fork' is the default context.
        """
        from rq import Queue

        fake_redis = MagicMock()
        monkeypatch.setattr("services.queue_service.get_redis", lambda: fake_redis)
        monkeypatch.setattr("services.queue_service.is_redis_configured", lambda: True)

        q = _get_queue()
        assert q is not None
        assert isinstance(q, Queue)


# ===========================================================================
# Fix 1 — worker_runner.py deprecation stub
# ===========================================================================

class TestWorkerRunnerDeprecation:
    """worker_runner.py must exist and emit a DeprecationWarning on import."""

    def test_worker_runner_exists_and_warns(self):
        import importlib

        # Remove cached module so the warning fires fresh
        sys.modules.pop("worker_runner", None)

        with pytest.warns(DeprecationWarning, match="worker_runner.py is deprecated"):
            importlib.import_module("worker_runner")

    def test_deprecation_warning_mentions_rq_worker(self):
        import importlib
        import warnings

        sys.modules.pop("worker_runner", None)

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            importlib.import_module("worker_runner")

        dep_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert any("rq_worker.py" in str(dw.message) for dw in dep_warnings)


# ===========================================================================
# Integration test — enqueue + SimpleWorker processes job in-process
# ===========================================================================

@pytest.mark.integration
class TestRQIntegration:
    """Requires a live Redis connection AND a Unix-like platform (fork support).
    Skipped automatically when either condition is not met.
    """

    def test_enqueue_and_process_with_simple_worker(self, monkeypatch):
        if _RQ_UNAVAILABLE:
            pytest.skip("RQ cannot be imported on this platform (no fork context — Windows)")

        from config.redis_cache import get_redis
        redis_conn = get_redis()
        if not redis_conn:
            pytest.skip("Redis not configured; skipping RQ integration test")

        try:
            from rq import Queue
            from rq.worker import SimpleWorker
        except Exception as exc:
            pytest.skip(f"RQ import failed: {exc}")

        queue_name = os.environ.get("CINEMATE_QUEUE_NAME", "cinemate_intent_jobs")
        q = Queue(queue_name, connection=redis_conn, is_async=True)
        q.empty()

        monkeypatch.delenv("CINEMATE_INLINE_JOBS", raising=False)
        enqueue_job(RUN_INTENT_FUNC, intent="integration", chat_id="999")

        assert q.count == 1, "Job was not pushed onto the queue"

        worker = SimpleWorker([q], connection=redis_conn)
        worker.work(burst=True)

        assert q.count == 0, "Job was not consumed by the worker"
