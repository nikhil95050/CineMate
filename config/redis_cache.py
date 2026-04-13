import os
import json
import time
import threading
import redis
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger("redis_cache")

REDIS_URL = (
    os.environ.get("REDIS_URL", "").strip()
    or os.environ.get("UPSTASH_REDIS_URL", "").strip()
)


def _is_placeholder(url: str) -> bool:
    placeholders = ["your-db.upstash.io", "YOUR_PASSWORD", "example.com"]
    return any(p in url for p in placeholders)


_VALID_SCHEMES = ("redis://", "rediss://", "unix://")
if REDIS_URL:
    if not any(REDIS_URL.startswith(s) for s in _VALID_SCHEMES):
        logger.warning(
            "REDIS_URL does not start with a valid scheme (redis://). Falling back."
        )
        REDIS_URL = ""
    elif _is_placeholder(REDIS_URL):
        logger.info(
            "REDIS_URL contains placeholders (e.g., 'your-db.upstash.io'). Operating in local-only mode."
        )
        REDIS_URL = ""

_local_cache: dict = {}
_local_lock = threading.Lock()
_redis_client = None
_redis_init_lock = threading.Lock()
_error_logged = False


def get_redis():
    """Return a connected Redis client, or None if unavailable."""
    global _redis_client, _error_logged
    if _redis_client is not None:
        return _redis_client
    if not REDIS_URL:
        return None

    with _redis_init_lock:
        if _redis_client is not None:
            return _redis_client
        try:
            client = redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_timeout=30,
                socket_connect_timeout=15,
                retry_on_timeout=True,
                health_check_interval=25,
            )
            client.ping()
            _redis_client = client
            logger.info("Connected successfully to Redis.")
        except Exception as e:  # pragma: no cover - defensive
            if not _error_logged:
                logger.error(f"Redis connection failed: {e}")
                logger.info(
                    "Falling back to in-process local cache only (non-persistent)."
                )
                _error_logged = True
            _redis_client = None

    return _redis_client


def is_configured() -> bool:
    return bool(REDIS_URL)


def is_connected() -> bool:
    client = get_redis()
    if not client:
        return False
    try:
        return client.ping()
    except Exception:  # pragma: no cover - defensive
        return False


def get_json(key: str):
    """Fetch a JSON value from local cache, then Redis."""
    with _local_lock:
        item = _local_cache.get(key)
        if item:
            val, expiry = item
            if expiry is None or expiry > time.time():
                return val
            del _local_cache[key]

    client = get_redis()
    if not client:
        return None

    try:
        raw = client.get(key)
        if raw:
            data = json.loads(raw)
            with _local_lock:
                _local_cache[key] = (data, time.time() + 60)
            return data
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Redis GET error for '{key}': {e}")
    return None


MAX_LOCAL_SIZE = 1000


def set_json(key: str, value, ttl: int | None = None) -> None:
    """Write a JSON value to local cache and Redis."""
    with _local_lock:
        if len(_local_cache) >= MAX_LOCAL_SIZE:
            sorted_keys = sorted(
                _local_cache.keys(), key=lambda k: _local_cache[k][1] or 0
            )
            for k in sorted_keys[: MAX_LOCAL_SIZE // 5]:
                del _local_cache[k]

        expiry = time.time() + ttl if ttl else None
        _local_cache[key] = (value, expiry)

    client = get_redis()
    if not client:
        return

    try:
        val_str = json.dumps(value, ensure_ascii=False)
        if ttl:
            client.setex(key, ttl, val_str)
        else:
            client.set(key, val_str)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Redis SET error for '{key}': {e}")


def delete_key(key: str) -> None:
    """Delete a key from both local cache and Redis."""
    with _local_lock:
        _local_cache.pop(key, None)
    client = get_redis()
    if client:
        try:
            client.delete(key)
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Redis DELETE error for '{key}': {e}")


def delete_prefix(prefix: str) -> None:
    """Delete all keys starting with prefix (local and Redis)."""
    with _local_lock:
        to_del = [k for k in _local_cache if k.startswith(prefix)]
        for k in to_del:
            del _local_cache[k]

    client = get_redis()
    if client:
        try:
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor, match=f"{prefix}*", count=100)
                if keys:
                    client.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Redis DELETE_PREFIX error for '{prefix}': {e}")


def clear_local_cache() -> None:
    with _local_lock:
        _local_cache.clear()


_seen_updates: dict = {}
_seen_lock = threading.Lock()


def mark_processed_update(update_id: str) -> bool:
    """Return True if this update_id is new (not yet processed).

    BUG-9 FIX: expired keys are purged BEFORE the duplicate check so that
    an update whose TTL has elapsed is NOT incorrectly treated as a duplicate.
    Previously, the cleanup ran after the check, meaning stale entries were
    never evicted before they were tested.
    """
    key = f"processed_update:{update_id}"
    client = get_redis()
    if client:
        try:
            return bool(client.set(key, "1", nx=True, ex=3600))
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Redis Dedup error: {e}")

    with _seen_lock:
        now = time.time()

        # BUG-9 FIX: purge expired entries FIRST, then check for duplicates.
        expired = [k for k, v in _seen_updates.items() if v < now]
        for k in expired:
            del _seen_updates[k]

        if update_id in _seen_updates:
            return False

        if len(_seen_updates) >= 5000:
            oldest_keys = sorted(_seen_updates, key=_seen_updates.get)[:1000]
            for k in oldest_keys:
                del _seen_updates[k]

        _seen_updates[update_id] = now + 3600
    return True


def is_rate_limited(
    key: str, limit: int = 12, window_seconds: int = 60, user_tier: str = "user"
) -> bool:
    """Tiered rate limiter.

    - admin: 999 recs / min
    - vip:   30  recs / min
    - user:  honours the caller-supplied `limit` (default 12 recs / min)

    BUG-8 FIX: the previous implementation unconditionally overwrote `limit`
    with the hard-coded tier default (12) in the else branch, silently
    ignoring any value the caller passed.  The fix uses a separate
    `effective_limit` variable so the caller's value is respected for
    non-admin, non-vip tiers.
    """
    full_key = f"rate_limit:{key}"

    # BUG-8 FIX: derive effective limit without clobbering the parameter.
    if user_tier == "admin":
        effective_limit = 999
    elif user_tier == "vip":
        effective_limit = 30
    else:
        effective_limit = limit  # honour the caller's value

    client = get_redis()
    if client:
        lua_script = """
        local current = redis.call('INCR', KEYS[1])
        if current == 1 then
            redis.call('EXPIRE', KEYS[1], ARGV[1])
        end
        return current
        """
        try:
            current = client.eval(lua_script, 1, full_key, window_seconds)
            return int(current) > effective_limit
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Redis Rate limit error: {e}")

    with _local_lock:
        val, expiry = _local_cache.get(full_key, (0, 0))
        now = time.time()
        if now > expiry:
            _local_cache[full_key] = (1, now + window_seconds)
            return False
        new_val = val + 1
        _local_cache[full_key] = (new_val, expiry)
        return new_val > effective_limit


def increment(key: str, amount: int = 1) -> int:
    client = get_redis()
    if client:
        try:
            return int(client.incrby(key, amount))
        except Exception as e:  # pragma: no cover - defensive
            logger.debug(f"Redis INCR error for '{key}': {e}")

    with _local_lock:
        val, expiry = _local_cache.get(key, (0, None))
        new_val = (val or 0) + amount
        _local_cache[key] = (new_val, expiry)
        return int(new_val)
