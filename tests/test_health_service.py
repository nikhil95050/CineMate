"""Tests for HealthService circuit-breaker state transitions.

All tests are fully offline: AdminRepository is replaced with a dict-backed
stub so no Supabase connection is required.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta

from services.health_service import HealthService, FAILURE_THRESHOLD, RECOVERY_WINDOW


# ── Stub repository ──────────────────────────────────────────────────────────

class _StubAdminRepo:
    """In-memory substitute for AdminRepository used in unit tests."""

    def __init__(self):
        self._store: dict[str, str] = {}

    def get_config(self, key: str):
        return self._store.get(key)

    def set_config(self, key: str, value: str) -> None:
        self._store[key] = value


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def repo():
    return _StubAdminRepo()


@pytest.fixture()
def svc(repo):
    return HealthService(admin_repo=repo)


# ── is_healthy: CLOSED state ─────────────────────────────────────────────────

def test_healthy_by_default(svc):
    """No failures recorded → circuit is CLOSED → healthy."""
    assert svc.is_healthy("omdb") is True


def test_healthy_below_threshold(svc):
    """One failure below threshold still returns healthy."""
    svc.report_failure("omdb")
    # threshold is 3; one failure should still be healthy
    assert svc.is_healthy("omdb") is True


# ── Circuit opens at threshold ────────────────────────────────────────────────

def test_circuit_opens_at_threshold(svc):
    """Reporting FAILURE_THRESHOLD failures disables the provider."""
    for _ in range(FAILURE_THRESHOLD):
        svc.report_failure("perplexity")
    assert svc.is_healthy("perplexity") is False


def test_flag_set_to_false_on_open(svc, repo):
    """After circuit opens the admin flag must be 'false'."""
    for _ in range(FAILURE_THRESHOLD):
        svc.report_failure("perplexity")
    flag = repo.get_config("provider.perplexity.enabled")
    assert flag == "false"


# ── report_success resets the circuit ────────────────────────────────────────

def test_report_success_closes_circuit(svc):
    """A success after failures resets counter and re-enables provider."""
    for _ in range(FAILURE_THRESHOLD):
        svc.report_failure("watchmode")
    assert svc.is_healthy("watchmode") is False

    svc.report_success("watchmode")
    assert svc.is_healthy("watchmode") is True


def test_report_success_resets_flag(svc, repo):
    for _ in range(FAILURE_THRESHOLD):
        svc.report_failure("omdb")
    svc.report_success("omdb")
    flag = repo.get_config("provider.omdb.enabled")
    assert flag == "true"


def test_report_success_noop_when_already_healthy(svc, repo):
    """report_success on an already-clean provider should not write any keys."""
    svc.report_success("omdb")
    # failure_count key should not have been written
    assert repo.get_config("provider.omdb.failure_count") is None


# ── HALF-OPEN state ───────────────────────────────────────────────────────────

def test_half_open_after_recovery_window(svc, repo):
    """After RECOVERY_WINDOW seconds the circuit should allow a probe."""
    for _ in range(FAILURE_THRESHOLD):
        svc.report_failure("perplexity")
    # Back-date last_failure_time so recovery window has elapsed
    past = (datetime.now(timezone.utc) - timedelta(seconds=RECOVERY_WINDOW + 5)).isoformat()
    repo.set_config("provider.perplexity.last_failure_time", past)
    # Flag is still "false" from circuit open but window elapsed → probe allowed
    assert svc.is_healthy("perplexity") is True


def test_still_open_within_recovery_window(svc, repo):
    """Within RECOVERY_WINDOW the circuit must stay OPEN."""
    for _ in range(FAILURE_THRESHOLD):
        svc.report_failure("perplexity")
    # Last failure is very recent (1 second ago)
    recent = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    repo.set_config("provider.perplexity.last_failure_time", recent)
    assert svc.is_healthy("perplexity") is False


# ── Manual feature-flag override ─────────────────────────────────────────────

def test_manual_disable(svc, repo):
    """A manual 'false' flag disables the provider regardless of failure count."""
    repo.set_config("provider.omdb.enabled", "false")
    assert svc.is_healthy("omdb") is False


def test_manual_enable_overrides(svc, repo):
    """A manual 'true' flag with 0 failures → healthy."""
    repo.set_config("provider.omdb.enabled", "true")
    assert svc.is_healthy("omdb") is True


# ── Daily budget ──────────────────────────────────────────────────────────────

def test_daily_budget_blocks_when_exceeded(svc, repo):
    """When daily calls >= budget the provider is considered unhealthy."""
    from services.health_service import DAILY_BUDGET
    from datetime import datetime, timezone
    budget = DAILY_BUDGET["perplexity"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    repo.set_config(f"provider.perplexity.calls.{today}", str(budget))
    assert svc.is_healthy("perplexity") is False


def test_daily_budget_allows_below_limit(svc, repo):
    from services.health_service import DAILY_BUDGET
    from datetime import datetime, timezone
    budget = DAILY_BUDGET["perplexity"]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    repo.set_config(f"provider.perplexity.calls.{today}", str(budget - 1))
    assert svc.is_healthy("perplexity") is True


# ── increment_daily_calls ─────────────────────────────────────────────────────

def test_increment_daily_calls(svc, repo):
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    svc.increment_daily_calls("omdb")
    svc.increment_daily_calls("omdb")
    val = repo.get_config(f"provider.omdb.calls.{today}")
    assert int(val) == 2


# ── get_provider_status ───────────────────────────────────────────────────────

def test_get_provider_status_closed(svc):
    status = svc.get_provider_status("omdb")
    assert status["state"] == "closed"
    assert status["failure_count"] == 0


def test_get_provider_status_open(svc):
    for _ in range(FAILURE_THRESHOLD):
        svc.report_failure("omdb")
    status = svc.get_provider_status("omdb")
    assert "open" in status["state"]


def test_get_provider_status_half_open(svc, repo):
    for _ in range(FAILURE_THRESHOLD):
        svc.report_failure("omdb")
    past = (datetime.now(timezone.utc) - timedelta(seconds=RECOVERY_WINDOW + 5)).isoformat()
    repo.set_config("provider.omdb.last_failure_time", past)
    status = svc.get_provider_status("omdb")
    assert status["state"] == "half_open"
