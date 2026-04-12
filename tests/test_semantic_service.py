"""Tests for SemanticService: classify_intent + Redis cache hit/miss.

All tests run fully offline:
  - LLM call (_call_llm) is monkeypatched.
  - Redis is replaced with a simple dict-backed stub.
"""
from __future__ import annotations

import pytest
import importlib
from unittest.mock import AsyncMock, patch, MagicMock


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_svc(fake_redis=None):
    """Return a SemanticService whose Redis helpers use fake_redis."""
    # Re-import to get a fresh module object
    import services.semantic_service as ss_mod
    importlib.reload(ss_mod)
    svc = ss_mod.SemanticService(health_service=None)
    return svc, ss_mod


class _FakeRedis:
    """In-process dict that mimics the get_json / set_json interface."""

    def __init__(self):
        self.store: dict = {}

    def get_json(self, key):
        return self.store.get(key)

    def set_json(self, key, value, ttl=None):
        self.store[key] = value


# ── Cache miss → LLM called ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_calls_llm_on_cache_miss():
    """When the Redis cache is empty, _call_llm should be invoked."""
    fr = _FakeRedis()
    svc, mod = _make_svc(fr)

    svc._call_llm = AsyncMock(return_value="trending")

    with patch.object(mod.SemanticService, "_get_cached", staticmethod(fr.get_json)), \
         patch.object(mod.SemanticService, "_set_cached", staticmethod(fr.set_json)):
        result = await svc.classify_intent("What movies are trending this week?")

    assert result == "trending"
    svc._call_llm.assert_awaited_once()


# ── Cache hit → LLM NOT called ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classify_uses_cache_hit():
    """When a cached label exists, _call_llm must NOT be invoked."""
    fr = _FakeRedis()
    text = "What movies are trending this week?"
    # Pre-populate cache
    from services.semantic_service import SemanticService
    cache_key = SemanticService._make_cache_key(text)
    fr.store[cache_key] = "trending"

    svc, mod = _make_svc(fr)
    svc._call_llm = AsyncMock(return_value="should_not_be_called")

    with patch.object(mod.SemanticService, "_get_cached", staticmethod(fr.get_json)), \
         patch.object(mod.SemanticService, "_set_cached", staticmethod(fr.set_json)):
        result = await svc.classify_intent(text)

    assert result == "trending"
    svc._call_llm.assert_not_awaited()


# ── Unknown label from LLM ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_llm_label_returns_unknown():
    """An unrecognised LLM label should be normalised to 'unknown'."""
    fr = _FakeRedis()
    svc, mod = _make_svc(fr)
    svc._call_llm = AsyncMock(return_value="something_random")

    with patch.object(mod.SemanticService, "_get_cached", staticmethod(fr.get_json)), \
         patch.object(mod.SemanticService, "_set_cached", staticmethod(fr.set_json)):
        result = await svc.classify_intent("blah blah blah totally unknown")

    assert result == "unknown"


# ── Short text returns unknown without LLM call ───────────────────────────────

@pytest.mark.asyncio
async def test_short_text_skips_llm():
    svc, mod = _make_svc()
    svc._call_llm = AsyncMock()
    result = await svc.classify_intent("hi")
    assert result == "unknown"
    svc._call_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_text_returns_unknown():
    svc, _ = _make_svc()
    svc._call_llm = AsyncMock()
    result = await svc.classify_intent("")
    assert result == "unknown"


# ── Health guard ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unhealthy_provider_returns_unknown():
    """When HealthService says provider is down, LLM should not be called."""
    fr = _FakeRedis()
    mock_health = MagicMock()
    mock_health.is_healthy.return_value = False

    import services.semantic_service as ss_mod
    importlib.reload(ss_mod)
    svc = ss_mod.SemanticService(health_service=mock_health)
    svc._call_llm = AsyncMock(return_value="trending")

    with patch.object(ss_mod.SemanticService, "_get_cached", staticmethod(fr.get_json)), \
         patch.object(ss_mod.SemanticService, "_set_cached", staticmethod(fr.set_json)):
        result = await svc.classify_intent("what is popular right now")

    assert result == "unknown"
    svc._call_llm.assert_not_awaited()


# ── Valid intents ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("label", [
    "start", "trending", "surprise", "watchlist",
    "history", "search", "movie_search", "help",
])
async def test_valid_labels_pass_through(label):
    """All valid labels returned by LLM should pass through unchanged."""
    fr = _FakeRedis()
    svc, mod = _make_svc(fr)
    svc._call_llm = AsyncMock(return_value=label)

    with patch.object(mod.SemanticService, "_get_cached", staticmethod(fr.get_json)), \
         patch.object(mod.SemanticService, "_set_cached", staticmethod(fr.set_json)):
        result = await svc.classify_intent("some long enough query text")

    assert result == label


# ── LLM exception → graceful unknown ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_llm_exception_returns_unknown():
    """An exception from _call_llm must not crash the caller."""
    fr = _FakeRedis()
    svc, mod = _make_svc(fr)
    svc._call_llm = AsyncMock(side_effect=RuntimeError("network error"))

    with patch.object(mod.SemanticService, "_get_cached", staticmethod(fr.get_json)), \
         patch.object(mod.SemanticService, "_set_cached", staticmethod(fr.set_json)):
        result = await svc.classify_intent("recommend me something good")

    assert result == "unknown"


# ── Cache key stability ───────────────────────────────────────────────────────

def test_cache_key_is_stable():
    """The same text must always produce the same cache key."""
    from services.semantic_service import SemanticService
    key1 = SemanticService._make_cache_key("trending movies")
    key2 = SemanticService._make_cache_key("trending movies")
    assert key1 == key2


def test_cache_key_differs_for_different_text():
    from services.semantic_service import SemanticService
    key1 = SemanticService._make_cache_key("trending movies")
    key2 = SemanticService._make_cache_key("my watchlist please")
    assert key1 != key2


def test_cache_key_has_prefix():
    from services.semantic_service import SemanticService, CACHE_PREFIX
    key = SemanticService._make_cache_key("any text here")
    assert key.startswith(CACHE_PREFIX)
