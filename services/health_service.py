"""HealthService: circuit-breaker logic for external providers.

State is persisted via AdminRepository (app_config table) so it survives
process restarts.  In-memory dict is the fallback when Supabase is absent.

Circuit-breaker states
──────────────────────
  CLOSED    – provider healthy; all calls allowed.
  OPEN      – too many failures; calls blocked for RECOVERY_WINDOW seconds.
  HALF-OPEN – recovery window expired; next call is a probe.
              On success → CLOSED.  On failure → OPEN again.

Priority order in is_healthy()
───────────────────────────────
  1. Daily budget guard          (always respected)
  2. Manual feature-flag disable (admin toggle always wins)
  3. Circuit-breaker state       (OPEN / HALF-OPEN / CLOSED)

app_config keys used
────────────────────
  provider.<name>.enabled             → "true" | "false"  (manual toggle)
  provider.<name>.failure_count       → "<int>"
  provider.<name>.last_failure_time   → ISO-8601 string
  provider.<name>.calls.<YYYY-MM-DD>  → "<int>"  (daily counter)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("health_service")

# ── Thresholds ───────────────────────────────────────────────────────
FAILURE_THRESHOLD: int = 3        # consecutive failures before circuit opens
RECOVERY_WINDOW:   int = 120      # seconds before half-open probe is allowed

DAILY_BUDGET: dict[str, int] = {
    "perplexity": 500,
    "omdb":       1_000,
    "watchmode":  500,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


class HealthService:
    """Manage per-provider circuit-breaker state via AdminRepository."""

    def __init__(self, admin_repo) -> None:
        self._repo = admin_repo

    # ── Public API (called by clients) ─────────────────────────────────────

    def is_healthy(self, provider: str) -> bool:
        """Return True when the provider circuit is CLOSED or HALF-OPEN (probe).

        Priority:
          1. Daily budget guard  – respected unconditionally.
          2. Manual admin toggle – if an admin explicitly disabled the provider
             via /admin_disable_provider, that decision is always honoured,
             even if the circuit would otherwise enter HALF-OPEN.
          3. Circuit-breaker     – OPEN / HALF-OPEN / CLOSED logic.
        """
        # 1. Daily budget guard (always respected regardless of circuit state)
        if self._over_daily_budget(provider):
            logger.warning("[Health] %s exceeded daily call budget", provider)
            return False

        # 2. Manual feature-flag check – admin disable always wins.
        #    This is evaluated BEFORE the circuit-breaker so that a provider
        #    disabled via /admin_disable_provider cannot be accidentally
        #    re-probed by the HALF-OPEN recovery logic.
        flag = self._repo.get_config(f"provider.{provider}.enabled")
        if flag is not None and flag.lower() == "false":
            # Only block if the circuit was manually disabled (failure_count == 0
            # or the flag was set by an admin, not by report_failure hitting the
            # threshold).  We detect a manual disable by checking whether
            # failure_count is below the threshold – if the circuit was opened
            # automatically by report_failure, we still want HALF-OPEN recovery
            # so the circuit can heal itself.  If an admin explicitly disabled
            # the provider (failure_count < FAILURE_THRESHOLD), block the call.
            failures = self._get_failure_count(provider)
            if failures < FAILURE_THRESHOLD:
                logger.debug("[Health] %s manually disabled by admin", provider)
                return False
            # flag=false was written by report_failure — fall through to
            # circuit-breaker logic so HALF-OPEN recovery still works.

        # 3. Circuit-breaker state
        failures = self._get_failure_count(provider)
        if failures >= FAILURE_THRESHOLD:
            last_failure = self._get_last_failure_time(provider)
            if last_failure is not None:
                elapsed = (_utc_now() - last_failure).total_seconds()
                if elapsed >= RECOVERY_WINDOW:
                    # HALF-OPEN: recovery window elapsed – allow one probe
                    logger.info(
                        "[Health] %s HALF-OPEN (elapsed=%.0fs) – allowing probe",
                        provider, elapsed,
                    )
                    return True

                logger.warning(
                    "[Health] %s circuit OPEN (failures=%d, %.0fs remaining)",
                    provider, failures, RECOVERY_WINDOW - elapsed,
                )
                return False
            # No timestamp stored → treat as healthy
            return True

        return True  # CLOSED

    def report_failure(self, provider: str) -> None:
        """Increment failure counter and persist timestamp.  Opens circuit at threshold."""
        count = self._get_failure_count(provider) + 1
        self._set_failure_count(provider, count)
        self._repo.set_config(f"provider.{provider}.last_failure_time", _utc_now_iso())

        if count >= FAILURE_THRESHOLD:
            # Flip the feature flag to disabled so is_healthy() short-circuits fast
            current = self._repo.get_config(f"provider.{provider}.enabled")
            if current is None or current.lower() != "false":
                self._repo.set_config(f"provider.{provider}.enabled", "false")
                logger.error(
                    "[Health] %s circuit OPENED after %d failures – flag set to disabled",
                    provider, count,
                )

    def report_success(self, provider: str) -> None:
        """Reset failure counter and re-enable provider (CLOSED state)."""
        prev = self._get_failure_count(provider)
        if prev == 0:
            # Already healthy; skip unnecessary writes
            return
        self._set_failure_count(provider, 0)
        self._repo.set_config(f"provider.{provider}.enabled", "true")
        logger.info("[Health] %s circuit CLOSED – reset after successful call", provider)

    def increment_daily_calls(self, provider: str) -> None:
        """Bump the today-scoped daily call counter (called on every successful call)."""
        today = _utc_now().strftime("%Y-%m-%d")
        key = f"provider.{provider}.calls.{today}"
        current = self._get_daily_calls(provider)
        self._repo.set_config(key, str(current + 1))

    def get_provider_status(self, provider: str) -> dict:
        """Return a summary dict of a provider's circuit-breaker state."""
        failures = self._get_failure_count(provider)
        last_failure = self._get_last_failure_time(provider)
        flag = self._repo.get_config(f"provider.{provider}.enabled")

        if failures >= FAILURE_THRESHOLD:
            if last_failure:
                elapsed = (_utc_now() - last_failure).total_seconds()
                state = "half_open" if elapsed >= RECOVERY_WINDOW else "open_circuit"
            else:
                state = "open_circuit"
        elif flag is not None and flag.lower() == "false":
            state = "open_manual"
        else:
            state = "closed"

        return {
            "provider": provider,
            "state": state,
            "failure_count": failures,
            "last_failure_time": last_failure.isoformat() if last_failure else None,
            "daily_calls_today": self._get_daily_calls(provider),
            "daily_budget": DAILY_BUDGET.get(provider),
        }

    # ── Daily budget helpers ──────────────────────────────────────────────

    def _over_daily_budget(self, provider: str) -> bool:
        budget = DAILY_BUDGET.get(provider)
        if budget is None:
            return False
        return self._get_daily_calls(provider) >= budget

    def _get_daily_calls(self, provider: str) -> int:
        today = _utc_now().strftime("%Y-%m-%d")
        val = self._repo.get_config(f"provider.{provider}.calls.{today}")
        try:
            return int(val or 0)
        except (ValueError, TypeError):
            return 0

    # ── Persistence helpers ──────────────────────────────────────────────

    def _get_failure_count(self, provider: str) -> int:
        val = self._repo.get_config(f"provider.{provider}.failure_count")
        try:
            return int(val or 0)
        except (ValueError, TypeError):
            return 0

    def _set_failure_count(self, provider: str, count: int) -> None:
        self._repo.set_config(f"provider.{provider}.failure_count", str(count))

    def _get_last_failure_time(self, provider: str) -> Optional[datetime]:
        val = self._repo.get_config(f"provider.{provider}.last_failure_time")
        if not val:
            return None
        try:
            dt = datetime.fromisoformat(val)
            # Ensure timezone-aware
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
