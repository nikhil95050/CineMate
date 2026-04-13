"""Tests for Feature 8 fixes:
  1. handle_star serialises MovieModel exactly once (no double model_dump round-trip)
  2. history add failures are logged per-movie, not silently swallowed as one block
  3. _build_share_card / _streaming_label handles str, list, dict, None, N/A safely
"""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_movie(movie_id: str = "tt0111161", title: str = "The Shawshank Redemption") -> MagicMock:
    m = MagicMock()
    m.movie_id = movie_id
    m.model_dump.return_value = {
        "movie_id": movie_id,
        "title": title,
        "year": 1994,
        "rating": 9.3,
        "genres": "Drama",
        "reason": "Top rated",
        "streaming": "Netflix",
    }
    return m


# ---------------------------------------------------------------------------
# Fix 1 — model_dump called exactly once per movie, not twice
# ---------------------------------------------------------------------------

class TestModelDumpCalledOnce:
    """handle_star must serialise each MovieModel exactly once.

    Previously model_dump() was called twice per movie:
      1. to build last_recs_json
      2. to pass to send_movies_async
    The fix introduces _movie_to_dict() so serialisation happens once and
    the resulting list is shared by both consumers.
    """

    @pytest.mark.asyncio
    async def test_model_dump_called_once_per_movie(self):
        movies = [_make_movie("tt0111161"), _make_movie("tt0068646", "The Godfather")]

        with patch("handlers.discovery_handlers.discovery_service") as mock_ds, \
             patch("handlers.discovery_handlers.history_service"), \
             patch("handlers.discovery_handlers.session_service") as mock_ss, \
             patch("handlers.discovery_handlers.send_movies_async", new_callable=AsyncMock), \
             patch("handlers.discovery_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.discovery_handlers.show_typing", new_callable=AsyncMock):

            mock_ds.get_star_movies = AsyncMock(return_value=movies)
            mock_ss.get_session.return_value = MagicMock(last_recs_json="[]")

            from handlers.discovery_handlers import handle_star
            await handle_star(chat_id="123", input_text="/star Nolan")

        for movie in movies:
            assert movie.model_dump.call_count == 1, (
                f"model_dump called {movie.model_dump.call_count} times for {movie.movie_id}; "
                "expected exactly 1"
            )

    @pytest.mark.asyncio
    async def test_send_movies_receives_dicts_not_models(self):
        movie = _make_movie()

        with patch("handlers.discovery_handlers.discovery_service") as mock_ds, \
             patch("handlers.discovery_handlers.history_service"), \
             patch("handlers.discovery_handlers.session_service") as mock_ss, \
             patch("handlers.discovery_handlers.send_movies_async", new_callable=AsyncMock) as mock_send, \
             patch("handlers.discovery_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.discovery_handlers.show_typing", new_callable=AsyncMock):

            mock_ds.get_star_movies = AsyncMock(return_value=[movie])
            mock_ss.get_session.return_value = MagicMock(last_recs_json="[]")

            from handlers.discovery_handlers import handle_star
            await handle_star(chat_id="123", input_text="/star Nolan")

        sent_arg = mock_send.call_args[0][1]
        assert isinstance(sent_arg, list)
        assert all(isinstance(item, dict) for item in sent_arg), (
            "send_movies_async should receive plain dicts, not MovieModel objects"
        )


# ---------------------------------------------------------------------------
# Fix 2 — per-movie history failures are individually logged
# ---------------------------------------------------------------------------

