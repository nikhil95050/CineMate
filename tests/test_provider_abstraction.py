"""Tests for provider abstraction layer."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from clients.provider_base import ApiProvider, MovieMetadataProvider, LlmProvider, StreamingProvider


class TestApiProvider:
    def test_provider_name_default(self):
        provider = ApiProvider()
        assert provider.provider_name == ""
        assert provider.daily_budget == 1000

    def test_api_key_default(self):
        provider = ApiProvider()
        assert provider._api_key() == ""

    @pytest.mark.asyncio
    async def test_check_health_no_health_service(self):
        provider = ApiProvider()
        with patch.object(provider, "_health", return_value=None):
            result = await provider._check_health()
            assert result is True

    @pytest.mark.asyncio
    async def test_check_health_circuit_open(self):
        provider = ApiProvider()
        provider.provider_name = "test_provider"
        mock_hs = MagicMock()
        mock_hs.is_healthy = MagicMock(return_value=False)
        with patch.object(provider, "_health", return_value=mock_hs):
            result = await provider._check_health()
            assert result is False
            mock_hs.is_healthy.assert_called_once_with("test_provider")

    @pytest.mark.asyncio
    async def test_check_health_circuit_closed(self):
        provider = ApiProvider()
        provider.provider_name = "test_provider"
        mock_hs = MagicMock()
        mock_hs.is_healthy = MagicMock(return_value=True)
        with patch.object(provider, "_health", return_value=mock_hs):
            result = await provider._check_health()
            assert result is True


class TestMovieMetadataProvider:
    def test_is_subclass(self):
        assert issubclass(MovieMetadataProvider, ApiProvider)

    def test_abstract_methods(self):
        assert hasattr(MovieMetadataProvider, "get_by_title")
        assert hasattr(MovieMetadataProvider, "get_by_id")
        assert hasattr(MovieMetadataProvider, "search")

    def test_has_required_abstracts(self):
        from abc import abstractmethod
        assert MovieMetadataProvider.get_by_title.__isabstractmethod__
        assert MovieMetadataProvider.get_by_id.__isabstractmethod__
        assert MovieMetadataProvider.search.__isabstractmethod__


class TestLlmProvider:
    def test_is_subclass(self):
        assert issubclass(LlmProvider, ApiProvider)

    def test_has_chat_method(self):
        from abc import abstractmethod
        assert LlmProvider.chat.__isabstractmethod__


class TestStreamingProvider:
    def test_is_subclass(self):
        assert issubclass(StreamingProvider, ApiProvider)

    def test_has_required_abstracts(self):
        from abc import abstractmethod
        assert StreamingProvider.get_sources.__isabstractmethod__
        assert StreamingProvider.format_summary.__isabstractmethod__
