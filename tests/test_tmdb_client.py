"""Tests for TMDB client."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from clients.tmdb_client import (
    TmdbClient, poster_url, backdrop_url, _normalize_search_result,
)


class TestTmdbHelpers:
    def test_poster_url_none(self):
        assert poster_url(None) is None

    def test_poster_url_path(self):
        url = poster_url("/xyz123.jpg")
        assert "/w500" in url
        assert "/xyz123.jpg" in url

    def test_backdrop_url_none(self):
        assert backdrop_url(None) is None

    def test_backdrop_url_path(self):
        url = backdrop_url("/bg123.jpg")
        assert "/w1280" in url
        assert "/bg123.jpg" in url

    def test_normalize_search_result(self):
        result = _normalize_search_result({
            "id": 123,
            "title": "Test Movie",
            "release_date": "2024-06-15",
            "vote_average": 8.5,
            "vote_count": 1000,
            "overview": "A great movie",
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "popularity": 95.0,
        })
        assert result["tmdb_id"] == "123"
        assert result["title"] == "Test Movie"
        assert result["year"] == "2024"
        assert result["rating"] == 8.5
        assert result["vote_count"] == 1000
        assert result["source"] == "tmdb"
        assert result["popularity"] == 95.0

    def test_normalize_search_result_minimal(self):
        result = _normalize_search_result({
            "id": 456,
            "original_title": "No Title",
        })
        assert result["tmdb_id"] == "456"
        assert result["year"] is None
        assert result["rating"] is None
        assert result["source"] == "tmdb"


class TestTmdbClientInit:
    def test_provider_name(self):
        client = TmdbClient()
        assert client.provider_name == "tmdb"
        assert client.daily_budget == 1000

    def test_api_key_from_env(self):
        client = TmdbClient()
        assert client._api_key() == ""

    def test_headers(self):
        client = TmdbClient()
        with patch.object(client, "_api_key", return_value="test-key"):
            headers = client._headers()
            assert headers["Authorization"] == "Bearer test-key"
            assert headers["accept"] == "application/json"


class TestTmdbClientNormalizeMovie:
    def test_full_movie_normalization(self):
        client = TmdbClient()
        data = {
            "id": 680,
            "imdb_id": "tt0110912",
            "title": "Pulp Fiction",
            "original_title": "Pulp Fiction",
            "release_date": "1994-10-14",
            "vote_average": 8.5,
            "vote_count": 25000,
            "genres": [{"name": "Thriller"}, {"name": "Crime"}],
            "original_language": "en",
            "overview": "Amazing movie",
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "runtime": 154,
            "popularity": 89.0,
        }
        result = client._normalize_movie(data)
        assert result["imdb_id"] == "tt0110912"
        assert result["tmdb_id"] == "680"
        assert result["title"] == "Pulp Fiction"
        assert result["year"] == "1994"
        assert result["rating"] == 8.5
        assert result["vote_count"] == 25000
        assert result["genres"] == "Thriller, Crime"
        assert result["language"] == "EN"
        assert result["description"] == "Amazing movie"
        assert result["runtime"] == 154
        assert result["popularity"] == 89.0
        assert result["source"] == "tmdb"
        assert result["poster_url"] is not None
        assert result["backdrop_url"] is not None

    def test_minimal_movie_normalization(self):
        client = TmdbClient()
        data = {"id": 1, "title": "Test"}
        result = client._normalize_movie(data)
        assert result["title"] == "Test"
        assert result["tmdb_id"] == "1"
        assert result["imdb_id"] == ""
        assert result["genres"] is None
        assert result["cast"] == []
        assert result["director"] is None


class TestTmdbClientAsync:
    @pytest.mark.asyncio
    async def test_get_by_title_no_api_key(self):
        client = TmdbClient()
        result = await client.get_by_title("Inception")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_title_empty_title(self):
        client = TmdbClient()
        with patch.object(client, "_api_key", return_value="fake-key"):
            result = await client.get_by_title("")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id_no_api_key(self):
        client = TmdbClient()
        result = await client.get_by_id("tt1234567")
        assert result is None

    @pytest.mark.asyncio
    async def test_search_no_api_key(self):
        client = TmdbClient()
        result = await client.search("Inception")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_trending_no_api_key(self):
        client = TmdbClient()
        result = await client.get_trending()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_similar_no_api_key(self):
        client = TmdbClient()
        result = await client.get_similar("123")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_credits_no_api_key(self):
        client = TmdbClient()
        result = await client.get_credits("123")
        assert result == {"cast": [], "director": None, "tmdb_id": "123"}

    @pytest.mark.asyncio
    async def test_get_trailers_no_api_key(self):
        client = TmdbClient()
        result = await client.get_trailers("123")
        assert result is None

    @pytest.mark.asyncio
    async def test_search_person_no_api_key(self):
        client = TmdbClient()
        result = await client.search_person("Nolan")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_person_movies_no_api_key(self):
        client = TmdbClient()
        result = await client.get_person_movies(1)
        assert result == []

    @pytest.mark.asyncio
    async def test_get_by_title_circuit_open(self):
        client = TmdbClient()
        mock_hs = MagicMock()
        mock_hs.is_healthy = MagicMock(return_value=False)
        with patch.object(client, "_api_key", return_value="fake-key"):
            with patch.object(client, "_health", return_value=mock_hs):
                result = await client.get_by_title("Inception")
                assert result is None

    @pytest.mark.asyncio
    async def test_get_by_title_success(self):
        client = TmdbClient()
        mock_hs = MagicMock()
        mock_hs.is_healthy = MagicMock(return_value=True)
        with patch.object(client, "_api_key", return_value="fake-key"):
            with patch.object(client, "_health", return_value=mock_hs):
                with patch.object(client, "_request") as mock_req:
                    mock_req.side_effect = [
                        {"results": [{"id": 680, "title": "Pulp Fiction"}]},
                        {"id": 680, "title": "Pulp Fiction", "imdb_id": "tt0110912",
                         "release_date": "1994-10-14", "vote_average": 8.5,
                         "videos": {"results": []}},
                    ]
                    result = await client.get_by_title("Pulp Fiction", chat_id="test123")
                    assert result is not None
                    assert result["source"] == "tmdb"
                    assert result["title"] == "Pulp Fiction"

    @pytest.mark.asyncio
    async def test_get_by_title_not_found(self):
        client = TmdbClient()
        mock_hs = MagicMock()
        mock_hs.is_healthy = MagicMock(return_value=True)
        with patch.object(client, "_api_key", return_value="fake-key"):
            with patch.object(client, "_health", return_value=mock_hs):
                with patch.object(client, "_request", return_value={"results": []}):
                    result = await client.get_by_title("NonExistentMovie12345")
                    assert result is None

    @pytest.mark.asyncio
    async def test_get_by_title_network_error(self):
        client = TmdbClient()
        mock_hs = MagicMock()
        mock_hs.is_healthy = MagicMock(return_value=True)
        with patch.object(client, "_api_key", return_value="fake-key"):
            with patch.object(client, "_health", return_value=mock_hs):
                with patch.object(client, "_request", return_value=None):
                    result = await client.get_by_title("Inception")
                    assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id_with_trailer(self):
        client = TmdbClient()
        mock_hs = MagicMock()
        mock_hs.is_healthy = MagicMock(return_value=True)
        with patch.object(client, "_api_key", return_value="fake-key"):
            with patch.object(client, "_health", return_value=mock_hs):
                with patch.object(client, "_request") as mock_req:
                    mock_req.return_value = {
                        "id": 680, "title": "Pulp Fiction",
                        "imdb_id": "tt0110912",
                        "release_date": "1994-10-14",
                        "vote_average": 8.5,
                        "videos": {"results": [
                            {"type": "Trailer", "site": "YouTube", "key": "abc123"}
                        ]},
                        "credits": {"cast": [], "crew": []},
                    }
                    result = await client.get_by_id("tt0110912")
                    assert result is not None
                    assert "youtube.com/watch?v=abc123" in result["trailer_url"]

    @pytest.mark.asyncio
    async def test_get_by_id_404(self):
        client = TmdbClient()
        mock_hs = MagicMock()
        mock_hs.is_healthy = MagicMock(return_value=True)
        import httpx
        with patch.object(client, "_api_key", return_value="fake-key"):
            with patch.object(client, "_health", return_value=mock_hs):
                with patch.object(client, "_request", return_value=None):
                    result = await client.get_by_id("99999999")
                    assert result is None
