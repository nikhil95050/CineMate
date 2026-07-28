"""Abstract base for external API clients.

All external providers (Perplexity, TMDB, OMDb, Watchmode, etc.) follow the
same pattern: circuit-breaker check, API call, success/failure reporting,
and usage logging. This base class consolidates that pattern.
"""
from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any, Optional

from services.logging_service import LoggingService, get_logger, error_batcher
from utils.time_utils import utc_now_iso


class ApiProvider(ABC):
    """Base class for external API providers with circuit-breaker integration."""

    provider_name: str = ""
    daily_budget: int = 1000

    def __init__(self) -> None:
        self.logger = get_logger(self.provider_name)

    def _api_key(self) -> str:
        return ""

    def _health(self):
        try:
            from services.container import health_service
            return health_service
        except Exception:
            return None

    async def _check_health(self) -> bool:
        hs = self._health()
        if hs is not None:
            is_healthy = await asyncio.to_thread(hs.is_healthy, self.provider_name)
            if not is_healthy:
                self.logger.warning("[%s] circuit OPEN - call skipped", self.provider_name)
                return False
        return True

    def _report_success(self) -> None:
        hs = self._health()
        if hs is not None:
            asyncio.create_task(asyncio.to_thread(hs.report_success, self.provider_name))
            asyncio.create_task(asyncio.to_thread(hs.increment_daily_calls, self.provider_name))

    def _report_failure(self) -> None:
        hs = self._health()
        if hs is not None:
            asyncio.create_task(asyncio.to_thread(hs.report_failure, self.provider_name))

    def _log_usage(self, action: str, chat_id: str = "system", **tokens: Optional[int]) -> None:
        LoggingService.log_api_usage(
            provider=self.provider_name,
            action=action,
            chat_id=chat_id,
            **tokens,
        )

    def _emit_error(self, chat_id: str, error_type: str, message: str, step: str) -> None:
        error_batcher.emit({
            "chat_id": str(chat_id),
            "error_type": error_type,
            "error_message": str(message)[:2000],
            "workflow_step": step,
            "intent": "provider",
            "request_id": "N/A",
            "raw_payload": "{}",
            "timestamp": utc_now_iso(),
        })


class LlmProvider(ApiProvider):
    """Base for LLM chat-completion providers (Perplexity, Omniroute, etc.)."""

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, str]],
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 1500,
        timeout: float = 30.0,
        chat_id: str = "system",
    ) -> Optional[str]:
        ...


class MovieMetadataProvider(ApiProvider):
    """Base for movie metadata providers (TMDB, OMDb)."""

    @abstractmethod
    async def get_by_title(
        self,
        title: str,
        year: Optional[str] = None,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    async def get_by_id(
        self,
        movie_id: str,
        chat_id: str = "system",
        timeout: float = 10.0,
    ) -> Optional[dict[str, Any]]:
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        chat_id: str = "system",
        limit: int = 5,
        timeout: float = 10.0,
    ) -> list[dict[str, Any]]:
        ...


class StreamingProvider(ApiProvider):
    """Base for streaming-availability providers (Watchmode, JustWatch)."""

    @abstractmethod
    async def get_sources(
        self,
        imdb_id: str,
        chat_id: str = "system",
    ) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def format_summary(self, sources: list[dict[str, Any]]) -> str:
        ...
