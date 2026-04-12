from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Returns current UTC time in ISO 8601 format with a 'Z' suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
