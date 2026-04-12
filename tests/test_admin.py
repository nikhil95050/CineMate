"""Tests for Feature 9: admin decorator, cache clearing, handler smoke tests."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def admin_repo_stub():
    repo = MagicMock()
    repo.is_admin.return_value = True
    repo.get_all_stats.return_value = {"recommendations_sent": 42, "users_total": 7}
    repo.get_recent_errors.return_value = [
        {
            "timestamp": "2026-04-12T10:00:00+00:00",
            "error_type": "LLMTimeout",
            "error_message": "Request timed out after 30 s",
            "workflow_step": "recommend",
            "chat_id": "123",
        }
    ]
    repo.get_usage_summary.return_value = [
        {"provider": "perplexity", "calls": 100, "total_tokens": 50000, "unique_users": 5},
    ]
    repo.get_top_users.return_value = [{"chat_id": "111", "interactions": 30}]
    repo.get_all_user_chat_ids.return_value = ["111", "222", "333"]
    repo.get_config.return_value = None
    return repo


@pytest.fixture
def admin_service_stub(admin_repo_stub):
    from services.admin_service import AdminService
    svc = AdminService(admin_repo=admin_repo_stub)
    return svc


# ============================================================
# admin_only decorator
# ============================================================

class TestAdminOnlyDecorator:

    def _make_handler(self):
        from handlers.admin.decorator import admin_only

        @admin_only
        async def my_handler(chat_id, **kwargs):
            return "executed"

        return my_handler

    def test_admin_allowed(self, admin_repo_stub):
        handler = self._make_handler()
        with patch("services.container.admin_repo", admin_repo_stub):
            admin_repo_stub.is_admin.return_value = True
            result = asyncio.run(handler(chat_id="999"))
        assert result == "executed"

    def test_non_admin_silently_blocked(self, admin_repo_stub):
        handler = self._make_handler()
        admin_repo_stub.is_admin.return_value = False
        with patch("services.container.admin_repo", admin_repo_stub):
            result = asyncio.run(handler(chat_id="999"))
        assert result is None  # silent no-op

    def test_repo_exception_blocks_access(self, admin_repo_stub):
        handler = self._make_handler()
        admin_repo_stub.is_admin.side_effect = RuntimeError("db dead")
        with patch("services.container.admin_repo", admin_repo_stub):
            result = asyncio.run(handler(chat_id="999"))
        assert result is None  # fail-closed


# ============================================================
# AdminService.check_health
# ============================================================

class TestAdminServiceHealth:

    def test_health_returns_dict(self, admin_service_stub):
        with (
            patch("config.supabase_client.is_configured", return_value=False),
            patch("config.redis_cache.get_redis", return_value=None),  # fixed
        ):
            result = admin_service_stub.check_health()
        assert isinstance(result, dict)
        assert "supabase" in result
        assert "redis" in result

    def test_supabase_ok(self, admin_service_stub):
        with (
            patch("config.supabase_client.is_configured", return_value=True),
            patch(
                "config.supabase_client.select_rows",
                return_value=([{"metric_name": "x", "metric_value": 1}], None),
            ),
            patch("config.redis_cache.get_redis", return_value=None),  # fixed
        ):
            result = admin_service_stub.check_health()
        assert result["supabase"] == "ok"

    def test_redis_ok(self, admin_service_stub):
        fake_redis = MagicMock()
        fake_redis.ping.return_value = True
        with (
            patch("config.supabase_client.is_configured", return_value=False),
            patch("config.redis_cache.get_redis", return_value=fake_redis),  # fixed
        ):
            result = admin_service_stub.check_health()
        assert result["redis"] == "ok"


# ============================================================
# AdminService.clear_cache
# ============================================================

class TestAdminServiceClearCache:

    def test_clear_cache_redis_available(self, admin_service_stub):
        fake_redis = MagicMock()
        fake_redis.keys.side_effect = lambda pattern: [b"movie:tt123"] if "movie:" in pattern else []
        fake_redis.delete.return_value = 1
        with (
            patch("config.redis_cache.get_redis", return_value=fake_redis),  # fixed
            patch("config.redis_cache.clear_local_cache"),
        ):
            report = admin_service_stub.clear_cache()
        assert "redis_keys_deleted" in report
        assert report["local_cache"] == "cleared"

    def test_clear_cache_redis_unavailable(self, admin_service_stub):
        with (
            patch("config.redis_cache.get_redis", return_value=None),  # fixed
            patch("config.redis_cache.clear_local_cache"),
        ):
            report = admin_service_stub.clear_cache()
        assert report.get("redis") == "not_configured"
        assert report.get("local_cache") == "cleared"

    def test_clear_cache_redis_exception_is_caught(self, admin_service_stub):
        fake_redis = MagicMock()
        fake_redis.keys.side_effect = ConnectionError("Redis gone")
        with (
            patch("config.redis_cache.get_redis", return_value=fake_redis),  # fixed
            patch("config.redis_cache.clear_local_cache"),
        ):
            report = admin_service_stub.clear_cache()
        assert "redis_error" in report


# ============================================================
# AdminService.get_usage_report
# ============================================================

class TestAdminServiceUsage:

    def test_cost_estimated_correctly(self, admin_service_stub):
        report = admin_service_stub.get_usage_report(hours=24)
        perplexity = next(
            (p for p in report["providers"] if p["provider"] == "perplexity"), None
        )
        assert perplexity is not None
        # 50_000 tokens * $0.001 / 1_000 = $0.05
        assert perplexity["estimated_cost_usd"] == pytest.approx(0.05, abs=1e-6)

    def test_empty_usage_returns_zero_cost(self, admin_repo_stub, admin_service_stub):
        admin_repo_stub.get_usage_summary.return_value = []
        report = admin_service_stub.get_usage_report(hours=24)
        assert report["total_estimated_cost_usd"] == 0.0


# ============================================================
# AdminService provider flags
# ============================================================

class TestProviderFlags:

    def test_disable_sets_false(self, admin_repo_stub, admin_service_stub):
        admin_service_stub.disable_provider("perplexity")
        admin_repo_stub.set_config.assert_called_once_with(
            "provider.perplexity.enabled", "false"
        )

    def test_enable_sets_true(self, admin_repo_stub, admin_service_stub):
        admin_service_stub.enable_provider("omdb")
        admin_repo_stub.set_config.assert_called_once_with(
            "provider.omdb.enabled", "true"
        )

    def test_is_provider_enabled_default_true(self, admin_repo_stub, admin_service_stub):
        admin_repo_stub.get_config.return_value = None
        assert admin_service_stub.is_provider_enabled("perplexity") is True

    def test_is_provider_enabled_false_when_set(self, admin_repo_stub, admin_service_stub):
        admin_repo_stub.get_config.return_value = "false"
        assert admin_service_stub.is_provider_enabled("perplexity") is False


# ============================================================
# Broadcast pending store (in-memory path)
# ============================================================

class TestBroadcastPendingStore:

    def setup_method(self):
        import handlers.admin.broadcast_handlers as bh
        bh._PENDING_STORE.clear()

    def test_store_and_pop(self):
        import handlers.admin.broadcast_handlers as bh
        with patch("config.redis_cache.get_redis", return_value=None):  # fixed
            bh._store_pending("Hello world")
            result = bh._pop_pending()
        assert result == "Hello world"

    def test_pop_returns_none_when_empty(self):
        import handlers.admin.broadcast_handlers as bh
        with patch("config.redis_cache.get_redis", return_value=None):  # fixed
            result = bh._pop_pending()
        assert result is None

    def test_cancel_clears_pending(self):
        import handlers.admin.broadcast_handlers as bh
        with patch("config.redis_cache.get_redis", return_value=None):  # fixed
            bh._store_pending("will be cancelled")
            cancelled = bh._cancel_pending()
            assert cancelled is True
            assert bh._pop_pending() is None


# ============================================================
# Handler smoke tests (no Telegram I/O)
# ============================================================

class TestAdminHandlerSmoke:
    """Smoke-test handlers end-to-end with send_message mocked out."""

    @pytest.fixture(autouse=True)
    def _mock_send(self):
        with patch("clients.telegram_helpers.send_message", new_callable=AsyncMock):
            yield

    def test_handle_admin_health_smoke(self, admin_repo_stub, admin_service_stub):
        with (
            patch("services.container.admin_repo", admin_repo_stub),
            patch("services.container.admin_service", admin_service_stub),
            patch("config.supabase_client.is_configured", return_value=False),
            patch("config.redis_cache.get_redis", return_value=None),  # fixed
        ):
            from handlers.admin.admin_handlers import handle_admin_health
            asyncio.run(handle_admin_health(chat_id="admin_1"))

    def test_handle_admin_stats_smoke(self, admin_repo_stub, admin_service_stub):
        with (
            patch("services.container.admin_repo", admin_repo_stub),
            patch("services.container.admin_service", admin_service_stub),
        ):
            from handlers.admin.admin_handlers import handle_admin_stats
            asyncio.run(handle_admin_stats(chat_id="admin_1"))

    def test_handle_admin_errors_smoke(self, admin_repo_stub, admin_service_stub):
        with (
            patch("services.container.admin_repo", admin_repo_stub),
            patch("services.container.admin_service", admin_service_stub),
        ):
            from handlers.admin.admin_handlers import handle_admin_errors
            asyncio.run(handle_admin_errors(chat_id="admin_1"))

    def test_non_admin_gets_no_message(self, admin_repo_stub):
        admin_repo_stub.is_admin.return_value = False
        with (
            patch("services.container.admin_repo", admin_repo_stub),
            patch("clients.telegram_helpers.send_message", new_callable=AsyncMock) as mock_send,
        ):
            from handlers.admin.admin_handlers import handle_admin_stats
            asyncio.run(handle_admin_stats(chat_id="hacker_99"))
        mock_send.assert_not_called()
