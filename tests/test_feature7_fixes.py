"""Targeted tests for the three Feature-7 issues fixed in this PR.

Issue 1 — Taste vector only captured genres, not actors/directors.
Issue 2 — _schedule_taste_recompute used deprecated asyncio.get_event_loop().
Issue 3 — handle_min_rating with no argument (bare command) was untested.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.user_service import UserService


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_user_service(
    liked_rows,
    history_map: dict,
) -> UserService:
    """Build a UserService wired to in-memory fakes."""
    feedback_repo = MagicMock()
    feedback_repo.get_likes.return_value = liked_rows

    history_repo = MagicMock()
    history_repo.get_by_movie_id.side_effect = lambda chat_id, mid: history_map.get(mid)

    user_repo = MagicMock()
    user_repo.get_user.return_value = {
        "chat_id": "42",
        "username": "Nikhil",
        "preferred_genres": [],
        "disliked_genres": [],
        "user_taste_vector": None,
        "avg_rating_preference": None,
        "subscriptions": [],
    }

    return UserService(
        user_repo=user_repo,
        feedback_repo=feedback_repo,
        history_repo=history_repo,
    )


# ===========================================================================
# Issue 1 — Actors and directors must appear in taste vector
# ===========================================================================

class TestTasteVectorIncludesActorsAndDirectors:
    """recompute_taste_profile must derive top_actors and top_directors."""

    def test_taste_vector_has_top_actors_key(self):
        liked = [{"movie_id": "tt0111161"}]
        history = {
            "tt0111161": {
                "genres": "Drama",
                "actors": "Tim Robbins, Morgan Freeman",
                "director": "Frank Darabont",
            }
        }
        svc = _make_user_service(liked, history)
        svc.recompute_taste_profile("42")

        upsert_call = svc.user_repo.upsert_user.call_args
        patch_arg = upsert_call[1]["patch"]
        tv = patch_arg["user_taste_vector"]

        assert "top_actors" in tv, "taste vector missing top_actors"
        assert "top_directors" in tv, "taste vector missing top_directors"

    def test_top_actors_ranked_by_frequency(self):
        """Actors appearing in more liked movies rank higher."""
        liked = [
            {"movie_id": "tt0111161"},
            {"movie_id": "tt0068646"},
            {"movie_id": "tt0071562"},
        ]
        history = {
            "tt0111161": {
                "genres": "Drama",
                "actors": "Morgan Freeman, Tim Robbins",
                "director": "Frank Darabont",
            },
            "tt0068646": {
                "genres": "Crime, Drama",
                "actors": "Marlon Brando, Morgan Freeman",
                "director": "Francis Ford Coppola",
            },
            "tt0071562": {
                "genres": "Crime, Drama",
                "actors": "Al Pacino, Morgan Freeman",
                "director": "Francis Ford Coppola",
            },
        }
        svc = _make_user_service(liked, history)
        svc.recompute_taste_profile("42")

        patch_arg = svc.user_repo.upsert_user.call_args[1]["patch"]
        tv = patch_arg["user_taste_vector"]

        # Morgan Freeman appears in all 3 liked movies — must be #1
        assert tv["top_actors"][0] == "Morgan Freeman"
        # Francis Ford Coppola directed 2 of 3 — must be #1 director
        assert tv["top_directors"][0] == "Francis Ford Coppola"

    def test_actor_counts_present_in_vector(self):
        liked = [{"movie_id": "tt0133093"}]
        history = {
            "tt0133093": {
                "genres": "Action, Sci-Fi",
                "actors": "Keanu Reeves, Laurence Fishburne",
                "director": "The Wachowskis",
            }
        }
        svc = _make_user_service(liked, history)
        svc.recompute_taste_profile("42")

        patch_arg = svc.user_repo.upsert_user.call_args[1]["patch"]
        tv = patch_arg["user_taste_vector"]

        assert "actor_counts" in tv
        assert "director_counts" in tv
        assert tv["actor_counts"]["Keanu Reeves"] == 1
        assert tv["director_counts"]["The Wachowskis"] == 1

    def test_missing_actors_field_does_not_crash(self):
        """History rows without actors/director keys must not raise."""
        liked = [{"movie_id": "tt0000001"}]
        history = {
            "tt0000001": {
                "genres": "Drama",
                # No 'actors' or 'director' key at all
            }
        }
        svc = _make_user_service(liked, history)
        # Must complete without raising
        svc.recompute_taste_profile("42")

        patch_arg = svc.user_repo.upsert_user.call_args[1]["patch"]
        tv = patch_arg["user_taste_vector"]
        assert tv["top_actors"] == []
        assert tv["top_directors"] == []

    def test_genres_still_correctly_populated(self):
        """Existing genre logic must not regress after the actor/director addition."""
        liked = [
            {"movie_id": "tt0111161"},
            {"movie_id": "tt0068646"},
        ]
        history = {
            "tt0111161": {"genres": "Drama", "actors": "", "director": ""},
            "tt0068646": {"genres": "Drama, Crime", "actors": "", "director": ""},
        }
        svc = _make_user_service(liked, history)
        svc.recompute_taste_profile("42")

        patch_arg = svc.user_repo.upsert_user.call_args[1]["patch"]
        assert "Drama" in patch_arg["preferred_genres"]
        tv = patch_arg["user_taste_vector"]
        assert tv["genre_counts"]["Drama"] == 2


# ===========================================================================
# Issue 2 — _schedule_taste_recompute must use get_running_loop, not get_event_loop
# ===========================================================================

class TestScheduleTasteRecomputeUsesRunningLoop:
    """_schedule_taste_recompute must call asyncio.get_running_loop()."""

    def test_source_does_not_call_get_event_loop(self):
        """Inspect source to ensure the deprecated API is not referenced."""
        import inspect
        from handlers import feedback_handlers

        source = inspect.getsource(feedback_handlers._schedule_taste_recompute)
        assert "get_event_loop" not in source, (
            "_schedule_taste_recompute still uses deprecated asyncio.get_event_loop(). "
            "Replace with asyncio.get_running_loop()."
        )

    def test_source_calls_get_running_loop(self):
        import inspect
        from handlers import feedback_handlers

        source = inspect.getsource(feedback_handlers._schedule_taste_recompute)
        assert "get_running_loop" in source, (
            "_schedule_taste_recompute does not use asyncio.get_running_loop()."
        )

    def test_schedule_uses_run_in_executor_inside_running_loop(self):
        """When called from an async context, run_in_executor must be invoked."""
        from handlers import feedback_handlers

        mock_loop = MagicMock()
        mock_loop.run_in_executor.return_value = None

        with patch("handlers.feedback_handlers.asyncio.get_running_loop", return_value=mock_loop):
            with patch("handlers.feedback_handlers.user_service") as mock_us:
                feedback_handlers._schedule_taste_recompute("99")

        mock_loop.run_in_executor.assert_called_once()
        # Ensure it was called with the recompute function and the chat_id
        call_args = mock_loop.run_in_executor.call_args[0]
        assert call_args[0] is None  # executor=None means default thread pool
        assert call_args[2] == "99"  # chat_id passed through

    def test_schedule_falls_back_to_sync_when_no_running_loop(self):
        """When there is no running event loop (e.g. tests/CLI),
        recompute_taste_profile must be called synchronously.
        """
        from handlers import feedback_handlers

        with patch(
            "handlers.feedback_handlers.asyncio.get_running_loop",
            side_effect=RuntimeError("no running event loop"),
        ):
            with patch("handlers.feedback_handlers.user_service") as mock_us:
                feedback_handlers._schedule_taste_recompute("42")

        mock_us.recompute_taste_profile.assert_called_once_with("42")


# ===========================================================================
# Issue 3 — handle_min_rating with no argument must prompt for usage
# ===========================================================================

class TestHandleMinRatingNoArgument:
    """Sending a bare /min_rating (or 'min_rating' with no value) must
    return a usage prompt, not raise or silently swallow the input.
    """

    @pytest.fixture(autouse=True)
    def _patch_send(self):
        with patch(
            "handlers.feedback_handlers.send_message", new_callable=AsyncMock
        ) as mock_send:
            self.mock_send = mock_send
            yield

    @pytest.mark.asyncio
    async def test_bare_slash_command_prompts_usage(self):
        from handlers.feedback_handlers import handle_min_rating

        await handle_min_rating(chat_id="42", input_text="/min_rating")

        self.mock_send.assert_awaited_once()
        sent_text: str = self.mock_send.call_args[0][1]
        assert "Usage" in sent_text or "usage" in sent_text.lower() or "min_rating" in sent_text

    @pytest.mark.asyncio
    async def test_bare_command_without_slash_prompts_usage(self):
        """Worker-routed 'min_rating' with no value must also prompt."""
        from handlers.feedback_handlers import handle_min_rating

        await handle_min_rating(chat_id="42", input_text="min_rating")

        self.mock_send.assert_awaited_once()
        sent_text: str = self.mock_send.call_args[0][1]
        assert "min_rating" in sent_text

    @pytest.mark.asyncio
    async def test_empty_input_prompts_usage(self):
        from handlers.feedback_handlers import handle_min_rating

        await handle_min_rating(chat_id="42", input_text="")

        self.mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_valid_value_does_not_trigger_usage_prompt(self):
        """Regression: a valid command must not show the usage prompt."""
        from handlers.feedback_handlers import handle_min_rating

        with patch("handlers.feedback_handlers.user_service") as mock_us:
            await handle_min_rating(chat_id="42", input_text="/min_rating 7.5")

        # send_message should be called once with the success confirmation,
        # not the usage prompt
        self.mock_send.assert_awaited_once()
        sent_text: str = self.mock_send.call_args[0][1]
        assert "7.5" in sent_text
        assert "Usage" not in sent_text

    @pytest.mark.asyncio
    async def test_rating_command_alias_no_arg_prompts_usage(self):
        """The /rating alias bare command must also return usage."""
        from handlers.feedback_handlers import handle_min_rating

        await handle_min_rating(chat_id="42", input_text="/rating")

        self.mock_send.assert_awaited_once()
        sent_text: str = self.mock_send.call_args[0][1]
        assert "min_rating" in sent_text
