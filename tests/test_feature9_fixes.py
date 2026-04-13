"""Tests for Feature 9 (Admin) fixes:
  1. broadcast no longer uses try/except ImportError fallback for send_message_with_keyboard
  2. handle_admin_stats uses _safe_format_value so string stats don't raise ValueError
  3. handle_admin_usage uses .get() so missing provider/user keys don't raise KeyError
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fix 1 — send_message_with_keyboard is a hard import, no try/except fallback
# ---------------------------------------------------------------------------

class TestBroadcastHardImport:
    """broadcast_handlers must import send_message_with_keyboard at module level.

    The old code wrapped the import in try/except (ImportError, AttributeError)
    which silently degraded to text-only when the function was absent.  The fix
    makes it a hard top-level import so a missing implementation raises loudly
    at import time rather than silently omitting the keyboard at runtime.
    """

    def test_no_try_except_import_fallback_in_source(self):
        import handlers.admin.broadcast_handlers as bh
        source = inspect.getsource(bh)
        assert "(ImportError, AttributeError)" not in source, (
            "broadcast_handlers still has the try/except (ImportError, AttributeError) "
            "fallback. Remove it — send_message_with_keyboard must be a hard import."
        )

    def test_send_message_with_keyboard_imported_at_top_level(self):
        import handlers.admin.broadcast_handlers as bh
        assert hasattr(bh, "send_message_with_keyboard"), (
            "send_message_with_keyboard should be importable directly from the module's namespace "
            "via its top-level from-import."
        )

    @pytest.mark.asyncio
    async def test_broadcast_calls_send_message_with_keyboard(self):
        """handle_admin_broadcast must call send_message_with_keyboard, not send_message."""
        with patch("handlers.admin.broadcast_handlers.send_message_with_keyboard", new_callable=AsyncMock) as mock_smk, \
             patch("handlers.admin.broadcast_handlers.send_message", new_callable=AsyncMock) as mock_sm, \
             patch("handlers.admin.broadcast_handlers._store_pending"), \
             patch("handlers.admin.decorator.admin_repo") as mock_ar:

            mock_ar.is_admin.return_value = True

            from handlers.admin.broadcast_handlers import handle_admin_broadcast
            await handle_admin_broadcast(chat_id="123", input_text="/admin_broadcast Hello world")

        mock_smk.assert_called_once()
        # send_message must NOT be called for the preview (only send_message_with_keyboard)
        assert mock_sm.call_count == 0, (
            "send_message should not be called for the preview; "
            "send_message_with_keyboard should handle it"
        )


# ---------------------------------------------------------------------------
# Fix 2 — _safe_format_value handles str, int, float, None
# ---------------------------------------------------------------------------

class TestSafeFormatValue:
    """_safe_format_value must never raise regardless of value type."""

    def setup_method(self):
        from handlers.admin.admin_handlers import _safe_format_value
        self._fn = _safe_format_value

    def test_int_formatted_with_commas(self):
        assert self._fn(1_234_567) == "1,234,567"

    def test_float_formatted_with_commas_and_two_decimals(self):
        assert self._fn(9876.5) == "9,876.50"

    def test_string_returned_as_is(self):
        assert self._fn("unknown") == "unknown"

    def test_none_returns_string_none(self):
        assert self._fn(None) == "None"

    def test_zero_int(self):
        assert self._fn(0) == "0"

    def test_zero_float(self):
        assert self._fn(0.0) == "0.00"

    @pytest.mark.asyncio
    async def test_handle_admin_stats_does_not_raise_on_string_value(self):
        """handle_admin_stats must not raise ValueError when a stat value is a string."""
        with patch("handlers.admin.admin_handlers.send_message", new_callable=AsyncMock) as mock_sm, \
             patch("handlers.admin.decorator.admin_repo") as mock_ar:

            mock_ar.is_admin.return_value = True

            from services.container import admin_service as real_svc
            with patch("handlers.admin.admin_handlers.admin_service", create=True) as mock_svc:
                # Mix of int and string values — the old {value:,} would crash on "pending"
                mock_svc.get_stats.return_value = {
                    "total_users": 1500,
                    "active_today": "pending",
                    "recommendations_sent": 42_000,
                }

                from handlers.admin.admin_handlers import handle_admin_stats
                # Must not raise
                await handle_admin_stats(chat_id="123")

        mock_sm.assert_called_once()
        output = mock_sm.call_args[0][1]
        assert "1,500" in output
        assert "pending" in output
        assert "42,000" in output


# ---------------------------------------------------------------------------
# Fix 3 — handle_admin_usage uses .get() for all provider and user keys
# ---------------------------------------------------------------------------

class TestUsageReportDefensiveAccess:
    """handle_admin_usage must not raise KeyError on missing provider/user keys."""

    @pytest.mark.asyncio
    async def test_missing_provider_keys_do_not_raise(self):
        """Provider rows with missing calls/tokens/cost must not raise KeyError."""
        with patch("handlers.admin.admin_handlers.send_message", new_callable=AsyncMock) as mock_sm, \
             patch("handlers.admin.decorator.admin_repo") as mock_ar:

            mock_ar.is_admin.return_value = True

            with patch("handlers.admin.admin_handlers.admin_service", create=True) as mock_svc:
                # Intentionally missing 'calls', 'total_tokens', 'estimated_cost_usd'
                mock_svc.get_usage_report.return_value = {
                    "providers": [{"provider": "openai"}],
                    "total_estimated_cost_usd": 0.0,
                    "top_users": [],
                }

                from handlers.admin.admin_handlers import handle_admin_usage
                await handle_admin_usage(chat_id="123", input_text="admin_usage 24")

        mock_sm.assert_called_once()
        output = mock_sm.call_args[0][1]
        assert "openai" in output

    @pytest.mark.asyncio
    async def test_missing_user_keys_do_not_raise(self):
        """User rows with missing chat_id/interactions must not raise KeyError."""
        with patch("handlers.admin.admin_handlers.send_message", new_callable=AsyncMock) as mock_sm, \
             patch("handlers.admin.decorator.admin_repo") as mock_ar:

            mock_ar.is_admin.return_value = True

            with patch("handlers.admin.admin_handlers.admin_service", create=True) as mock_svc:
                mock_svc.get_usage_report.return_value = {
                    "providers": [],
                    "total_estimated_cost_usd": 1.23,
                    # Intentionally missing 'chat_id' and 'interactions'
                    "top_users": [{}],
                }

                from handlers.admin.admin_handlers import handle_admin_usage
                await handle_admin_usage(chat_id="123", input_text="admin_usage 24")

        mock_sm.assert_called_once()

    @pytest.mark.asyncio
    async def test_string_cost_does_not_raise(self):
        """estimated_cost_usd returned as string must not crash the formatter."""
        with patch("handlers.admin.admin_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.admin.decorator.admin_repo") as mock_ar:

            mock_ar.is_admin.return_value = True

            with patch("handlers.admin.admin_handlers.admin_service", create=True) as mock_svc:
                mock_svc.get_usage_report.return_value = {
                    "providers": [{"provider": "openai", "estimated_cost_usd": "N/A"}],
                    "total_estimated_cost_usd": "N/A",
                    "top_users": [],
                }

                from handlers.admin.admin_handlers import handle_admin_usage
                # Must not raise
                await handle_admin_usage(chat_id="123")

    @pytest.mark.asyncio
    async def test_full_report_renders_correctly(self):
        """A complete well-formed report must render all expected fields."""
        with patch("handlers.admin.admin_handlers.send_message", new_callable=AsyncMock) as mock_sm, \
             patch("handlers.admin.decorator.admin_repo") as mock_ar:

            mock_ar.is_admin.return_value = True

            with patch("handlers.admin.admin_handlers.admin_service", create=True) as mock_svc:
                mock_svc.get_usage_report.return_value = {
                    "providers": [{
                        "provider": "openai",
                        "calls": 1500,
                        "total_tokens": 300_000,
                        "estimated_cost_usd": 0.45,
                    }],
                    "total_estimated_cost_usd": 0.45,
                    "top_users": [{"chat_id": "u123", "interactions": 42}],
                }

                from handlers.admin.admin_handlers import handle_admin_usage
                await handle_admin_usage(chat_id="123", input_text="admin_usage 48")

        output = mock_sm.call_args[0][1]
        assert "openai" in output
        assert "1,500" in output
        assert "300,000" in output
        assert "0.4500" in output
        assert "u123" in output
        assert "42" in output
