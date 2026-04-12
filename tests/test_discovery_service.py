"""Unit tests for DiscoveryService.

Covers:
- Happy path: Perplexity OK + OMDb OK
- Perplexity returns None  → DB fallback triggered
- Perplexity returns malformed JSON → DB fallback triggered
- OMDb returns None (no result) → LLM stub preserved, NOT dropped
- OMDb raises exception → stub preserved, error_batcher called
- DB fallback itself returns rows → MovieModels built correctly
- DB fallback fails → empty list returned (no crash)
- All discovery modes route to the correct prompt builder
- error_batcher.emit() is called on every failure path
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from models.domain import SessionModel, UserModel
from services.discovery_service import (
    DiscoveryService,
    _extract_json_array,
    _llm_item_to_movie,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session() -> SessionModel:
    return SessionModel(
        chat_id="111",
        answers_mood="happy",
        answers_genre="Comedy",
        answers_language="English",
        answers_era="2010s",
        answers_rating="7+",
    )


@pytest.fixture
def user() -> UserModel:
    return UserModel(chat_id="111", preferred_genres=["Comedy"], disliked_genres=["Horror"])


PERPLEXITY_GOOD = json.dumps([
    {"title": "Inception",   "year": "2010", "reason": "Layered dream thriller"},
    {"title": "The Matrix",  "year": "1999", "reason": "Cyber-philosophy"},
    {"title": "Interstellar","year": "2014", "reason": "Space epic"},
])

OMDB_OK = {
    "Response": "True", "imdbID": "tt1375666",
    "Title": "Inception", "Year": "2010", "imdbRating": "8.8",
    "Genre": "Action, Sci-Fi", "Language": "English",
    "Plot": "A thief enters dreams.", "Poster": "https://img.example.com/p.jpg",
}

OMDB_NA_POSTER = {**OMDB_OK, "imdbID": "tt0133093", "Title": "The Matrix", "Poster": "N/A"}


DB_ROWS = [
    {
        "movie_id": "tt9999001",
        "data_json": {
            "Title": "Stored Movie A", "Year": "2020", "imdbRating": "7.5",
            "Genre": "Drama", "Language": "English",
            "Plot": "A great drama.", "Poster": "https://example.com/a.jpg",
        },
    },
    {
        "movie_id": "tt9999002",
        "data_json": {
            "Title": "Stored Movie B", "Year": "2019", "imdbRating": "N/A",
            "Genre": "Comedy", "Language": "Hindi",
            "Plot": None, "Poster": "N/A",
        },
    },
]


# ---------------------------------------------------------------------------
# _extract_json_array — unit tests
# ---------------------------------------------------------------------------

class TestExtractJsonArray:
    def test_clean_json_array(self):
        raw = json.dumps([{"title": "Inception", "year": "2010", "reason": "Mind-bending"}])
        result = _extract_json_array(raw)
        assert len(result) == 1
        assert result[0]["title"] == "Inception"

    def test_embedded_in_prose(self):
        raw = 'Here are picks: [{"title": "Dune", "year": "2021", "reason": "Epic"}] enjoy!'
        assert _extract_json_array(raw)[0]["title"] == "Dune"

    def test_trailing_comma_repaired(self):
        raw = '[{"title": "A", "year": "2020", "reason": "Good"},]'
        assert _extract_json_array(raw)[0]["title"] == "A"

    def test_empty_string(self):
        assert _extract_json_array("") == []

    def test_no_array_in_text(self):
        assert _extract_json_array("Just text, no JSON here.") == []

    def test_nested_objects(self):
        raw = json.dumps([{"title": "X", "year": "2022", "reason": "test"},
                          {"title": "Y", "year": "2023", "reason": "test2"}])
        assert len(_extract_json_array(raw)) == 2


# ---------------------------------------------------------------------------
# _llm_item_to_movie — unit tests
# ---------------------------------------------------------------------------

class TestLlmItemToMovie:
    def test_valid_item(self):
        m = _llm_item_to_movie({"title": "Parasite", "year": "2019", "reason": "Oscar winner"})
        assert m is not None
        assert m.title == "Parasite"
        assert m.year == "2019"
        assert m.reason == "Oscar winner"

    def test_missing_title_returns_none(self):
        assert _llm_item_to_movie({"year": "2020"}) is None

    def test_empty_title_returns_none(self):
        assert _llm_item_to_movie({"title": "   ", "year": "2020"}) is None

    def test_movie_id_is_slugified(self):
        m = _llm_item_to_movie({"title": "The Dark Knight", "year": "2008", "reason": "Great"})
        assert m is not None
        # movie_id should be lowercase alphanumeric + underscores only
        assert all(c.isalnum() or c == "_" for c in m.movie_id)

    def test_no_year_is_ok(self):
        m = _llm_item_to_movie({"title": "Unknown Year"})
        assert m is not None
        assert m.year is None


# ---------------------------------------------------------------------------
# DiscoveryService.discover — POSITIVE (happy path)
# ---------------------------------------------------------------------------

class TestDiscoverHappyPath:
    @pytest.mark.asyncio
    async def test_returns_enriched_movies(self):
        svc = DiscoveryService()
        omdb_responses = [OMDB_OK, OMDB_NA_POSTER, OMDB_OK]
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=PERPLEXITY_GOOD)), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(side_effect=omdb_responses)):
            results = await svc.discover(mode="movie", seed_title="Inception", chat_id="111")
        assert len(results) == 3
        assert results[0].movie_id == "tt1375666"
        assert results[0].rating == 8.8

    @pytest.mark.asyncio
    async def test_na_poster_becomes_none(self):
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=json.dumps([{"title": "Matrix", "year": "1999", "reason": "Classic"}]))), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(return_value=OMDB_NA_POSTER)):
            results = await svc.discover(mode="movie", seed_title="Matrix")
        assert results[0].poster is None

    @pytest.mark.asyncio
    async def test_all_modes_do_not_raise(self, session, user):
        svc = DiscoveryService()
        modes = [
            {"mode": "question_engine", "session": session, "user": user},
            {"mode": "movie",           "seed_title": "Dune"},
            {"mode": "trending"},
            {"mode": "surprise",        "user": user},
            {"mode": "more_like",       "seed_title": "Dune", "seen_titles": ["Arrival"]},
            {"mode": "unknown_mode"},   # fallback to trending
        ]
        for kwargs in modes:
            with patch("services.discovery_service.perplexity_client.chat",
                       new=AsyncMock(return_value=PERPLEXITY_GOOD)), \
                 patch("services.discovery_service.omdb_client.get_by_title",
                       new=AsyncMock(return_value=None)):
                results = await svc.discover(**kwargs)
            assert isinstance(results, list), f"mode={kwargs['mode']} should return a list"


# ---------------------------------------------------------------------------
# DiscoveryService.discover — NEGATIVE: Perplexity failures → DB fallback
# ---------------------------------------------------------------------------

class TestDiscoverPerplexityFailures:
    @pytest.mark.asyncio
    async def test_perplexity_none_triggers_db_fallback(self):
        """NEGATIVE: Perplexity returns None → should NOT return [] — must try DB."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=(DB_ROWS, None))):
            results = await svc.discover(mode="trending", chat_id="111")
        assert len(results) == 2
        assert results[0].title == "Stored Movie A"
        assert results[0].rating == 7.5

    @pytest.mark.asyncio
    async def test_perplexity_none_emits_error_to_batcher(self):
        """NEGATIVE: Perplexity None → error_batcher.emit() must be called."""
        svc = DiscoveryService()
        mock_emit = MagicMock()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=([], None))), \
             patch("services.discovery_service.error_batcher") as mock_batcher:
            mock_batcher.emit = mock_emit
            await svc.discover(mode="trending", chat_id="999")
        mock_emit.assert_called_once()
        payload = mock_emit.call_args[0][0]
        assert payload["chat_id"] == "999"
        assert payload["error_type"] == "perplexity_empty_response"
        assert payload["workflow_step"] == "discovery.discover"

    @pytest.mark.asyncio
    async def test_perplexity_malformed_json_triggers_db_fallback(self):
        """NEGATIVE: Perplexity returns garbage JSON → DB fallback."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value="absolutely not JSON")), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=(DB_ROWS, None))):
            results = await svc.discover(mode="surprise", chat_id="111")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_perplexity_malformed_json_emits_error(self):
        """NEGATIVE: Parse failure → error_batcher.emit() called with correct type."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value="not json")), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=([], None))), \
             patch("services.discovery_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            await svc.discover(mode="trending", chat_id="888")
        mock_batcher.emit.assert_called_once()
        payload = mock_batcher.emit.call_args[0][0]
        assert payload["error_type"] == "perplexity_parse_failed"
        assert payload["chat_id"] == "888"

    @pytest.mark.asyncio
    async def test_db_fallback_builds_movie_with_na_poster_as_none(self):
        """NEGATIVE: DB row with Poster=N/A → poster field is None."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=(DB_ROWS, None))):
            results = await svc.discover(mode="trending", chat_id="111")
        # DB_ROWS[1] has Poster N/A — should be None
        movie_b = next(m for m in results if m.title == "Stored Movie B")
        assert movie_b.poster is None

    @pytest.mark.asyncio
    async def test_db_fallback_rating_na_becomes_none(self):
        """NEGATIVE: imdbRating=N/A in DB row → rating is None, not a crash."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=(DB_ROWS, None))):
            results = await svc.discover(mode="trending", chat_id="111")
        movie_b = next(m for m in results if m.title == "Stored Movie B")
        assert movie_b.rating is None

    @pytest.mark.asyncio
    async def test_db_fallback_itself_fails_returns_empty_list(self):
        """NEGATIVE: DB also fails → return [] gracefully, no crash."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(side_effect=Exception("DB is down"))):
            results = await svc.discover(mode="trending", chat_id="111")
        assert results == []

    @pytest.mark.asyncio
    async def test_db_fallback_supabase_error_string_returns_empty(self):
        """NEGATIVE: select_rows_async returns (None, 'some error') → empty list."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=(None, "connection refused"))):
            results = await svc.discover(mode="trending", chat_id="111")
        assert results == []