class TestPerMovieHistoryFailures:
    """Each movie's history insertion failure must be caught and logged separately.

    Previously a single try/except around the entire loop meant the second
    movie would never be attempted if the first raised.  The fix wraps each
    iteration individually so partial history writes succeed.
    """

    @pytest.mark.asyncio
    async def test_second_movie_attempted_even_when_first_fails(self):
        movie_a = _make_movie("tt0111161", "Shawshank")
        movie_b = _make_movie("tt0068646", "Godfather")

        call_count = {"n": 0}

        def flaky_add(chat_id, movie):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("Redis timeout")

        with patch("handlers.discovery_handlers.discovery_service") as mock_ds, \
             patch("handlers.discovery_handlers.history_service") as mock_hs, \
             patch("handlers.discovery_handlers.session_service") as mock_ss, \
             patch("handlers.discovery_handlers.send_movies_async", new_callable=AsyncMock), \
             patch("handlers.discovery_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.discovery_handlers.show_typing", new_callable=AsyncMock):

            mock_ds.get_star_movies = AsyncMock(return_value=[movie_a, movie_b])
            mock_hs.add_to_history.side_effect = flaky_add
            mock_ss.get_session.return_value = MagicMock(last_recs_json="[]")

            from handlers.discovery_handlers import handle_star
            await handle_star(chat_id="123", input_text="/star Nolan")

        assert mock_hs.add_to_history.call_count == 2, (
            "Both movies must be attempted even when the first insertion fails"
        )

    @pytest.mark.asyncio
    async def test_user_still_receives_cards_when_history_fails(self):
        movie = _make_movie()

        with patch("handlers.discovery_handlers.discovery_service") as mock_ds, \
             patch("handlers.discovery_handlers.history_service") as mock_hs, \
             patch("handlers.discovery_handlers.session_service") as mock_ss, \
             patch("handlers.discovery_handlers.send_movies_async", new_callable=AsyncMock) as mock_send, \
             patch("handlers.discovery_handlers.send_message", new_callable=AsyncMock), \
             patch("handlers.discovery_handlers.show_typing", new_callable=AsyncMock):

            mock_ds.get_star_movies = AsyncMock(return_value=[movie])
            mock_hs.add_to_history.side_effect = RuntimeError("DB down")
            mock_ss.get_session.return_value = MagicMock(last_recs_json="[]")

            from handlers.discovery_handlers import handle_star
            await handle_star(chat_id="123", input_text="/star Nolan")

        mock_send.assert_called_once(), "Cards must still be sent even if history write fails"


# ---------------------------------------------------------------------------
# Fix 3 — _streaming_label type guard: str / list / dict / None / N/A
# ---------------------------------------------------------------------------

class TestStreamingLabelTypeGuard:
    """_streaming_label must handle every possible streaming field type safely."""

    def setup_method(self):
        from handlers.discovery_handlers import _streaming_label
        self._fn = _streaming_label

    def test_none_returns_empty(self):
        assert self._fn(None) == ""

    def test_empty_string_returns_empty(self):
        assert self._fn("") == ""

    def test_na_string_returns_empty(self):
        assert self._fn("N/A") == ""
        assert self._fn("n/a") == ""

    def test_none_string_returns_empty(self):
        assert self._fn("None") == ""
        assert self._fn("none") == ""
        assert self._fn("NONE") == ""

    def test_plain_string_returned_as_is(self):
        assert self._fn("Netflix") == "Netflix"

    def test_list_joined(self):
        assert self._fn(["Netflix", "Hulu"]) == "Netflix, Hulu"

    def test_list_with_empty_entries_filtered(self):
        result = self._fn(["", "Prime", None])
        assert "Prime" in result
        assert result.startswith("Prime") or result.endswith("Prime")

    def test_dict_keys_joined(self):
        result = self._fn({"Netflix": "https://netflix.com", "Prime": "https://prime.com"})
        assert "Netflix" in result
        assert "Prime" in result

    def test_dict_empty_returns_empty(self):
        assert self._fn({}) == ""

    def test_build_share_card_does_not_raise_on_dict_streaming(self):
        from handlers.discovery_handlers import _build_share_card
        recs = [{
            "title": "Inception",
            "year": 2010,
            "rating": 8.8,
            "genres": "Sci-Fi",
            "reason": "Mind-bending",
            "streaming": {"Netflix": "https://netflix.com/inception"},
        }]
        card = _build_share_card(recs)
        assert "Netflix" in card

    def test_build_share_card_does_not_raise_on_list_streaming(self):
        from handlers.discovery_handlers import _build_share_card
        recs = [{"title": "Dune", "streaming": ["HBO Max", "Prime"]}]
        card = _build_share_card(recs)
        assert "HBO Max" in card

    def test_build_share_card_skips_na_streaming(self):
        from handlers.discovery_handlers import _build_share_card
        recs = [{"title": "Dune", "streaming": "N/A"}]
        card = _build_share_card(recs)
        assert "\U0001f4fa" not in card  # TV emoji should not appear
