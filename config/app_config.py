import os
import threading
from dotenv import load_dotenv

load_dotenv()


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


def get_startup_readiness() -> dict:
    """Return readiness of critical environment configuration.

    Mirrors the behavior of the original Antigravity app_config while keeping
    naming aligned to SUPABASE_SERVICE_ROLE_KEY.
    """

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
