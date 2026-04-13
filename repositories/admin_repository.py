"""AdminRepository: admins table, app_config flags, bot_stats, error_logs, api_usage."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import config.supabase_client as sb

logger = logging.getLogger("admin_repository")

# In-memory fallback stores (used when Supabase is unavailable / in tests)
_admin_store: set = set()
_config_store: Dict[str, str] = {}
_stats_store: Dict[str, int] = {}


def clear_test_stores() -> None:
    """Clear in-memory fallback stores (for test isolation)."""
    _admin_store.clear()
    _config_store.clear()
    _stats_store.clear()


class AdminRepository:
    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    def is_admin(self, chat_id: str) -> bool:
        """Return True if chat_id is listed in the admins table."""
        if sb.is_configured():
            try:
                res, err = sb.select_rows(
                    "admins",
                    filters={"chat_id": str(chat_id)},
                    limit=1,
                )
                if err:
                    logger.warning("[AdminRepo] is_admin query error: %s", err)
                    return False
                return bool(res)
            except Exception as exc:
                logger.warning("[AdminRepo] is_admin exception: %s", exc)
                return False
        return str(chat_id) in _admin_store

    # ------------------------------------------------------------------
    # App config / feature flags
    # ------------------------------------------------------------------

    def get_config(self, key: str) -> Optional[str]:
        if sb.is_configured():
            try:
                res, err = sb.select_rows("app_config", filters={"key": key}, limit=1)
                if err or not res:
                    return None
                return res[0].get("value")
            except Exception as exc:
                logger.warning("[AdminRepo] get_config exception: %s", exc)
                return None
        return _config_store.get(key)

    def set_config(self, key: str, value: str) -> None:
        if sb.is_configured():
            try:
                sb.upsert_rows(
                    "app_config",
                    [{"key": key, "value": value, "updated_at": _now()}],
                    on_conflict="key",
                )
            except Exception as exc:
                logger.warning("[AdminRepo] set_config exception: %s", exc)
        else:
            _config_store[key] = value

    # ------------------------------------------------------------------
    # Bot stats
    # ------------------------------------------------------------------

    def get_all_stats(self) -> Dict[str, int]:
        if sb.is_configured():
            try:
                res, err = sb.select_rows("bot_stats", limit=200)
                if err:
                    return {}
                return {row["metric_name"]: int(row["metric_value"]) for row in (res or [])}
            except Exception as exc:
                logger.warning("[AdminRepo] get_all_stats exception: %s", exc)
                return {}
        return dict(_stats_store)

    def increment_stat(self, metric_name: str, by: int = 1) -> None:
        if sb.is_configured():
            try:
                existing = self.get_all_stats().get(metric_name, 0)
                sb.upsert_rows(
                    "bot_stats",
                    [{"metric_name": metric_name, "metric_value": existing + by}],
                    on_conflict="metric_name",
                )
            except Exception as exc:
                logger.warning("[AdminRepo] increment_stat exception: %s", exc)
        else:
            _stats_store[metric_name] = _stats_store.get(metric_name, 0) + by

    # ------------------------------------------------------------------
    # Error logs
    # ------------------------------------------------------------------

    def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        if sb.is_configured():
            try:
                res, err = sb.select_rows(
                    "error_logs",
                    order_by="timestamp",
                    order_desc=True,
                    limit=limit,
                )
                return res or []
            except Exception as exc:
                logger.warning("[AdminRepo] get_recent_errors exception: %s", exc)
                return []
        return []

    # ------------------------------------------------------------------
    # API usage
    # ------------------------------------------------------------------

    def get_usage_summary(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Aggregate api_usage by provider for the last `hours` hours."""
        if sb.is_configured():
            try:
                from datetime import datetime, timezone, timedelta
                since = (
                    datetime.now(timezone.utc) - timedelta(hours=hours)
                ).isoformat()
                res, err = sb.select_rows(
                    "api_usage",
                    filters={"timestamp__gte": since},
                    limit=5000,
                )
                if err or not res:
                    return []
                # Aggregate in Python
                agg: Dict[str, Dict[str, Any]] = {}
                for row in res:
                    p = row.get("provider", "unknown")
                    if p not in agg:
                        agg[p] = {"provider": p, "calls": 0, "total_tokens": 0, "chat_ids": set()}
                    agg[p]["calls"] += 1
                    agg[p]["total_tokens"] += int(row.get("total_tokens") or 0)
                    agg[p]["chat_ids"].add(row.get("chat_id", ""))
                result = []
                for p, data in agg.items():
                    data["unique_users"] = len(data.pop("chat_ids"))
                    result.append(data)
                return sorted(result, key=lambda x: x["calls"], reverse=True)
            except Exception as exc:
                logger.warning("[AdminRepo] get_usage_summary exception: %s", exc)
                return []
        return []

    def get_top_users(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Return top users by interaction count."""
        if sb.is_configured():
            try:
                res, err = sb.select_rows("user_interactions", limit=10000)
                if err or not res:
                    return []
                counts: Dict[str, int] = {}
                for row in res:
                    cid = row.get("chat_id", "unknown")
                    counts[cid] = counts.get(cid, 0) + 1
                sorted_users = sorted(counts.items(), key=lambda x: x[1], reverse=True)
                return [{"chat_id": c, "interactions": n} for c, n in sorted_users[:limit]]
            except Exception as exc:
                logger.warning("[AdminRepo] get_top_users exception: %s", exc)
                return []
        return []

    # ------------------------------------------------------------------
    # All users (for broadcast)
    # ------------------------------------------------------------------

    def get_all_user_chat_ids(self) -> List[str]:
        if sb.is_configured():
            try:
                res, err = sb.select_rows("users", limit=10000)
                if err or not res:
                    return []
                return [str(row["chat_id"]) for row in res if row.get("chat_id")]
            except Exception as exc:
                logger.warning("[AdminRepo] get_all_user_chat_ids exception: %s", exc)
                return []
        return []


def _now() -> str:
    from utils.time_utils import utc_now_iso
    return utc_now_iso()
