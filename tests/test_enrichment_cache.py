"""Tests for the write-through cache and fallback logic added to enrichment_service.

Scope
-----
These tests are SEPARATE from test_enrichment_service.py which covers the
original enrichment behaviour (trailer injection, Watchmode failure handling,
gather exception recovery, etc.).  This file focuses exclusively on the NEW
behaviour introduced when movie_metadata persistence was added:

  _persist_streaming:
    - Called (fire-and-forget) after a successful Watchmode response
    - Merges streaming_sources into existing data_json without clobbering OMDb fields
    - A repo failure inside _persist_streaming never propagates to the caller

  _get_streaming_from_cache:
    - Returns a formatted streaming summary when the repo has cached sources
    - Returns empty string when repo.get() returns None
    - Returns empty string when repo.get() raises

  _enrich_one (cache integration):
    - Watchmode returns empty list → falls back to cache
    - Watchmode raises exception  → falls back to cache
    - Cache hit after Watchmode failure → streaming field set on returned movie
    - Cache miss after Watchmode failure → streaming field stays None (safe degradation)

All tests inject a mock MovieMetadataRepository via EnrichmentService.__init__
so no Supabase or Redis calls are made.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from models.domain import MovieModel
from services.enrichment_service import EnrichmentService


# ---------------------------------------------------------------------------
# Fixtures & Factories
# ---------------------------------------------------------------------------

def make_movie(
    movie_id: str = "tt1375666",
    title: str = "Inception",
    year: str = "2010",
    streaming: str | None = None,
    **kwargs,
) -> MovieModel:
    return MovieModel(movie_id=movie_id, title=title, year=year, streaming=streaming, **kwargs)


def make_repo(
    get_return=None,
    upsert_side_effect=None,
    get_side_effect=None,
):
    """Build a mock MovieMetadataRepository with configurable behaviour."""
    repo = MagicMock()
    repo.get = AsyncMock(return_value=get_return, side_effect=get_side_effect)
    if upsert_side_effect:
        repo.upsert = AsyncMock(side_effect=upsert_side_effect)
    else:
        repo.upsert = AsyncMock(return_value=None)
    return repo


RAW_SOURCES = [{"name": "Netflix", "type": "sub"}, {"name": "Hulu", "type": "sub"}]
FORMATTED = "\U0001f4fa Netflix \xb7 Hulu"


# ---------------------------------------------------------------------------
# _persist_streaming
# ---------------------------------------------------------------------------

class TestPersistStreaming:
    @pytest.mark.asyncio
    async def test_persist_merges_sources_into_existing_data(self):
        """POSITIVE: _persist_streaming merges streaming_sources without removing OMDb fields."""
        existing = {"title": "Inception", "imdb_rating": "8.8"}
        repo = make_repo(get_return=existing)
        svc = EnrichmentService(metadata_repo=repo)

        await svc._persist_streaming("tt1375666", RAW_SOURCES)

        repo.upsert.assert_called_once()
        _, call_data = repo.upsert.call_args[0]
        assert call_data["streaming_sources"] == RAW_SOURCES
        assert call_data["title"] == "Inception"          # OMDb field preserved
        assert call_data["imdb_rating"] == "8.8"          # OMDb field preserved

    @pytest.mark.asyncio
    async def test_persist_creates_record_when_none_exists(self):
        """POSITIVE: When repo.get() returns None, upsert is still called with sources."""
        repo = make_repo(get_return=None)
        svc = EnrichmentService(metadata_repo=repo)

        await svc._persist_streaming("tt1375666", RAW_SOURCES)

        repo.upsert.assert_called_once()
        _, call_data = repo.upsert.call_args[0]
        assert call_data["streaming_sources"] == RAW_SOURCES

    @pytest.mark.asyncio
    async def test_persist_overwrites_stale_sources(self):
        """POSITIVE: Calling _persist_streaming twice updates streaming_sources."""
        old_sources = [{"name": "HBO", "type": "sub"}]
        repo = make_repo(get_return={"streaming_sources": old_sources})
        svc = EnrichmentService(metadata_repo=repo)

        await svc._persist_streaming("tt1375666", RAW_SOURCES)

        _, call_data = repo.upsert.call_args[0]
        assert call_data["streaming_sources"] == RAW_SOURCES  # new value

    @pytest.mark.asyncio
    async def test_persist_repo_exception_does_not_raise(self):
        """NEGATIVE: If upsert raises, _persist_streaming swallows the exception."""
        repo = make_repo(upsert_side_effect=Exception("DB timeout"))
        svc = EnrichmentService(metadata_repo=repo)

        # Must not raise
        await svc._persist_streaming("tt1375666", RAW_SOURCES)

    @pytest.mark.asyncio
    async def test_persist_get_exception_does_not_raise(self):
        """NEGATIVE: If repo.get() raises, _persist_streaming swallows the exception."""
        repo = make_repo(get_side_effect=Exception("connection refused"))
        svc = EnrichmentService(metadata_repo=repo)

        await svc._persist_streaming("tt1375666", RAW_SOURCES)
        # No assertion needed — just must not propagate


# ---------------------------------------------------------------------------
# _get_streaming_from_cache
# ---------------------------------------------------------------------------

class TestGetStreamingFromCache:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_formatted_summary(self):
        """POSITIVE: Cached sources are formatted and returned as a summary string."""
        cached_data = {"streaming_sources": RAW_SOURCES}
        repo = make_repo(get_return=cached_data)
        svc = EnrichmentService(metadata_repo=repo)

        with patch(
            "services.enrichment_service.watchmode_client.format_streaming_summary",
            return_value=FORMATTED,
        ):
            result = await svc._get_streaming_from_cache("tt1375666")

        assert result == FORMATTED

    @pytest.mark.asyncio
    async def test_cache_miss_returns_empty_string(self):
        """NEGATIVE: repo.get() returns None → empty string returned, no exception."""
        repo = make_repo(get_return=None)
        svc = EnrichmentService(metadata_repo=repo)

        result = await svc._get_streaming_from_cache("tt9999999")
        assert result == ""

    @pytest.mark.asyncio
    async def test_no_streaming_sources_key_returns_empty_string(self):
        """NEGATIVE: data_json exists but has no streaming_sources → empty string."""
        repo = make_repo(get_return={"title": "Inception", "imdb_rating": "8.8"})
        svc = EnrichmentService(metadata_repo=repo)

        result = await svc._get_streaming_from_cache("tt1375666")
        assert result == ""

    @pytest.mark.asyncio
    async def test_empty_sources_list_returns_empty_string(self):
        """NEGATIVE: streaming_sources is an empty list → format returns nothing."""
        repo = make_repo(get_return={"streaming_sources": []})
        svc = EnrichmentService(metadata_repo=repo)

        with patch(
            "services.enrichment_service.watchmode_client.format_streaming_summary",
            return_value="",
        ):
            result = await svc._get_streaming_from_cache("tt1375666")

        assert result == ""

    @pytest.mark.asyncio
    async def test_repo_exception_returns_empty_string(self):
        """NEGATIVE: repo.get() raises → empty string returned, no exception propagates."""
        repo = make_repo(get_side_effect=Exception("network error"))
        svc = EnrichmentService(metadata_repo=repo)

        result = await svc._get_streaming_from_cache("tt1375666")
        assert result == ""


# ---------------------------------------------------------------------------
# _enrich_one — cache integration
# ---------------------------------------------------------------------------

class TestEnrichOneCacheIntegration:
    @pytest.mark.asyncio
    async def test_successful_watchmode_triggers_persist_streaming(self):
        """POSITIVE: Successful Watchmode call schedules _persist_streaming."""
        repo = make_repo()
        svc = EnrichmentService(metadata_repo=repo)
        movie = make_movie()

        with patch(
            "services.enrichment_service.watchmode_client.get_streaming_sources",
            new=AsyncMock(return_value=RAW_SOURCES),
        ), patch(
            "services.enrichment_service.watchmode_client.format_streaming_summary",
            return_value=FORMATTED,
        ), patch(
            "asyncio.ensure_future"
        ) as mock_ensure:
            result = await svc._enrich_one(movie, chat_id="u1")

        assert result.streaming == FORMATTED
        # ensure_future was called once (fire-and-forget persist)
        mock_ensure.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_watchmode_result_falls_back_to_cache(self):
        """POSITIVE: Watchmode returns [] → cache consulted and streaming set from cache."""
        repo = make_repo(get_return={"streaming_sources": RAW_SOURCES})
        svc = EnrichmentService(metadata_repo=repo)
        movie = make_movie()

        with patch(
            "services.enrichment_service.watchmode_client.get_streaming_sources",
            new=AsyncMock(return_value=[]),  # empty — no live sources
        ), patch(
            "services.enrichment_service.watchmode_client.format_streaming_summary",
            side_effect=lambda sources: FORMATTED if sources else "",
        ):
            result = await svc._enrich_one(movie, chat_id="u1")

        assert result.streaming == FORMATTED

    @pytest.mark.asyncio
    async def test_empty_watchmode_result_cache_miss_leaves_streaming_none(self):
        """NEGATIVE: Watchmode returns [] and cache is empty → streaming stays None."""
        repo = make_repo(get_return=None)
        svc = EnrichmentService(metadata_repo=repo)
        movie = make_movie()

        with patch(
            "services.enrichment_service.watchmode_client.get_streaming_sources",
            new=AsyncMock(return_value=[]),
        ), patch(
            "services.enrichment_service.watchmode_client.format_streaming_summary",
            return_value="",
        ):
            result = await svc._enrich_one(movie, chat_id="u1")

        assert result.streaming is None

    @pytest.mark.asyncio
    async def test_watchmode_exception_falls_back_to_cache(self):
        """POSITIVE: Watchmode raises → cache consulted → streaming set from cache."""
        repo = make_repo(get_return={"streaming_sources": RAW_SOURCES})
        svc = EnrichmentService(metadata_repo=repo)
        movie = make_movie()

        with patch(
            "services.enrichment_service.watchmode_client.get_streaming_sources",
            new=AsyncMock(side_effect=Exception("503 Watchmode down")),
        ), patch(
            "services.enrichment_service.watchmode_client.format_streaming_summary",
            return_value=FORMATTED,
        ), patch("services.enrichment_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            result = await svc._enrich_one(movie, chat_id="u1")

        assert result.streaming == FORMATTED
        mock_batcher.emit.assert_called_once()  # error still logged

    @pytest.mark.asyncio
    async def test_watchmode_exception_cache_miss_leaves_streaming_none(self):
        """NEGATIVE: Watchmode raises and cache is empty → streaming stays None."""
        repo = make_repo(get_return=None)
        svc = EnrichmentService(metadata_repo=repo)
        movie = make_movie()

        with patch(
            "services.enrichment_service.watchmode_client.get_streaming_sources",
            new=AsyncMock(side_effect=Exception("timeout")),
        ), patch("services.enrichment_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            result = await svc._enrich_one(movie, chat_id="u1")

        assert isinstance(result, MovieModel)
        assert result.streaming is None

    @pytest.mark.asyncio
    async def test_watchmode_exception_movie_still_returned(self):
        """NEGATIVE: Watchmode raises → movie is never dropped, title is preserved."""
        repo = make_repo(get_return=None)
        svc = EnrichmentService(metadata_repo=repo)
        movie = make_movie(title="Interstellar", movie_id="tt0816692")

        with patch(
            "services.enrichment_service.watchmode_client.get_streaming_sources",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ), patch("services.enrichment_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            result = await svc._enrich_one(movie, chat_id="u1")

        assert result.title == "Interstellar"

    @pytest.mark.asyncio
    async def test_no_persist_when_watchmode_returns_empty(self):
        """NEGATIVE: Watchmode returns [] → _persist_streaming must NOT be called."""
        repo = make_repo(get_return=None)
        svc = EnrichmentService(metadata_repo=repo)
        movie = make_movie()

        with patch(
            "services.enrichment_service.watchmode_client.get_streaming_sources",
            new=AsyncMock(return_value=[]),
        ), patch(
            "services.enrichment_service.watchmode_client.format_streaming_summary",
            return_value="",
        ), patch("asyncio.ensure_future") as mock_ensure:
            await svc._enrich_one(movie, chat_id="u1")

        mock_ensure.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_movie_id_skips_watchmode_and_cache(self):
        """NEGATIVE: movie_id not starting with 'tt' → Watchmode and cache never called."""
        repo = make_repo()
        svc = EnrichmentService(metadata_repo=repo)
        movie = make_movie(movie_id="slug_inception")

        with patch(
            "services.enrichment_service.watchmode_client.get_streaming_sources",
        ) as mock_wm:
            await svc._enrich_one(movie, chat_id="u1")

        mock_wm.assert_not_called()
        repo.get.assert_not_called()
        repo.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_streaming_skips_watchmode_and_cache(self):
        """NEGATIVE: movie already has streaming → Watchmode and cache are both skipped."""
        repo = make_repo()
        svc = EnrichmentService(metadata_repo=repo)
        movie = make_movie(streaming="\U0001f4fa Prime Video")

        with patch(
            "services.enrichment_service.watchmode_client.get_streaming_sources",
        ) as mock_wm:
            result = await svc._enrich_one(movie, chat_id="u1")

        mock_wm.assert_not_called()
        repo.get.assert_not_called()
        assert result.streaming == "\U0001f4fa Prime Video"
