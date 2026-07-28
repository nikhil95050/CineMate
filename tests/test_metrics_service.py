"""Tests for metrics service."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from services.metrics_service import (
    _aggregate_api_usage, _aggregate_user_activity, _metrics_loop,
    start_metrics_aggregation, AGGREGATION_INTERVAL,
)


class TestMetricsService:
    def test_aggregation_interval(self):
        assert AGGREGATION_INTERVAL == 300

    @pytest.mark.asyncio
    async def test_aggregate_api_usage_no_supabase(self):
        with patch("services.metrics_service._aggregate_api_usage") as mock_agg:
            mock_agg.return_value = None
        with patch("config.supabase_client.is_configured", return_value=False):
            await _aggregate_api_usage()

    @pytest.mark.asyncio
    async def test_aggregate_api_usage_with_data(self):
        from datetime import datetime, timezone, timedelta
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        rows = [
            {"provider": "tmdb", "total_tokens": 100, "prompt_tokens": 50, "completion_tokens": 50},
            {"provider": "tmdb", "total_tokens": 200, "prompt_tokens": 100, "completion_tokens": 100},
            {"provider": "perplexity", "total_tokens": 500, "prompt_tokens": 300, "completion_tokens": 200},
        ]
        with patch("config.supabase_client.is_configured", return_value=True):
            with patch("config.supabase_client.select_rows", return_value=(rows, None)):
                with patch("config.supabase_client.insert_rows", return_value=(None, None)):
                    await _aggregate_api_usage()

    @pytest.mark.asyncio
    async def test_aggregate_api_usage_with_error(self):
        with patch("config.supabase_client.is_configured", return_value=True):
            with patch("config.supabase_client.select_rows", return_value=(None, "db error")):
                await _aggregate_api_usage()

    @pytest.mark.asyncio
    async def test_aggregate_user_activity(self):
        rows = [
            {"chat_id": "user1", "intent": "trending"},
            {"chat_id": "user1", "intent": "recommend"},
            {"chat_id": "user2", "intent": "like"},
            {"chat_id": "user2", "intent": "search"},
        ]
        with patch("config.supabase_client.is_configured", return_value=True):
            with patch("config.supabase_client.select_rows", return_value=(rows, None)):
                with patch("config.supabase_client.insert_rows", return_value=(None, None)):
                    await _aggregate_user_activity()

    @pytest.mark.asyncio
    async def test_aggregate_user_activity_no_data(self):
        with patch("config.supabase_client.is_configured", return_value=True):
            with patch("config.supabase_client.select_rows", return_value=(None, "error")):
                await _aggregate_user_activity()

    @pytest.mark.asyncio
    async def test_metrics_loop_runs_once(self):
        with patch("asyncio.sleep", side_effect=[None, KeyboardInterrupt()]):
            with patch("services.metrics_service._aggregate_api_usage", AsyncMock()):
                with patch("services.metrics_service._aggregate_user_activity", AsyncMock()):
                    try:
                        await _metrics_loop()
                    except KeyboardInterrupt:
                        pass

    @pytest.mark.asyncio
    async def test_start_metrics_aggregation_running_loop(self):
        import asyncio
        async def dummy():
            start_metrics_aggregation()
        await dummy()
