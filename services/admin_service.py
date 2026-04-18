"""AdminService: health checks, cache clearing, cost estimation, provider flags."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger("admin_service")

# Approximate cost per 1 000 tokens (USD) – update as pricing changes
_TOKEN_COSTS: Dict[str, float] = {
    "perplexity": 0.001,
    "openai":     0.002,
    "omdb":       0.0,
    "watchmode":  0.0,
}


class AdminService:
    def __init__(self, admin_repo, health_service=None):
        self.admin_repo = admin_repo
        self._health_service = health_service

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def check_health(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {}

        # Supabase
        try:
            import config.supabase_client as sb
            if sb.is_configured():
                res, err = sb.select_rows("bot_stats", limit=1)
                results["supabase"] = "ok" if not err else f"error: {err}"
            else:
                results["supabase"] = "not_configured"
        except Exception as exc:
            results["supabase"] = f"exception: {exc}"

        # Redis
        try:
            import config.redis_cache as rc
            client = rc.get_redis()          # <-- corrected
            if client is not None:
                client.ping()
                results["redis"] = "ok"
            else:
                results["redis"] = "not_configured"
        except Exception as exc:
            results["redis"] = f"exception: {exc}"

        # ── External API providers ───────────────────────────────────────
        import os

        # Perplexity
        perplexity_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
        if not perplexity_key:
            results["perplexity"] = "not_configured"
        else:
            results["perplexity"] = self._provider_circuit_status("perplexity")

        # OMDb
        omdb_key = os.environ.get("OMDB_API_KEY", "").strip()
        if not omdb_key:
            results["omdb"] = "not_configured"
        else:
            results["omdb"] = self._provider_circuit_status("omdb")

        # Watchmode
        watchmode_key = os.environ.get("WATCHMODE_API_KEY", "").strip()
        if not watchmode_key:
            results["watchmode"] = "not_configured"
        else:
            results["watchmode"] = self._provider_circuit_status("watchmode")

        return results

    def _provider_circuit_status(self, provider: str) -> str:
        """Check HealthService circuit-breaker state for a provider."""
        hs = self._health_service
        if hs is None:
            # Try to get it from the container as a fallback
            try:
                from services.container import health_service
                hs = health_service
            except Exception:
                pass
        if hs is not None:
            try:
                status = hs.get_provider_status(provider)
                state = status.get("state", "unknown")
                if state == "closed":
                    return "ok"
                return state  # open_circuit, half_open, open_manual
            except Exception as exc:
                return f"check_error: {exc}"
        return "ok"  # No health service available, assume ok

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_stats(self) -> Dict[str, int]:
        return self.admin_repo.get_all_stats()

    # ------------------------------------------------------------------
    # Cache clearing
    # ------------------------------------------------------------------

    def clear_cache(self) -> Dict[str, Any]:
        report: Dict[str, Any] = {}

        PREFIXES = ["movie:", "rec:", "session:", "enrich:", "update:"]
        try:
            import config.redis_cache as rc
            client = rc.get_redis()          # <-- corrected
            if client:
                deleted = 0
                for prefix in PREFIXES:
                    keys = client.keys(f"{prefix}*")
                    if keys:
                        deleted += client.delete(*keys)
                report["redis_keys_deleted"] = deleted
            else:
                report["redis"] = "not_configured"
        except Exception as exc:
            report["redis_error"] = str(exc)

        # In-process local cache
        try:
            import config.redis_cache as rc
            rc.clear_local_cache()
            report["local_cache"] = "cleared"
        except Exception as exc:
            report["local_cache_error"] = str(exc)

        return report

    # ------------------------------------------------------------------
    # Error logs
    # ------------------------------------------------------------------

    def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        return self.admin_repo.get_recent_errors(limit=limit)

    # ------------------------------------------------------------------
    # API Usage + cost estimate
    # ------------------------------------------------------------------

    def get_usage_report(self, hours: int = 24) -> Dict[str, Any]:
        rows = self.admin_repo.get_usage_summary(hours=hours)
        top_users = self.admin_repo.get_top_users(limit=5)

        total_cost = 0.0
        for row in rows:
            provider = row.get("provider", "").lower()
            tokens = row.get("total_tokens", 0)
            cost_per_k = _TOKEN_COSTS.get(provider, 0.001)
            row["estimated_cost_usd"] = round((tokens / 1000) * cost_per_k, 4)
            total_cost += row["estimated_cost_usd"]

        return {
            "hours": hours,
            "providers": rows,
            "total_estimated_cost_usd": round(total_cost, 4),
            "top_users": top_users,
        }

    # ------------------------------------------------------------------
    # Provider flags
    # ------------------------------------------------------------------

    def disable_provider(self, provider: str) -> None:
        key = f"provider.{provider.lower()}.enabled"
        self.admin_repo.set_config(key, "false")
        logger.info("[AdminService] Provider disabled: %s", provider)

    def enable_provider(self, provider: str) -> None:
        key = f"provider.{provider.lower()}.enabled"
        self.admin_repo.set_config(key, "true")
        logger.info("[AdminService] Provider enabled: %s", provider)

    def is_provider_enabled(self, provider: str) -> bool:
        key = f"provider.{provider.lower()}.enabled"
        val = self.admin_repo.get_config(key)
        return (val or "true").lower() != "false"
