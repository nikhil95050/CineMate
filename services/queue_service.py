import os
import importlib
import asyncio
from typing import Any

from config.redis_cache import get_redis, is_configured as is_redis_configured
from services.logging_service import get_logger

logger = get_logger("queue")

QUEUE_NAME = os.environ.get("CINEMATE_QUEUE_NAME", "cinemate_intent_jobs")

# ---------------------------------------------------------------------------
# Production-safety guard
# ---------------------------------------------------------------------------
# CINEMATE_INLINE_JOBS is a development convenience that runs background jobs
# synchronously inside the web process instead of pushing them to RQ.
#
# Leaving this flag enabled in a production deployment will:
#   - Block the webhook response thread for the duration of each job.
#   - Silently skip Redis/RQ even when both are fully configured.
#
# At startup, app_config.get_startup_readiness() checks this flag and emits a
# WARNING log entry if CINEMATE_ENV=production and CINEMATE_INLINE_JOBS is on.
# The check is also surfaced here so the warning fires at the first enqueue
# call if the startup check was somehow bypassed.
_INLINE_PROD_WARNED = False


def _warn_if_inline_in_production() -> None:
    """Emit a single WARNING if inline mode is active in a production env."""
    global _INLINE_PROD_WARNED
    if _INLINE_PROD_WARNED:
        return
    env = os.environ.get("CINEMATE_ENV", "").strip().lower()
    if env in {"production", "prod"}:
        logger.warning(
            "[Queue] CINEMATE_INLINE_JOBS is enabled in a PRODUCTION environment. "
            "Jobs will execute synchronously inside the web process. "
            "Disable CINEMATE_INLINE_JOBS to restore async RQ processing."
        )
        _INLINE_PROD_WARNED = True


def _resolve_callable(func_name: str):
    """Resolve a dotted function path like 'services.worker_service.run_intent_job'."""
    module_name, attr = func_name.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, attr)


async def _run_inline_async(func_name: str, **kwargs: Any) -> None:
    """Execute the target function as an awaitable coroutine."""
    func = _resolve_callable(func_name)
    result = func(**kwargs)
    if asyncio.iscoroutine(result):
        await result


def _get_queue():
    """Return an RQ Queue instance or None."""
    client = get_redis()
    if not client or not is_redis_configured():
        return None
    try:
        from rq import Queue  # type: ignore
    except Exception:
        logger.warning("[Queue] RQ import failed; falling back to inline execution only.")
        return None
    return Queue(QUEUE_NAME, connection=client)


def enqueue_job(func_name: str, **kwargs: Any) -> None:
    """Enqueue a background job.

    INLINE mode (CINEMATE_INLINE_JOBS=1) or when Redis/RQ is unavailable:
    - If a running event loop exists (FastAPI / uvicorn): schedules a
      fire-and-forget asyncio.Task so the webhook response is not blocked.
    - If NO event loop is running (unit tests, scripts): calls the function
      synchronously via asyncio.run() so test assertions fire immediately.

    RQ mode: pushes the job onto the Redis queue for a separate worker process.

    Production safety: if CINEMATE_ENV=production and CINEMATE_INLINE_JOBS is
    set, a WARNING is logged. Set CINEMATE_INLINE_JOBS=0 (or unset it) to
    re-enable proper RQ dispatch in production.
    """
    inline_env = os.environ.get("CINEMATE_INLINE_JOBS", "").strip().lower()
    inline_mode = inline_env in {"1", "true", "yes", "on"}

    if inline_mode:
        _warn_if_inline_in_production()
        logger.info(f"[Queue] INLINE mode: scheduling '{func_name}' as asyncio task.")
        _schedule_async_task(func_name, **kwargs)
        return

    queue = _get_queue()
    if not queue:
        logger.warning(
            f"[Queue] Redis/RQ unavailable. Scheduling '{func_name}' as asyncio task."
        )
        _schedule_async_task(func_name, **kwargs)
        return

    try:
        func = _resolve_callable(func_name)
        # Use queue.enqueue(func, **kwargs) — the modern RQ 1.x+ API.
        # enqueue_call(func=..., kwargs=...) was deprecated in RQ 1.x and
        # removed in RQ 2.x; it must not be used on RQ >= 1.0.
        job = queue.enqueue(func, **kwargs)
        logger.info(
            "[Queue] Enqueued '%s' for chat_id=%s as job_id=%s",
            func_name,
            kwargs.get("chat_id"),
            job.id,
        )
    except Exception as e:  # pragma: no cover
        logger.error(f"[Queue] Enqueue failed for '{func_name}': {e}")
        _schedule_async_task(func_name, **kwargs)


def _schedule_async_task(func_name: str, **kwargs: Any) -> None:
    """Schedule the async worker function.

    Behaviour depends on whether an event loop is already running:

    - Running loop (FastAPI/uvicorn): use loop.create_task() for
      fire-and-forget async execution that does not block the response.
    - No running loop (unit tests, CLI scripts): use asyncio.run() which
      executes the coroutine synchronously and returns only after completion.
      This ensures monkeypatched fakes are called before test assertions run.
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run_inline_async(func_name, **kwargs))
    except RuntimeError:
        asyncio.run(_run_inline_async(func_name, **kwargs))
