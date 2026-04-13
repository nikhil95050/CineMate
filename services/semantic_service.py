"""SemanticService: LLM-backed intent classification with Redis caching.

classify_intent(text) maps free-form user messages to one of:
    start | trending | surprise | watchlist | history |
    search | movie_search | help | unknown

Classifications are cached in Redis under key  semantic:<sha256(text[:200])>
with CACHE_TTL TTL to avoid redundant API calls for repeated phrases.

Safety guarantees
──────────────────
• This service is ONLY invoked from worker_service dispatch_intent for
  genuine fallback messages.  It never calls itself recursively.
• If classify_intent is called with the text of a fallback-loop guard
  (i.e. the caller already tried semantic routing once), it returns
  "unknown" immediately.
• Provider disabled / unhealthy → returns "unknown" without calling API.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

logger = logging.getLogger("semantic_service")

# ── Constants ─────────────────────────────────────────────────────
VALID_INTENTS = frozenset({
    "start", "trending", "surprise", "watchlist",
    "history", "search", "movie_search", "help",
})
CACHE_TTL    = 3_600   # 1 hour
MIN_TEXT_LEN = 5       # ignore tiny / empty strings
CACHE_PREFIX = "semantic:"

# Model fallback chain: primary first, cheaper fallback second.
# sonar-pro gives higher accuracy; sonar is always available as a safety net.
_LLM_MODELS = ("sonar-pro", "sonar")

_SYSTEM_PROMPT = """\
You are an intent classifier for a Telegram movie-recommendation bot called CineMate.
Classify the user's message into EXACTLY ONE of these lowercase labels:

  start        – user wants to begin, set up preferences, or greet the bot
  trending     – user wants to know what is popular / trending right now
  surprise     – user wants a random or surprise movie recommendation
  watchlist    – user wants to view, manage, or add to their watchlist
  history      – user wants to view their watch history or previously recommended movies
  search       – user wants to search for a specific movie or look something up by title
  movie_search – user asks about a specific named movie (details, cast, rating, streaming)
  help         – user is asking for help, usage info, or what the bot can do
  unknown      – none of the above; the message is unrelated to movies or the bot

Rules:
- Reply with ONLY the single label. No explanation, no punctuation.
- If the message is ambiguous between search and movie_search, prefer movie_search
  when a specific film title is mentioned.
- If the message contains a command keyword (trending, watchlist, history, etc.)
  even phrased naturally, map to the corresponding label.
"""


class SemanticService:
    """Classify free-text user messages into structured bot intents."""

    def __init__(self, health_service=None) -> None:
        """health_service is optional; pass it to gate calls when Perplexity is down."""
        self._health = health_service

    # ── Public API ───────────────────────────────────────────────────────

    async def classify_intent(self, text: str) -> str:
        """Return a valid intent string, or 'unknown' on any error / short text."""
        text = (text or "").strip()
        if len(text) < MIN_TEXT_LEN:
            return "unknown"

        # 1. Redis cache check
        cache_key = self._make_cache_key(text)
        cached = self._get_cached(cache_key)
        if cached:
            logger.debug("[Semantic] cache hit: %r → %s", text[:60], cached)
            return cached

        # 2. Health/feature-flag guard
        if self._health is not None and not self._health.is_healthy("perplexity"):
            logger.warning("[Semantic] Perplexity unhealthy – returning unknown")
            return "unknown"

        # 3. LLM call with model fallback chain
        try:
            result = await self._call_llm(text)
        except Exception as exc:  # noqa: BLE001
            logger.error("[Semantic] _call_llm raised unexpectedly: %s", exc)
            return "unknown"

        result = result.strip().lower() if result else "unknown"
        if result not in VALID_INTENTS:
            logger.debug("[Semantic] LLM returned unexpected label %r – using unknown", result)
            result = "unknown"

        # 4. Cache result
        self._set_cached(cache_key, result)
        logger.info("[Semantic] classified %r → %s", text[:60], result)
        return result

    # ── LLM call with fallback chain ─────────────────────────────────────────

    async def _call_llm(self, text: str) -> Optional[str]:
        """Try each model in _LLM_MODELS in order; return first successful reply.

        Model chain: sonar-pro (primary) → sonar (fallback).
        Each failure is logged.  Returns None only when all models are exhausted.
        """
        from clients import perplexity_client  # local import to allow easy mocking

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": text[:500]},
        ]

        last_exc: Optional[Exception] = None
        for model in _LLM_MODELS:
            try:
                reply = await perplexity_client.chat(
                    messages=messages,
                    model=model,
                    temperature=0.0,
                    max_tokens=20,
                )
                if reply:
                    if model != _LLM_MODELS[0]:
                        logger.warning(
                            "[Semantic] primary model %s failed; succeeded with %s",
                            _LLM_MODELS[0], model,
                        )
                    return reply
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Semantic] model %s failed: %s", model, exc)
                last_exc = exc

        logger.error("[Semantic] all models in fallback chain exhausted; last error: %s", last_exc)
        return None

    # ── Cache helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _make_cache_key(text: str) -> str:
        digest = hashlib.sha256(text[:200].encode()).hexdigest()[:16]
        return f"{CACHE_PREFIX}{digest}"

    @staticmethod
    def _get_cached(key: str) -> Optional[str]:
        try:
            import config.redis_cache as rc
            val = rc.get_json(key)
            if isinstance(val, str):
                return val
        except Exception:
            pass
        return None

    @staticmethod
    def _set_cached(key: str, value: str) -> None:
        try:
            import config.redis_cache as rc
            rc.set_json(key, value, ttl=CACHE_TTL)
        except Exception:
            pass
