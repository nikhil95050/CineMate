"""Unit tests for repositories.movie_metadata_repository.MovieMetadataRepository.

Isolation strategy
------------------
MovieMetadataRepository calls supabase_client.insert_rows_async and
supabase_client.select_rows_async directly -- it has no in-memory _store.
We patch those two coroutines on the `config.supabase_client` module so
every test runs without a real Supabase connection.

Return-value convention
-----------------------
* insert_rows_async  -> (None, None)          on success
* select_rows_async  -> (rows_list, None)      on success
* Either             -> (None, "some error")   on failure

search() returns a list of dicts shaped as {"data_json": {...}, ...}.
Filtering keys inside data_json use OMDb-style capitalised names
("Genre", "Language") -- matching what the repository code reads.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from repositories.movie_metadata_repository import MovieMetadataRepository


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ACTION_ROW = {"movie_id": "tt0095016", "data_json": {"title": "Die Hard", "Genre": "Action", "Language": "English", "year": "1988"}}
DRAMA_ROW  = {"movie_id": "tt0068646", "data_json": {"title": "The Godfather", "Genre": "Drama", "Language": "English", "year": "1972"}}
FRENCH_ROW = {"movie_id": "tt0211915", "data_json": {"title": "Amelie", "Genre": "Romance", "Language": "French", "year": "2001"}}


def _insert_ok():
    """Patch insert_rows_async to return success."""
    return patch(
        "repositories.movie_metadata_repository.supabase_client.insert_rows_async",
        new=AsyncMock(return_value=(None, None)),
    )


def _insert_err(msg="db error"):
    """Patch insert_rows_async to return an error string."""
    return patch(
        "repositories.movie_metadata_repository.supabase_client.insert_rows_async",
        new=AsyncMock(return_value=(None, msg)),
    )


def _select_ok(rows):
    """Patch select_rows_async to return a list of rows."""
    return patch(
        "repositories.movie_metadata_repository.supabase_client.select_rows_async",
        new=AsyncMock(return_value=(rows, None)),
    )


def _select_err(msg="db error"):
    """Patch select_rows_async to return an error."""
    return patch(
        "repositories.movie_metadata_repository.supabase_client.select_rows_async",
        new=AsyncMock(return_value=(None, msg)),
    )


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------

class TestUpsert:
    @pytest.mark.asyncio
    async def test_upsert_returns_true_on_success(self):
        """POSITIVE: upsert returns True when insert_rows_async returns no error."""
        repo = MovieMetadataRepository()
        with _insert_ok():
            result = await repo.upsert("tt0095016", {"title": "Die Hard"})
        assert result is True

    @pytest.mark.asyncio
    async def test_upsert_calls_insert_with_correct_payload(self):
        """POSITIVE: insert_rows_async is called with movie_id and data_json in the row."""
        repo = MovieMetadataRepository()
        mock_insert = AsyncMock(return_value=(None, None))
        with patch(
            "repositories.movie_metadata_repository.supabase_client.insert_rows_async",
            new=mock_insert,
        ):
            await repo.upsert("tt0095016", {"title": "Die Hard"})

        args, kwargs = mock_insert.call_args
        rows = args[1]  # second positional arg is the rows list
        assert rows[0]["movie_id"] == "tt0095016"
        assert rows[0]["data_json"]["title"] == "Die Hard"
        assert kwargs.get("upsert") is True
        assert kwargs.get("on_conflict") == "movie_id"

    @pytest.mark.asyncio
    async def test_upsert_returns_false_when_supabase_returns_error(self):
        """NEGATIVE: upsert returns False when insert_rows_async signals an error."""
        repo = MovieMetadataRepository()
        with _insert_err("unique constraint"):
            result = await repo.upsert("tt0095016", {"title": "Die Hard"})
        assert result is False

    @pytest.mark.asyncio
    async def test_upsert_returns_false_for_empty_movie_id(self):
        """NEGATIVE: Empty movie_id short-circuits and returns False without a DB call."""
        repo = MovieMetadataRepository()
        mock_insert = AsyncMock(return_value=(None, None))
        with patch(
            "repositories.movie_metadata_repository.supabase_client.insert_rows_async",
            new=mock_insert,
        ):
            result = await repo.upsert("", {"title": "X"})
        assert result is False
        mock_insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_returns_false_for_empty_data_json(self):
        """NEGATIVE: Empty data_json short-circuits and returns False without a DB call."""
        repo = MovieMetadataRepository()
        mock_insert = AsyncMock(return_value=(None, None))
        with patch(
            "repositories.movie_metadata_repository.supabase_client.insert_rows_async",
            new=mock_insert,
        ):
            result = await repo.upsert("tt0095016", {})
        assert result is False
        mock_insert.assert_not_called()

    @pytest.mark.asyncio
    async def test_upsert_accepts_nested_json_including_streaming_sources(self):
        """POSITIVE: data_json with nested streaming_sources is passed through unchanged."""
        repo = MovieMetadataRepository()
        data = {"title": "Die Hard", "streaming_sources": [{"name": "Netflix", "type": "sub"}]}
        mock_insert = AsyncMock(return_value=(None, None))
        with patch(
            "repositories.movie_metadata_repository.supabase_client.insert_rows_async",
            new=mock_insert,
        ):
            result = await repo.upsert("tt0095016", data)
        assert result is True
        sent_row = mock_insert.call_args[0][1][0]
        assert sent_row["data_json"]["streaming_sources"][0]["name"] == "Netflix"


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

class TestGet:
    @pytest.mark.asyncio
    async def test_get_returns_data_json_for_known_id(self):
        """POSITIVE: get() extracts and returns the data_json dict from the row."""
        repo = MovieMetadataRepository()
        with _select_ok([ACTION_ROW]):
            result = await repo.get("tt0095016")
        assert result is not None
        assert result["title"] == "Die Hard"

    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_rows(self):
        """NEGATIVE: get() returns None when select returns an empty list."""
        repo = MovieMetadataRepository()
        with _select_ok([]):
            result = await repo.get("tt9999999")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_none_on_supabase_error(self):
        """NEGATIVE: get() returns None when select returns an error string."""
        repo = MovieMetadataRepository()
        with _select_err("connection timeout"):
            result = await repo.get("tt0095016")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_none_for_empty_movie_id(self):
        """NEGATIVE: Empty movie_id short-circuits and returns None without a DB call."""
        repo = MovieMetadataRepository()
        mock_select = AsyncMock(return_value=([], None))
        with patch(
            "repositories.movie_metadata_repository.supabase_client.select_rows_async",
            new=mock_select,
        ):
            result = await repo.get("")
        assert result is None
        mock_select.assert_not_called()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.mark.asyncio
    async def test_search_returns_up_to_limit(self):
        """POSITIVE: search() with limit=2 returns at most 2 records."""
        rows = [ACTION_ROW, DRAMA_ROW, FRENCH_ROW]
        repo = MovieMetadataRepository()
        with _select_ok(rows):
            result = await repo.search(limit=2)
        assert len(result) <= 2

    @pytest.mark.asyncio
    async def test_search_empty_result_returns_empty_list(self):
        """NEGATIVE: search() when select returns [] yields []."""
        repo = MovieMetadataRepository()
        with _select_ok([]):
            result = await repo.search(limit=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_search_error_returns_empty_list(self):
        """NEGATIVE: search() when select returns an error yields [] without raising."""
        repo = MovieMetadataRepository()
        with _select_err():
            result = await repo.search(limit=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_search_no_filters_returns_all_within_limit(self):
        """POSITIVE: search() with no genre/language returns all rows up to limit."""
        repo = MovieMetadataRepository()
        with _select_ok([ACTION_ROW, DRAMA_ROW]):
            result = await repo.search(limit=10)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_search_genre_filter_includes_only_matching(self):
        """POSITIVE: genre='Action' includes only rows whose Genre contains 'action'."""
        repo = MovieMetadataRepository()
        # search() fetches limit*3 rows from Supabase then filters in Python
        with _select_ok([ACTION_ROW, DRAMA_ROW, FRENCH_ROW]):
            result = await repo.search(limit=10, genre="Action")
        assert len(result) == 1
        assert result[0]["data_json"]["Genre"] == "Action"

    @pytest.mark.asyncio
    async def test_search_genre_filter_is_case_insensitive(self):
        """POSITIVE: genre='action' (lowercase) still matches Genre='Action'."""
        repo = MovieMetadataRepository()
        with _select_ok([ACTION_ROW, DRAMA_ROW]):
            result = await repo.search(limit=10, genre="action")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_search_genre_filter_excludes_non_matching(self):
        """NEGATIVE: genre='Thriller' returns [] when no stored row has that genre."""
        repo = MovieMetadataRepository()
        with _select_ok([ACTION_ROW, DRAMA_ROW]):
            result = await repo.search(limit=10, genre="Thriller")
        assert result == []

    @pytest.mark.asyncio
    async def test_search_language_filter_includes_only_matching(self):
        """POSITIVE: language='French' returns only French rows."""
        repo = MovieMetadataRepository()
        with _select_ok([ACTION_ROW, FRENCH_ROW]):
            result = await repo.search(limit=10, language="French")
        assert len(result) == 1
        assert result[0]["data_json"]["Language"] == "French"

    @pytest.mark.asyncio
    async def test_search_language_filter_is_case_insensitive(self):
        """POSITIVE: language='french' matches Language='French'."""
        repo = MovieMetadataRepository()
        with _select_ok([FRENCH_ROW, ACTION_ROW]):
            result = await repo.search(limit=10, language="french")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_search_genre_and_language_combined(self):
        """POSITIVE: Both filters applied simultaneously."""
        action_french = {"movie_id": "tt_af", "data_json": {"title": "French Action", "Genre": "Action", "Language": "French"}}
        repo = MovieMetadataRepository()
        with _select_ok([ACTION_ROW, FRENCH_ROW, action_french]):
            result = await repo.search(limit=10, genre="Action", language="French")
        assert len(result) == 1
        assert result[0]["data_json"]["title"] == "French Action"

    @pytest.mark.asyncio
    async def test_search_records_without_genre_key_included_when_no_filter(self):
        """POSITIVE: Rows with no Genre key pass through when no genre filter given."""
        no_genre_row = {"movie_id": "tt_ng", "data_json": {"title": "Unknown Genre"}}
        repo = MovieMetadataRepository()
        with _select_ok([no_genre_row]):
            result = await repo.search(limit=10)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_search_limit_zero_returns_empty(self):
        """EDGE: limit=0 returns an empty list even when rows exist."""
        repo = MovieMetadataRepository()
        with _select_ok([ACTION_ROW, DRAMA_ROW]):
            result = await repo.search(limit=0)
        assert result == []
