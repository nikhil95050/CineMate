"""Tests for MovieDataProvider -- TMDB first, OMDB fallback."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from clients.movie_data_provider import MovieDataProvider, _normalize_omdb_response


class TestNormalizeOmdbResponse:
    def test_full_response(self):
        raw = {
            "imdbID": "tt0110912",
            "Title": "Pulp Fiction",
            "Year": "1994",
            "imdbRating": "8.9",
            "Genre": "Crime, Drama",
            "Language": "English",
            "Plot": "Lives of two mob hitmen...",
            "Poster": "https://example.com/poster.jpg",
        }
        result = _normalize_omdb_response(raw)
        assert result["source"] == "omdb"
        assert result["imdb_id"] == "tt0110912"
        assert result["title"] == "Pulp Fiction"
        assert result["year"] == "1994"
        assert result["rating"] == 8.9
        assert result["genres"] == "Crime, Drama"

    def test_with_na_values(self):
        raw = {
            "imdbID": "tt123",
            "Title": "Test",
            "Year": "N/A",
            "imdbRating": "N/A",
            "Genre": "N/A",
            "Language": "N/A",
            "Plot": "N/A",
            "Poster": "N/A",
        }
        result = _normalize_omdb_response(raw)
        assert result["source"] == "omdb"
        assert result["imdb_id"] == "tt123"
        assert result["title"] == "Test"
        assert "year" not in result
        assert "rating" not in result

    def test_empty_response(self):
        raw = {"imdbID": "tt123", "Title": "Movie"}
        result = _normalize_omdb_response(raw)
        assert result["source"] == "omdb"


def _make_provider():
    provider = MovieDataProvider()
    provider._tmdb_available = True
    return provider


class TestMovieDataProvider:
    @pytest.mark.asyncio
    async def test_get_by_title_tmdb_primary_success(self):
        provider = _make_provider()
        tmdb_data = {
            "imdb_id": "tt0110912", "tmdb_id": "680",
            "title": "Pulp Fiction", "year": "1994",
            "rating": 8.5, "genres": "Crime",
            "source": "tmdb",
        }
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_by_title", AsyncMock(return_value=tmdb_data)):
                result = await provider.get_by_title("Pulp Fiction")
                assert result is not None
                assert result["source"] == "tmdb"
                assert result["title"] == "Pulp Fiction"

    @pytest.mark.asyncio
    async def test_get_by_title_tmdb_fails_omdb_fallback(self):
        provider = _make_provider()
        omdb_raw = {
            "Response": "True", "imdbID": "tt0110912",
            "Title": "Pulp Fiction", "Year": "1994",
            "imdbRating": "8.9", "Genre": "Crime",
        }
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_by_title", AsyncMock(return_value=None)):
                with patch("clients.omdb_client.get_by_title", AsyncMock(return_value=omdb_raw)):
                    result = await provider.get_by_title("Pulp Fiction")
                    assert result is not None
                    assert result["source"] == "omdb"
                    assert result["title"] == "Pulp Fiction"

    @pytest.mark.asyncio
    async def test_get_by_title_both_fail(self):
        provider = _make_provider()
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_by_title", AsyncMock(return_value=None)):
                with patch("clients.omdb_client.get_by_title", AsyncMock(return_value=None)):
                    result = await provider.get_by_title("Nothing")
                    assert result is None

    @pytest.mark.asyncio
    async def test_get_by_title_no_tmdb_key_direct_omdb(self):
        provider = MovieDataProvider()
        provider._tmdb_available = False
        omdb_raw = {
            "Response": "True", "imdbID": "tt123",
            "Title": "Test Movie", "Year": "2024",
            "imdbRating": "7.0", "Genre": "Action",
        }
        with patch("clients.omdb_client.get_by_title", AsyncMock(return_value=omdb_raw)):
            result = await provider.get_by_title("Test Movie")
            assert result is not None
            assert result["source"] == "omdb"

    @pytest.mark.asyncio
    async def test_get_by_title_tmdb_exception_omdb_fallback(self):
        provider = _make_provider()
        omdb_raw = {"Response": "True", "imdbID": "tt123", "Title": "Fallback", "Year": "2024"}
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_by_title", AsyncMock(side_effect=RuntimeError("crash"))):
                with patch("clients.omdb_client.get_by_title", AsyncMock(return_value=omdb_raw)):
                    result = await provider.get_by_title("Movie")
                    assert result is not None
                    assert result["title"] == "Fallback"

    @pytest.mark.asyncio
    async def test_get_by_id_imdb_tmdb_success(self):
        provider = _make_provider()
        tmdb_data = {"source": "tmdb", "title": "Matrix", "tmdb_id": "603"}
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_by_id", AsyncMock(return_value=tmdb_data)):
                result = await provider.get_by_id("tt0133093")
                assert result is not None
                assert result["source"] == "tmdb"

    @pytest.mark.asyncio
    async def test_get_by_id_tmdb_numeric_id(self):
        provider = _make_provider()
        tmdb_data = {"source": "tmdb", "title": "Matrix", "tmdb_id": "603"}
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_by_id", AsyncMock(return_value=tmdb_data)):
                result = await provider.get_by_id("603")
                assert result is not None
                assert result["source"] == "tmdb"

    @pytest.mark.asyncio
    async def test_get_trending(self):
        provider = _make_provider()
        trending = [
            {"tmdb_id": "1", "title": "Movie A", "source": "tmdb", "popularity": 100},
            {"tmdb_id": "2", "title": "Movie B", "source": "tmdb", "popularity": 90},
        ]
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_trending", AsyncMock(return_value=trending)):
                result = await provider.get_trending()
                assert len(result) == 2
                assert result[0]["title"] == "Movie A"

    @pytest.mark.asyncio
    async def test_get_trending_no_tmdb(self):
        provider = MovieDataProvider()
        provider._tmdb_available = False
        result = await provider.get_trending()
        assert result == []

    @pytest.mark.asyncio
    async def test_get_similar(self):
        provider = _make_provider()
        similar = [{"tmdb_id": "2", "title": "Similar Movie", "source": "tmdb"}]
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_similar", AsyncMock(return_value=similar)):
                result = await provider.get_similar("680")
                assert len(result) == 1
                assert result[0]["title"] == "Similar Movie"

    @pytest.mark.asyncio
    async def test_get_credits(self):
        provider = _make_provider()
        credits = {"cast": ["Actor A"], "director": "Director X", "tmdb_id": "123"}
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_credits", AsyncMock(return_value=credits)):
                result = await provider.get_credits("123")
                assert result["cast"] == ["Actor A"]
                assert result["director"] == "Director X"

    @pytest.mark.asyncio
    async def test_get_trailers(self):
        provider = _make_provider()
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "get_trailers", AsyncMock(return_value="https://youtube.com/watch?v=abc")):
                result = await provider.get_trailers("123")
                assert "youtube.com" in result

    @pytest.mark.asyncio
    async def test_search(self):
        provider = _make_provider()
        results = [{"tmdb_id": "1", "title": "Searched Movie", "source": "tmdb"}]
        with patch.object(provider._tmdb, "_api_key", return_value="fake-key"):
            with patch.object(provider._tmdb, "search", AsyncMock(return_value=results)):
                result = await provider.search("Searched Movie")
                assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_title_returns_none(self):
        provider = MovieDataProvider()
        result = await provider.get_by_title("")
        assert result is None
