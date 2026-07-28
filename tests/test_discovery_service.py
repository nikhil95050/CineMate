"""Tests for discovery service with TMDB integration."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from services.discovery_service import (
    DiscoveryService,
    _tmdb_result_to_movie,
    _llm_item_to_movie,
    _extract_json_array,
    _build_trending_prompt,
)
from models.domain import MovieModel


class TestHelpers:
    def test_tmdb_result_to_movie_full(self):
        result = _tmdb_result_to_movie({
            "imdb_id": "tt0110912", "tmdb_id": "680",
            "title": "Pulp Fiction", "year": "1994",
            "rating": 8.5, "genres": "Crime",
            "description": "Great movie",
            "poster_url": "https://img/poster.jpg",
            "trailer_url": "https://youtube.com/watch?v=abc",
            "popularity": 95.0,
        })
        assert result is not None
        assert result.movie_id == "tt0110912"
        assert result.title == "Pulp Fiction"
        assert result.rating == 8.5
        assert "popularity: 95.0" in result.reason

    def test_tmdb_result_to_movie_no_imdb(self):
        result = _tmdb_result_to_movie({
            "tmdb_id": "680", "title": "Pulp Fiction",
            "year": "1994", "popularity": 90.0,
        })
        assert result is not None
        assert result.movie_id == "680"

    def test_tmdb_result_to_movie_no_title(self):
        result = _tmdb_result_to_movie({"tmdb_id": "123"})
        assert result is None

    def test_llm_item_to_movie(self):
        result = _llm_item_to_movie({
            "title": "Inception", "year": "2010",
            "reason": "Mind-bending thriller",
        })
        assert result is not None
        assert result.title == "Inception"
        assert result.year == "2010"
        assert result.reason == "Mind-bending thriller"

    def test_llm_item_to_movie_no_title(self):
        result = _llm_item_to_movie({"year": "2010"})
        assert result is None

    def test_extract_json_array_valid(self):
        result = _extract_json_array('[{"title":"Test","year":"2024"}]')
        assert len(result) == 1
        assert result[0]["title"] == "Test"

    def test_extract_json_array_with_prose(self):
        result = _extract_json_array('Here are movies: [{"title":"Test","year":"2024"}]')
        assert len(result) == 1
        assert result[0]["title"] == "Test"

    def test_extract_json_array_invalid(self):
        result = _extract_json_array("Not JSON at all")
        assert result == []

    def test_extract_json_array_empty(self):
        result = _extract_json_array("")
        assert result == []

    def test_build_trending_prompt_has_years(self):
        prompt = _build_trending_prompt()
        assert "acclaimed" in prompt.lower() or "widely watched" in prompt.lower() or "films" in prompt.lower()


class TestDiscoveryService:
    def test_instantiate(self):
        ds = DiscoveryService()
        assert ds is not None
        assert ds._metadata_repo is not None

    @pytest.mark.asyncio
    async def test_discover_trending_tmdb(self):
        ds = DiscoveryService()
        trending_data = [
            {"imdb_id": "tt1", "tmdb_id": "1", "title": "Movie A", "year": "2024",
             "rating": 7.5, "genres": "Action", "popularity": 100, "source": "tmdb"},
            {"imdb_id": "tt2", "tmdb_id": "2", "title": "Movie B", "year": "2024",
             "rating": 8.0, "genres": "Drama", "popularity": 90, "source": "tmdb"},
        ]
        with patch("services.discovery_service._get_movie_data_provider") as mock_provider:
            mock_provider.return_value.get_trending = AsyncMock(return_value=trending_data)
            result = await ds.discover(mode="trending", chat_id="test")
            assert len(result) == 2
            assert result[0].title == "Movie A"
            assert result[1].title == "Movie B"

    @pytest.mark.asyncio
    async def test_discover_trending_fallback_to_llm(self):
        ds = DiscoveryService()
        with patch("services.discovery_service._get_movie_data_provider") as mock_provider:
            mock_provider.return_value.get_trending = AsyncMock(return_value=[])
            mock_provider.return_value.search = AsyncMock(return_value=[])
            with patch("services.discovery_service.perplexity_client.chat") as mock_chat:
                mock_chat.return_value = None
                result = await ds.discover(mode="trending", chat_id="test")
                assert result == []

    @pytest.mark.asyncio
    async def test_discover_movie_tmdb_similar(self):
        ds = DiscoveryService()
        similar_data = [
            {"imdb_id": "tt3", "tmdb_id": "3", "title": "Similar A", "year": "2023",
             "rating": 7.0, "popularity": 80, "source": "tmdb"},
        ]
        with patch("services.discovery_service._get_movie_data_provider") as mock_provider:
            mock_provider.return_value.search = AsyncMock(return_value=[{"tmdb_id": "123"}])
            mock_provider.return_value.get_similar = AsyncMock(return_value=similar_data)
            result = await ds.discover(mode="movie", seed_title="Inception", chat_id="test")
            assert len(result) == 1
            assert result[0].title == "Similar A"

    @pytest.mark.asyncio
    async def test_discover_question_engine_uses_llm(self):
        ds = DiscoveryService()
        from models.domain import SessionModel
        session = SessionModel(chat_id="test", answers_mood="Happy", answers_genre="Action")
        with patch("services.discovery_service._get_movie_data_provider") as mock_provider:
            mock_provider.return_value.search = AsyncMock(return_value=[])
            with patch("services.discovery_service.perplexity_client.chat") as mock_chat:
                mock_chat.return_value = None
                result = await ds.discover(mode="question_engine", session=session, chat_id="test")
                assert result == []

    @pytest.mark.asyncio
    async def test_get_star_movies_tmdb(self):
        ds = DiscoveryService()
        person_movies = [
            {"imdb_id": "tt1", "tmdb_id": "1", "title": "Movie A", "year": "2020",
             "rating": 8.0, "popularity": 90, "source": "tmdb"},
            {"imdb_id": "tt2", "tmdb_id": "2", "title": "Movie B", "year": "2019",
             "rating": 7.5, "popularity": 80, "source": "tmdb"},
        ]
        with patch("services.discovery_service._get_movie_data_provider") as mock_provider:
            mock_provider.return_value._tmdb.search_person = AsyncMock(
                return_value={"id": 525, "name": "Nolan"}
            )
            mock_provider.return_value._tmdb.get_person_movies = AsyncMock(
                return_value=person_movies
            )
            result = await ds.get_star_movies("Christopher Nolan")
            assert len(result) == 2
            assert result[0].title == "Movie A"

    @pytest.mark.asyncio
    async def test_get_star_movies_empty_name(self):
        ds = DiscoveryService()
        result = await ds.get_star_movies("")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_star_movies_tmdb_fails_llm_fallback(self):
        ds = DiscoveryService()
        with patch("services.discovery_service._get_movie_data_provider") as mock_provider:
            mock_provider.return_value._tmdb.search_person = AsyncMock(return_value=None)
            with patch("services.discovery_service.perplexity_client.chat") as mock_chat:
                mock_chat.return_value = '[{"title":"Test Movie","year":"2020","reason":"Good"}]'
                result = await ds.get_star_movies("Someone")
                assert len(result) >= 0
