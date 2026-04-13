"""Application feature-flag configuration.

BUG #10 FIX
-----------
The /health endpoint calls get_startup_readiness() which previously only
checked environment variables — it never touched the app_config Supabase
table.  If that table is empty (not seeded), any code that reads feature
flags from it would silently get no rows and behave as if all features are
disabled (or always enabled, depending on the fallback logic).

Fix: seed_app_config_table() upserts the four canonical feature-flag rows
into app_config using ON CONFLICT DO NOTHING so it is safe to call on every
startup.  main.py (or the health check initialisation path) should call
seed_app_config_table() once after Supabase is confirmed reachable.

The four keys match the column naming used throughout the codebase:
  - omdb_enabled
  - watchmode_enabled
  - perplexity_enabled
  - bot_active
"""
import os
import logging
import threading
from dotenv import load_dotenv

load_dotenv()

_logger = logging.getLogger("app_config")


def _as_bool(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


_flag_lock = threading.Lock()
_runtime_flags = {
    "perplexity": _as_bool(os.environ.get("ENABLE_PERPLEXITY"), True),
    "trailers": _as_bool(os.environ.get("ENABLE_TRAILERS"), True),
    "explanations": _as_bool(os.environ.get("ENABLE_EXPLANATIONS"), True),
}


def is_feature_enabled(name: str) -> bool:
    with _flag_lock:
        return bool(_runtime_flags.get(name, True))


def set_feature_flag(name: str, enabled: bool) -> None:
    with _flag_lock:
        _runtime_flags[name] = bool(enabled)


def get_feature_flags() -> dict:
    with _flag_lock:
        return dict(_runtime_flags)


# ---------------------------------------------------------------------------
# BUG #10 FIX — app_config table seeding
# ---------------------------------------------------------------------------

# Default values written to the app_config table on first startup.
# ON CONFLICT DO NOTHING means existing rows are never overwritten, so
# runtime overrides set via the admin panel (or direct DB edits) are safe.
_DEFAULT_APP_CONFIG_ROWS = [
    {"key": "omdb_enabled",       "value": "true"},
    {"key": "watchmode_enabled",   "value": "true"},
    {"key": "perplexity_enabled",  "value": "true"},
    {"key": "bot_active",          "value": "true"},
]


def seed_app_config_table() -> None:
    """Upsert default feature-flag rows into the app_config Supabase table.

    Safe to call on every startup — rows that already exist are left
    unchanged (ON CONFLICT DO NOTHING via upsert=False insert semantics).

    Called automatically by get_startup_readiness() when Supabase is
    configured, so no explicit call is required in main.py.
    """
    try:
        from config.supabase_client import is_configured, insert_rows
        if not is_configured():
            _logger.debug("[app_config] Supabase not configured — skipping seed.")
            return

        # Use plain insert (not upsert) so that existing rows are untouched.
        # insert_rows with upsert=False will raise/return an error on conflict
        # for duplicate keys; we catch and ignore those errors intentionally.
        for row in _DEFAULT_APP_CONFIG_ROWS:
            try:
                _res, err = insert_rows("app_config", [row], upsert=False)
                if err:
                    # "duplicate key" errors are expected and harmless.
                    dup = str(err).lower()
                    if "duplicate" in dup or "conflict" in dup or "unique" in dup:
                        _logger.debug(
                            "[app_config] seed: key '%s' already exists — skipping.",
                            row["key"],
                        )
                    else:
                        _logger.warning(
                            "[app_config] seed: unexpected error for key '%s': %s",
                            row["key"], err,
                        )
            except Exception as row_exc:
                _logger.warning(
                    "[app_config] seed: exception inserting key '%s': %s",
                    row["key"], row_exc,
                )

        _logger.info("[app_config] app_config table seeded with default feature flags.")

    except Exception as exc:
        _logger.warning("[app_config] seed_app_config_table failed: %s", exc)


def get_startup_readiness() -> dict:
    """Return readiness of critical environment configuration.

    BUG #10 FIX: Seeds the app_config table with default feature-flag rows
    on every startup call so the /health endpoint and any downstream code
    that reads from app_config always finds the expected keys.

    Mirrors the behavior of the original Antigravity app_config while keeping
    naming aligned to SUPABASE_SERVICE_ROLE_KEY.
    """
    # BUG #10 FIX: ensure default rows exist in app_config table.
    seed_app_config_table()

    return {
        "telegram_bot_token": bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()),
        "perplexity_api_key": bool(os.environ.get("PERPLEXITY_API_KEY", "").strip()),
        "supabase_url": bool(os.environ.get("SUPABASE_URL", "").strip()),
        "supabase_service_key": bool(
            os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
            or os.environ.get("SUPABASE_API_KEY", "").strip()
        ),
        "redis_url": bool(
            os.environ.get("REDIS_URL", "").strip()
            or os.environ.get("UPSTASH_REDIS_URL", "").strip()
        ),
    }
