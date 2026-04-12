"""
Regression tests for Bug fixes 1–5.

Bug 1 — Flow stops when Perplexity fails          (discovery_service.py)
Bug 2 — Movies silently dropped when OMDb fails   (discovery_service.py)
Bug 3 — Movies silently dropped when Watchmode fails (enrichment_service.py)
Bug 4 — Errors not logged to DB                   (discovery + enrichment)
Bug 5 — chat_id in error rows was always 'system' (recommendation_service.py)
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ============================================================
# Shared helpers
# ============================================================

def _make_movie(title="Inception", movie_id="tt1375666", year="2010"):
    """Return a minimal MovieModel-like object backed by a MagicMock."""
    from models.domain import MovieModel
    return MovieModel(movie_id=movie_id, title=title, year=year, reason="Great film")


# ============================================================
# BUG 1 — Flow stops when Perplexity fails
# ============================================================

class TestBug1PerplexityFallback:
    """When Perplexity returns None/empty the DB fallback must be invoked
    and a non-empty list must be returned (not []).
    """

    @pytest.mark.asyncio
    async def test_perplexity_none_triggers_db_fallback(self):
        """Perplexity returns None → _fetch_from_metadata_db is called."""
        fallback_movies = [_make_movie("The Matrix"), _make_movie("Interstellar")]

        with (
            patch("services.discovery_service.perplexity_client.chat", new=AsyncMock(return_value=None)),
            patch(
                "services.discovery_service._fetch_from_metadata_db",
                new=AsyncMock(return_value=fallback_movies),
            ) as mock_fallback,
            patch("services.discovery_service.error_batcher") as mock_batcher,
        ):
            from services.discovery_service import DiscoveryService
            svc = DiscoveryService()
            result = await svc.discover(mode="trending", chat_id="99999")

        assert result == fallback_movies, "Must return DB fallback movies"
        mock_fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_perplexity_empty_string_triggers_db_fallback(self):
        """Perplexity returns empty string → DB fallback is invoked."""
        fallback_movies = [_make_movie()]

        with (
            patch("services.discovery_service.perplexity_client.chat", new=AsyncMock(return_value="")),
            patch(
                "services.discovery_service._fetch_from_metadata_db",
                new=AsyncMock(return_value=fallback_movies),
            ) as mock_fallback,
            patch("services.discovery_service.error_batcher"),
        ):
            from services.discovery_service import DiscoveryService
            svc = DiscoveryService()
            result = await svc.discover(mode="trending", chat_id="99999")

        assert result == fallback_movies
        mock_fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_perplexity_unparseable_json_triggers_db_fallback(self):
        """Perplexity returns prose with no valid JSON array → DB fallback."""
        fallback_movies = [_make_movie()]

        with (
            patch(
                "services.discovery_service.perplexity_client.chat",
                new=AsyncMock(return_value="Sorry, I cannot help with that."),
            ),
            patch(
                "services.discovery_service._fetch_from_metadata_db",
                new=AsyncMock(return_value=fallback_movies),
            ) as mock_fallback,
            patch("services.discovery_service.error_batcher"),
        ):
            from services.discovery_service import DiscoveryService
            svc = DiscoveryService()
            result = await svc.discover(mode="surprise", chat_id="99999")

        assert result == fallback_movies
        mock_fallback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_successful_perplexity_does_not_call_db_fallback(self):
        """When Perplexity works normally, DB fallback must NOT be called."""
        good_response = '[{"title": "Dune", "year": "2021", "reason": "Epic sci-fi"}]'
        enriched = _make_movie("Dune", "tt1160419", "2021")

        with (
            patch(
                "services.discovery_service.perplexity_client.chat",
                new=AsyncMock(return_value=good_response),
            ),
            patch(
                "services.discovery_service._enrich_with_omdb",
                new=AsyncMock(return_value=enriched),
            ),
            patch(
                "services.discovery_service._fetch_from_metadata_db",
                new=AsyncMock(return_value=[]),
            ) as mock_fallback,
        ):
            from services.discovery_service import DiscoveryService
            svc = DiscoveryService()
            result = await svc.discover(mode="trending", chat_id="99999")

        mock_fallback.assert_not_awaited()
        assert len(result) == 1


# ============================================================
# BUG 2 — Movies silently dropped when OMDb fails
# ============================================================

class TestBug2OMDbFallback:
    """_enrich_with_omdb must always return a MovieModel — never raise."""

    @pytest.mark.asyncio
    async def test_omdb_exception_returns_original_stub(self):
        """If OMDb raises, the original stub is returned intact."""
        stub = _make_movie("Inception")

        with (
            patch(
                "services.discovery_service.omdb_client.get_by_title",
                new=AsyncMock(side_effect=ConnectionError("OMDb down")),
            ),
            patch("services.discovery_service.error_batcher"),
        ):
            from services.discovery_service import _enrich_with_omdb
            result = await _enrich_with_omdb(stub, chat_id="99999")

        assert result is stub, "Original stub must be returned when OMDb raises"
        assert result.title == "Inception"
        assert result.reason == "Great film"

    @pytest.mark.asyncio
    async def test_omdb_returns_none_keeps_stub(self):
        """If OMDb returns None (no match), the original stub is preserved."""
        stub = _make_movie("Unknown Film 1985")

        with patch(
            "services.discovery_service.omdb_client.get_by_title",
            new=AsyncMock(return_value=None),
        ):
            from services.discovery_service import _enrich_with_omdb
            result = await _enrich_with_omdb(stub, chat_id="99999")

        assert result is stub

    @pytest.mark.asyncio
    async def test_discover_omdb_failure_does_not_drop_movies(self):
        """Even if every OMDb call fails, discover() must return the stubs."""
        llm_response = (
            '[{"title": "Film A", "year": "2020", "reason": "x"},' 
            '{"title": "Film B", "year": "2019", "reason": "y"}]'
        )

        with (
            patch(
                "services.discovery_service.perplexity_client.chat",
                new=AsyncMock(return_value=llm_response),
            ),
            patch(
                "services.discovery_service.omdb_client.get_by_title",
                new=AsyncMock(side_effect=TimeoutError("OMDb timeout")),
            ),
            patch("services.discovery_service.error_batcher"),
        ):
            from services.discovery_service import DiscoveryService
            svc = DiscoveryService()
            result = await svc.discover(mode="trending", chat_id="99999")

        assert len(result) == 2, "Both stubs must survive OMDb failure"
        titles = {m.title for m in result}
        assert "Film A" in titles and "Film B" in titles

    @pytest.mark.asyncio
    async def test_omdb_success_enriches_stub(self):
        """Successful OMDb response must override stub fields."""
        stub = _make_movie("Inception")
        omdb_data = {
            "imdbID": "tt1375666",
            "Year": "2010",
            "imdbRating": "8.8",
            "Genre": "Action, Adventure, Sci-Fi",
            "Language": "English",
            "Plot": "A thief who steals corporate secrets.",
            "Poster": "https://example.com/inception.jpg",
        }

        with patch(
            "services.discovery_service.omdb_client.get_by_title",
            new=AsyncMock(return_value=omdb_data),
        ):
            from services.discovery_service import _enrich_with_omdb
            result = await _enrich_with_omdb(stub, chat_id="99999")

        assert result.rating == 8.8
        assert result.genres == "Action, Adventure, Sci-Fi"
        assert result.poster == "https://example.com/inception.jpg"


# ============================================================
# BUG 3 — Movies silently dropped when Watchmode fails
# ============================================================

class TestBug3WatchmodeFallback:
    """enrichment_service must never drop a movie because Watchmode raises."""

    @pytest.mark.asyncio
    async def test_watchmode_exception_keeps_original_movie(self):
        """If _enrich_one raises for a movie, the original is kept."""
        movies = [_make_movie("Parasite"), _make_movie("1917")]

        with (
            patch(
                "services.enrichment_service.watchmode_client",
                new=MagicMock(),
            ),
            patch(
                "services.enrichment_service.EnrichmentService._enrich_one",
                new=AsyncMock(side_effect=ConnectionError("Watchmode down")),
            ),
            patch("services.enrichment_service.error_batcher"),
        ):
            from services.enrichment_service import EnrichmentService
            svc = EnrichmentService()
            result = await svc.enrich_movies(movies, chat_id="99999")

        assert len(result) == 2, "No movies should be dropped on Watchmode failure"
        titles = {m.title for m in result}
        assert "Parasite" in titles and "1917" in titles

    @pytest.mark.asyncio
    async def test_watchmode_partial_failure_keeps_all_movies(self):
        """If Watchmode succeeds for movie 1 but fails for movie 2, both survive."""
        movie_a = _make_movie("Film A", "tt0000001")
        movie_b = _make_movie("Film B", "tt0000002")
        enriched_a = _make_movie("Film A", "tt0000001")

        async def selective_enrich(movie, **kwargs):
            if movie.title == "Film A":
                return enriched_a
            raise RuntimeError("Watchmode unavailable for Film B")

        with (
            patch(
                "services.enrichment_service.EnrichmentService._enrich_one",
                new=selective_enrich,
            ),
            patch("services.enrichment_service.error_batcher"),
        ):
            from services.enrichment_service import EnrichmentService
            svc = EnrichmentService()
            result = await svc.enrich_movies([movie_a, movie_b], chat_id="99999")

        assert len(result) == 2
        assert any(m.title == "Film A" for m in result)
        assert any(m.title == "Film B" for m in result)

    @pytest.mark.asyncio
    async def test_empty_movie_list_returns_empty(self):
        """No crash on empty input."""
        from services.enrichment_service import EnrichmentService
        svc = EnrichmentService()
        result = await svc.enrich_movies([], chat_id="99999")
        assert result == []


# ============================================================
# BUG 4 — Errors not logged to DB
# ============================================================

class TestBug4ErrorLogging:
    """Every failure path must call error_batcher.emit() with correct fields."""

    @pytest.mark.asyncio
    async def test_perplexity_failure_emits_to_error_batcher(self):
        with (
            patch("services.discovery_service.perplexity_client.chat", new=AsyncMock(return_value=None)),
            patch("services.discovery_service._fetch_from_metadata_db", new=AsyncMock(return_value=[])),
            patch("services.discovery_service.error_batcher") as mock_batcher,
        ):
            from services.discovery_service import DiscoveryService
            svc = DiscoveryService()
            await svc.discover(mode="trending", chat_id="99999", request_id="req-001")

        mock_batcher.emit.assert_called()
        emitted = mock_batcher.emit.call_args_list[0][0][0]
        assert emitted["error_type"] == "perplexity_empty_response"
        assert emitted["workflow_step"] == "discovery.discover"
        assert emitted["intent"] == "trending"

    @pytest.mark.asyncio
    async def test_parse_failure_emits_to_error_batcher_with_raw_payload(self):
        bad_response = "I cannot provide movie lists."
        with (
            patch(
                "services.discovery_service.perplexity_client.chat",
                new=AsyncMock(return_value=bad_response),
            ),
            patch("services.discovery_service._fetch_from_metadata_db", new=AsyncMock(return_value=[])),
            patch("services.discovery_service.error_batcher") as mock_batcher,
        ):
            from services.discovery_service import DiscoveryService
            svc = DiscoveryService()
            await svc.discover(mode="surprise", chat_id="99999", request_id="req-002")

        mock_batcher.emit.assert_called()
        emitted = mock_batcher.emit.call_args_list[0][0][0]
        assert emitted["error_type"] == "perplexity_parse_failed"
        assert bad_response[:500] in emitted["raw_payload"]

    @pytest.mark.asyncio
    async def test_omdb_failure_emits_to_error_batcher(self):
        stub = _make_movie()
        with (
            patch(
                "services.discovery_service.omdb_client.get_by_title",
                new=AsyncMock(side_effect=TimeoutError("timeout")),
            ),
            patch("services.discovery_service.error_batcher") as mock_batcher,
        ):
            from services.discovery_service import _enrich_with_omdb
            await _enrich_with_omdb(stub, chat_id="12345")

        mock_batcher.emit.assert_called_once()
        emitted = mock_batcher.emit.call_args[0][0]
        assert emitted["error_type"] == "omdb_enrichment_error"
        assert emitted["chat_id"] == "12345"
        assert emitted["workflow_step"] == "discovery._enrich_with_omdb"

    @pytest.mark.asyncio
    async def test_emitted_row_has_required_keys(self):
        """Every error row must contain the mandatory DB columns."""
        required_keys = {
            "chat_id", "error_type", "error_message",
            "workflow_step", "intent", "request_id", "raw_payload", "timestamp",
        }
        stub = _make_movie()
        with (
            patch(
                "services.discovery_service.omdb_client.get_by_title",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch("services.discovery_service.error_batcher") as mock_batcher,
        ):
            from services.discovery_service import _enrich_with_omdb
            await _enrich_with_omdb(stub, chat_id="99999")

        emitted = mock_batcher.emit.call_args[0][0]
        missing = required_keys - emitted.keys()
        assert not missing, f"Missing keys in error row: {missing}"

    @pytest.mark.asyncio
    async def test_error_message_truncated_to_2000_chars(self):
        """error_message must never exceed 2000 characters."""
        long_error = "x" * 5000
        stub = _make_movie()
        with (
            patch(
                "services.discovery_service.omdb_client.get_by_title",
                new=AsyncMock(side_effect=RuntimeError(long_error)),
            ),
            patch("services.discovery_service.error_batcher") as mock_batcher,
        ):
            from services.discovery_service import _enrich_with_omdb
            await _enrich_with_omdb(stub, chat_id="99999")

        emitted = mock_batcher.emit.call_args[0][0]
        assert len(emitted["error_message"]) <= 2000


# ============================================================
# BUG 5 — chat_id in error rows was always "system"
# ============================================================

class TestBug5ChatIdPropagation:
    """Real chat_id must flow from RecommendationService down to every error row."""

    @pytest.mark.asyncio
    async def test_discover_receives_real_chat_id(self):
        """RecommendationService must pass the caller's chat_id to discover()."""
        mock_discover = AsyncMock(return_value=[])
        mock_enrich  = AsyncMock(return_value=[])

        with (
            patch("services.recommendation_service.DiscoveryService.discover", mock_discover),
            patch("services.recommendation_service.EnrichmentService.enrich_movies", mock_enrich),
            patch("services.recommendation_service.error_batcher"),
        ):
            from services.recommendation_service import RecommendationService
            svc = RecommendationService()
            await svc.get_recommendations(
                mode="trending",
                chat_id="USER_123",
                request_id="req-abc",
            )

        _, kwargs = mock_discover.call_args
        assert kwargs.get("chat_id") == "USER_123", (
            f"discover() received chat_id={kwargs.get('chat_id')!r}, expected 'USER_123'"
        )

    @pytest.mark.asyncio
    async def test_enrich_movies_receives_real_chat_id(self):
        """RecommendationService must pass chat_id to enrich_movies() too."""
        stub = _make_movie()
        mock_discover = AsyncMock(return_value=[stub])
        mock_enrich  = AsyncMock(return_value=[stub])

        with (
            patch("services.recommendation_service.DiscoveryService.discover", mock_discover),
            patch("services.recommendation_service.EnrichmentService.enrich_movies", mock_enrich),
            patch("services.recommendation_service.error_batcher"),
        ):
            from services.recommendation_service import RecommendationService
            svc = RecommendationService()
            await svc.get_recommendations(
                mode="surprise",
                chat_id="USER_456",
                request_id="req-xyz",
            )

        _, kwargs = mock_enrich.call_args
        assert kwargs.get("chat_id") == "USER_456", (
            f"enrich_movies() received chat_id={kwargs.get('chat_id')!r}, expected 'USER_456'"
        )

    @pytest.mark.asyncio
    async def test_error_row_chat_id_is_not_system_when_real_id_provided(self):
        """When OMDb fails and a real chat_id was passed, 'system' must not appear."""
        stub = _make_movie()
        with (
            patch(
                "services.discovery_service.omdb_client.get_by_title",
                new=AsyncMock(side_effect=RuntimeError("OMDb down")),
            ),
            patch("services.discovery_service.error_batcher") as mock_batcher,
        ):
            from services.discovery_service import _enrich_with_omdb
            await _enrich_with_omdb(stub, chat_id="REAL_USER_789")

        emitted = mock_batcher.emit.call_args[0][0]
        assert emitted["chat_id"] == "REAL_USER_789"
        assert emitted["chat_id"] != "system"

    @pytest.mark.asyncio
    async def test_system_fallback_when_no_chat_id_given(self):
        """If caller omits chat_id it should default to 'system', not crash."""
        stub = _make_movie()
        with (
            patch(
                "services.discovery_service.omdb_client.get_by_title",
                new=AsyncMock(side_effect=RuntimeError("down")),
            ),
            patch("services.discovery_service.error_batcher") as mock_batcher,
        ):
            from services.discovery_service import _enrich_with_omdb
            result = await _enrich_with_omdb(stub)  # no chat_id kwarg

        assert result is stub  # still returns stub
        emitted = mock_batcher.emit.call_args[0][0]
        assert emitted["chat_id"] == "system"  # falls back gracefully
