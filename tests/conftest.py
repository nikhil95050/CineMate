"""Pytest configuration and autouse fixtures for CineMate.

Key responsibilities
--------------------
1. Force every repository to use its in-memory _store by patching
   is_configured() to False at EVERY site that is checked:
     - config.supabase_client.is_configured  (the canonical function)
     - repositories.history_repository.sb.is_configured  (bound reference)
     - repositories.watchlist_repository.sb.is_configured  (bound reference)
   Also zero out the module-level SUPABASE_URL so the function itself
   returns False without needing to rely on the patch.

2. Silence BatchLoggers (error_batcher / interaction_batcher).
   logging_service.py does:
       from config.supabase_client import insert_rows, is_configured
   That creates a direct name binding, so patching
   config.supabase_client.is_configured never reaches BatchLogger.flush().
   Result: background threads attempt real Supabase HTTP calls and hang.
   Fix: patch both .emit() and .flush() on both batchers to no-ops so no
   thread is ever spawned during a test run.

3. Clear the Redis in-process local cache before and after each test
   to prevent get_json() hits from leaking between tests.

Why patching config.supabase_client.is_configured alone is not enough
----------------------------------------------------------------------
Each repository does `import config.supabase_client as sb` and then
calls `sb.is_configured()`.  Patching `config.supabase_client.is_configured`
replaces the function on the module object, so `sb.is_configured()` DOES see
the patched version — that part is correct.

The real problem is that SUPABASE_URL is set in the developer's .env file.
When the test process starts, dotenv loads it and `is_configured()` returns
True.  Our patch replaces the function, but only AFTER the repositories are
already imported (Python caches modules).  We must ensure the patch is active
during every call to any repo method — which the autouse fixture does by
holding the patch open for the lifetime of each test function.

Additional belt-and-suspenders: we also blank out
`config.supabase_client.SUPABASE_URL` so even an un-patched call to
is_configured() returns False.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _isolate_from_supabase():
    """Ensure every repo method takes the in-memory path, never Supabase."""
    # We patch the function at the canonical site AND blank the env var that
    # the function reads, so is_configured() returns False unconditionally.
    with (
        patch("config.supabase_client.is_configured", return_value=False),
        patch("config.supabase_client.SUPABASE_URL", ""),
        patch("config.supabase_client.SUPABASE_API_KEY", ""),
    ):
        # Also patch at the binding sites inside each repository module so
        # `sb.is_configured()` inside those modules returns False even if the
        # module was already imported before our patch took effect.
        try:
            import repositories.history_repository as _hr
            import repositories.watchlist_repository as _wr
            _hr.sb.is_configured = lambda: False
            _wr.sb.is_configured = lambda: False
            yield
        finally:
            # Restore the real function after each test.
            import config.supabase_client as _sb
            _hr.sb.is_configured = _sb.is_configured
            _wr.sb.is_configured = _sb.is_configured


@pytest.fixture(autouse=True)
def _silence_batchers():
    """Prevent error_batcher and interaction_batcher from spawning Supabase threads.

    logging_service uses ``from config.supabase_client import is_configured``
    which creates a direct name binding.  The _isolate_from_supabase fixture
    patches the module-level name, but that never updates the local binding
    inside BatchLogger.flush().  As a result, flush() calls insert_rows on
    every emit (batch_size=1 for error_batcher), spawning a background thread
    that blocks on a real HTTP request and hangs the test.

    Solution: replace .emit() and .flush() with no-ops for the whole test run.
    This is safe because logging side-effects are irrelevant to unit tests.
    """
    from services import logging_service as _ls

    _real_err_emit   = _ls.error_batcher.emit
    _real_err_flush  = _ls.error_batcher.flush
    _real_int_emit   = _ls.interaction_batcher.emit
    _real_int_flush  = _ls.interaction_batcher.flush

    _ls.error_batcher.emit        = lambda *_a, **_kw: None
    _ls.error_batcher.flush       = lambda *_a, **_kw: None
    _ls.interaction_batcher.emit  = lambda *_a, **_kw: None
    _ls.interaction_batcher.flush = lambda *_a, **_kw: None

    yield

    # Restore originals so nothing leaks between test sessions
    _ls.error_batcher.emit        = _real_err_emit
    _ls.error_batcher.flush       = _real_err_flush
    _ls.interaction_batcher.emit  = _real_int_emit
    _ls.interaction_batcher.flush = _real_int_flush


@pytest.fixture(autouse=True)
def _clear_redis_local_cache():
    """Wipe the in-process Redis cache dict before and after every test."""
    from config.redis_cache import clear_local_cache
    clear_local_cache()
    yield
    clear_local_cache()