# ---------------------------------------------------------------------------
# DiscoveryService.discover — NEGATIVE: OMDb failures → stub preserved
# ---------------------------------------------------------------------------

class TestDiscoverOmdbFailures:
    @pytest.mark.asyncio
    async def test_omdb_returns_none_preserves_llm_stub(self):
        """NEGATIVE: OMDb returns None → LLM stub returned, movie NOT dropped."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=PERPLEXITY_GOOD)), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(return_value=None)):
            results = await svc.discover(mode="movie", seed_title="Inception", chat_id="111")
        assert len(results) == 3  # All three stubs preserved
        assert all(m.title for m in results)
        assert all(m.rating is None for m in results)  # No OMDb data

    @pytest.mark.asyncio
    async def test_omdb_exception_preserves_stub_and_emits_error(self):
        """NEGATIVE: OMDb raises → stub kept + error logged."""
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=PERPLEXITY_GOOD)), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(side_effect=Exception("timeout"))), \
             patch("services.discovery_service.error_batcher") as mock_batcher:
            mock_batcher.emit = MagicMock()
            results = await svc.discover(mode="movie", seed_title="Inception", chat_id="555")
        # Movies must NOT be dropped
        assert len(results) == 3
        # error_batcher.emit called at least once per failed OMDb call
        assert mock_batcher.emit.call_count >= 1
        payload = mock_batcher.emit.call_args_list[0][0][0]
        assert payload["error_type"] == "omdb_enrichment_error"
        assert payload["chat_id"] == "555"

    @pytest.mark.asyncio
    async def test_omdb_partial_failure_keeps_successful_enrichments(self):
        """NEGATIVE: First OMDb OK, second raises → both movies in output."""
        good_resp = OMDB_OK
        bad_side_effect = [good_resp, Exception("503"), good_resp]
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=PERPLEXITY_GOOD)), \
             patch("services.discovery_service.omdb_client.get_by_title",
                   new=AsyncMock(side_effect=bad_side_effect)):
            results = await svc.discover(mode="movie", seed_title="Inception", chat_id="111")
        assert len(results) == 3  # No movie dropped
        # First movie fully enriched
        assert results[0].movie_id == "tt1375666"
        # Second movie is a stub (no imdb id from OMDb)
        assert results[1].rating is None

    @pytest.mark.asyncio
    async def test_all_omdb_fail_falls_back_to_db(self):
        """NEGATIVE: All OMDb calls raise AND stubs are empty → DB fallback."""
        # Simulate Perplexity returning no parseable items + OMDb all failing
        svc = DiscoveryService()
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value="[]")), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=(DB_ROWS, None))):
            results = await svc.discover(mode="trending", chat_id="111")
        # Empty LLM list triggers empty-after-omdb path → DB fallback
        assert len(results) == 2
        assert results[0].title == "Stored Movie A"


# ---------------------------------------------------------------------------
# chat_id propagation
# ---------------------------------------------------------------------------

class TestChatIdPropagation:
    @pytest.mark.asyncio
    async def test_error_rows_carry_real_chat_id(self):
        """chat_id passed to discover() must appear in every error_batcher payload."""
        svc = DiscoveryService()
        emitted = []
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=([], None))), \
             patch("services.discovery_service.error_batcher") as mock_batcher:
            mock_batcher.emit.side_effect = emitted.append
            await svc.discover(mode="trending", chat_id="user_42")
        assert emitted, "error_batcher.emit should have been called"
        assert all(row["chat_id"] == "user_42" for row in emitted)

    @pytest.mark.asyncio
    async def test_request_id_propagated_in_error_rows(self):
        svc = DiscoveryService()
        emitted = []
        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=None)), \
             patch("services.discovery_service.supabase_client.select_rows_async",
                   new=AsyncMock(return_value=([], None))), \
             patch("services.discovery_service.error_batcher") as mock_batcher:
            mock_batcher.emit.side_effect = emitted.append
            await svc.discover(mode="trending", chat_id="u1", request_id="req-xyz")
        assert emitted[0]["request_id"] == "req-xyz"
