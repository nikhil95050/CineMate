"""Unit tests for enrichment_service.

Covers:
- Happy path: trailer URL set, streaming info added
- Watchmode fails -> movie NOT dropped, original returned
- Watchmode raises -> error_batcher.emit() called with correct payload
- Non-IMDb movie_id skips Watchmode entirely
- Movie already has trailer/streaming -> no overwrite
- asyncio.gather exception -> original movie kept (never dropped)
- Multiple movies: partial failure still returns full list
- chat_id carried through to error payloads

Note: _enrich_one is an instance method on EnrichmentService (not a
module-level function). Tests call it via EnrichmentService()._enrich_one()
and inject a no-op mock repo so the write-through cache paths don't fire.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.domain import MovieModel
from services.enrichment_service import EnrichmentService, enrich_movies


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_movie(movie_id="tt1375666", title="Inception", year="2010", **kwargs) -> MovieModel:
    return MovieModel(movie_id=movie_id, title=title, year=year, **kwargs)


def make_noop_repo():
    """Mock repo that does nothing -- prevents real DB calls in _persist_streaming."""
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    repo.upsert = AsyncMock(return_value=True)
    return repo


# ---------------------------------------------------------------------------
# _enrich_one -- unit tests
# ---------------------------------------------------------------------------

class TestEnrichOne:
    @pytest.mark.asyncio
    async def test_trailer_fallback_url_set_when_missing(self):
        """POSITIVE: No trailer -> YouTube search URL injected."""
        svc = EnrichmentService(metadata_repo=make_noop_repo())
        movie = make_movie(trailer=None)
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=AsyncMock(return_value=[])):
            result = await svc._enrich_one(movie)
        assert result.trailer is not None
        assert "youtube.com/results" in result.trailer
        assert "Inception" in result.trailer

    @pytest.mark.asyncio
    async def test_existing_trailer_not_overwritten(self):
        """POSITIVE: Existing trailer must not be replaced."""
        svc = EnrichmentService(metadata_repo=make_noop_repo())
        movie = make_movie(trailer="https://existing.trailer/url")
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=AsyncMock(return_value=[])):
            result = await svc._enrich_one(movie)
        assert result.trailer == "https://existing.trailer/url"

    @pytest.mark.asyncio
    async def test_streaming_info_added_for_imdb_id(self):
        """POSITIVE: Watchmode returns sources -> streaming field set."""
        svc = EnrichmentService(metadata_repo=make_noop_repo())
        sources = [{"name": "Netflix", "type": "sub"}, {"name": "Hulu", "type": "sub"}]
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=AsyncMock(return_value=sources)), \
             patch("services.enrichment_service.watchmode_client.format_streaming_summary",
                   return_value="\U0001f4fa Netflix \xb7 Hulu"), \
             patch("asyncio.ensure_future"):
            result = await svc._enrich_one(make_movie())
        assert result.streaming == "\U0001f4fa Netflix \xb7 Hulu"

    @pytest.mark.asyncio
    async def test_non_imdb_id_skips_watchmode(self):
        """POSITIVE: movie_id not starting with 'tt' -> Watchmode never called."""
        svc = EnrichmentService(metadata_repo=make_noop_repo())
        movie = make_movie(movie_id="slug_inception")
        mock_wm = AsyncMock()
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources", new=mock_wm):
            await svc._enrich_one(movie)
        mock_wm.assert_not_called()

    @pytest.mark.asyncio
    async def test_existing_streaming_not_overwritten(self):
        """POSITIVE: Pre-populated streaming field must not be replaced."""
        svc = EnrichmentService(metadata_repo=make_noop_repo())
        movie = make_movie(streaming="\U0001f4fa Prime Video")
        mock_wm = AsyncMock()
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources", new=mock_wm):
            result = await svc._enrich_one(movie)
        mock_wm.assert_not_called()
        assert result.streaming == "\U0001f4fa Prime Video"

    @pytest.mark.asyncio
    async def test_watchmode_exception_returns_movie_not_raises(self):
        """NEGATIVE: Watchmode raises -> no exception propagated, movie returned."""
        svc = EnrichmentService(metadata_repo=make_noop_repo())
        movie = make_movie()
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=AsyncMock(side_effect=Exception("timeout"))):
            result = await svc._enrich_one(movie)
        assert isinstance(result, MovieModel)
        assert result.title == "Inception"

    @pytest.mark.asyncio
    async def test_watchmode_exception_emits_to_error_batcher(self):
        """NEGATIVE: Watchmode raises -> error_batcher.emit() called once."""
        svc = EnrichmentService(metadata_repo=make_noop_repo())
        movie = make_movie()
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=AsyncMock(side_effect=Exception("connection reset"))), \
             patch("services.enrichment_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            await svc._enrich_one(movie, chat_id="123")
        mock_batcher.emit.assert_called_once()
        payload = mock_batcher.emit.call_args[0][0]
        assert payload["error_type"] == "watchmode_enrichment_error"
        assert payload["chat_id"] == "123"
        assert "tt1375666" in payload["error_message"]

    @pytest.mark.asyncio
    async def test_watchmode_exception_error_payload_has_workflow_step(self):
        """NEGATIVE: Error payload must have the correct workflow_step."""
        svc = EnrichmentService(metadata_repo=make_noop_repo())
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("services.enrichment_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            await svc._enrich_one(make_movie(), chat_id="999")
        payload = mock_batcher.emit.call_args[0][0]
        assert payload["workflow_step"] == "enrichment._enrich_one"
        assert payload["intent"] == "enrichment"


# ---------------------------------------------------------------------------
# enrich_movies -- integration-style tests
# ---------------------------------------------------------------------------

class TestEnrichMovies:
    @pytest.mark.asyncio
    async def test_all_movies_returned_on_success(self):
        """POSITIVE: All input movies appear in output."""
        movies = [make_movie("tt000001", "Film A"), make_movie("tt000002", "Film B")]
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=AsyncMock(return_value=[])):
            result = await enrich_movies(movies)
        assert len(result) == 2
        assert {m.title for m in result} == {"Film A", "Film B"}

    @pytest.mark.asyncio
    async def test_watchmode_failure_does_not_drop_movie(self):
        """NEGATIVE: One Watchmode failure -> movie still in output."""
        movies = [make_movie("tt000001", "Film A"), make_movie("tt000002", "Film B")]
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=AsyncMock(side_effect=Exception("503 Service Unavailable"))):
            result = await enrich_movies(movies, chat_id="u1")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_partial_watchmode_failure_keeps_all_movies(self):
        """NEGATIVE: Second call fails -> still 3 movies out."""
        movies = [
            make_movie("tt000001", "Film A"),
            make_movie("tt000002", "Film B"),
            make_movie("tt000003", "Film C"),
        ]
        streaming_ok = [{"name": "Netflix", "type": "sub"}]
        call_count = 0

        async def side_effect(imdb_id):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise Exception("rate limited")
            return streaming_ok

        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=side_effect), \
             patch("services.enrichment_service.watchmode_client.format_streaming_summary",
                   return_value="\U0001f4fa Netflix"), \
             patch("asyncio.ensure_future"):
            result = await enrich_movies(movies, chat_id="u1")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_gather_exception_keeps_original_movie(self):
        """NEGATIVE: asyncio.gather captures an Exception -> original MovieModel kept."""
        movies = [make_movie("tt000001", "Film A")]

        async def always_raises(self_arg, movie, chat_id="system"):
            raise RuntimeError("unexpected internal crash")

        with patch.object(EnrichmentService, "_enrich_one", always_raises), \
             patch("services.enrichment_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            result = await enrich_movies(movies, chat_id="u1")

        assert len(result) == 1
        assert result[0].title == "Film A"
        mock_batcher.emit.assert_called_once()
        payload = mock_batcher.emit.call_args[0][0]
        assert payload["error_type"] == "enrichment_gather_exception"

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_list(self):
        """POSITIVE: Empty input -> empty output, no crashes."""
        result = await enrich_movies([])
        assert result == []

    @pytest.mark.asyncio
    async def test_chat_id_in_all_error_payloads(self):
        """NEGATIVE: chat_id='test_user' must appear in every emitted error payload."""
        movies = [make_movie("tt000001", "Film A"), make_movie("tt000002", "Film B")]
        emitted = []
        with patch("services.enrichment_service.watchmode_client.get_streaming_sources",
                   new=AsyncMock(side_effect=Exception("fail"))), \
             patch("services.enrichment_service.error_batcher") as mock_batcher:
            mock_batcher.emit.side_effect = emitted.append
            await enrich_movies(movies, chat_id="test_user")
        assert emitted
        assert all(row["chat_id"] == "test_user" for row in emitted)
