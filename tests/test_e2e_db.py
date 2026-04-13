"""
tests/test_e2e_db.py
====================
End-to-end database tests for CineMate.

Tests every table defined in the SQL schema:
  admins, api_usage, app_config, bot_stats, error_logs,
  feedback, history, movie_metadata, sessions,
  user_interactions, users, watchlist

Requirements
------------
  SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set in the environment.
  All tables must already exist (run your SQL schema first).

Run
---
  pytest tests/test_e2e_db.py -v --tb=short
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
import time
import pytest

# ---------------------------------------------------------------------------
# Skip entire module when Supabase is not configured
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
pytestmark = pytest.mark.skipif(
    not SUPABASE_URL or not SUPABASE_KEY,
    reason="SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — skipping E2E DB tests",
)

# Deterministic test identifiers (easy to grep and clean up)
_RUN_ID = uuid.uuid4().hex[:8]
TEST_CHAT_ID = f"e2e_{_RUN_ID}"
TEST_MOVIE_ID = f"tt_e2e_{_RUN_ID}"
TEST_MOVIE_TITLE = "E2E Test Movie"
TEST_ADMIN_CHAT_ID = f"e2e_admin_{_RUN_ID}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    """Run an async coroutine synchronously in tests."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Module-level fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sb():
    import config.supabase_client as _sb
    assert _sb.is_configured(), "Supabase client reports not configured"
    return _sb


@pytest.fixture(scope="module")
def user_repo():
    from repositories.user_repository import UserRepository
    return UserRepository()


@pytest.fixture(scope="module")
def session_repo():
    from repositories.session_repository import SessionRepository
    return SessionRepository()


@pytest.fixture(scope="module")
def history_repo():
    from repositories.history_repository import HistoryRepository
    return HistoryRepository()


@pytest.fixture(scope="module")
def watchlist_repo():
    from repositories.watchlist_repository import WatchlistRepository
    return WatchlistRepository()


@pytest.fixture(scope="module")
def feedback_repo():
    from repositories.feedback_repository import FeedbackRepository
    return FeedbackRepository()


@pytest.fixture(scope="module")
def admin_repo():
    from repositories.admin_repository import AdminRepository
    return AdminRepository()


@pytest.fixture(scope="module")
def metadata_repo():
    from repositories.movie_metadata_repository import MovieMetadataRepository
    return MovieMetadataRepository()


@pytest.fixture(scope="module")
def api_usage_repo():
    from repositories.api_usage_repository import ApiUsageRepository
    return ApiUsageRepository()


@pytest.fixture(scope="module", autouse=True)
def cleanup(sb):
    """Delete all test rows after the module finishes."""
    yield
    _tables = [
        ("users",             {"chat_id": TEST_CHAT_ID}),
        ("sessions",          {"chat_id": TEST_CHAT_ID}),
        ("history",           {"chat_id": TEST_CHAT_ID}),
        ("watchlist",         {"chat_id": TEST_CHAT_ID}),
        ("feedback",          {"chat_id": TEST_CHAT_ID}),
        ("user_interactions", {"chat_id": TEST_CHAT_ID}),
        ("error_logs",        {"chat_id": TEST_CHAT_ID}),
        ("api_usage",         {"chat_id": TEST_CHAT_ID}),
        ("movie_metadata",    {"movie_id": TEST_MOVIE_ID}),
        ("admins",            {"chat_id": TEST_ADMIN_CHAT_ID}),
    ]
    for table, filters in _tables:
        try:
            sb.delete_rows(table, filters=filters)
        except Exception:
            pass
    # Clean up paginated watchlist test rows
    for i in range(12):
        try:
            sb.delete_rows("watchlist", filters={"movie_id": f"{TEST_MOVIE_ID}_p{i}"})
        except Exception:
            pass


# ---------------------------------------------------------------------------
# T01 — users table
# ---------------------------------------------------------------------------

class TestUsersTable:
    """Covers: users.upsert, users.get, preferred_genres JSONB array."""

    def test_upsert_creates_row(self, user_repo, sb):
        user_repo.upsert_user(TEST_CHAT_ID, username="E2EUser")
        rows, err = sb.select_rows("users", filters={"chat_id": TEST_CHAT_ID}, limit=1)
        assert not err, f"DB error: {err}"
        assert rows, "users row not created — BUG #4: upsert_user may not be called on /start"
        assert rows[0]["chat_id"] == TEST_CHAT_ID
        assert rows[0]["username"] == "E2EUser"

    def test_upsert_updates_username(self, user_repo, sb):
        user_repo.upsert_user(TEST_CHAT_ID, username="UpdatedUser")
        rows, _ = sb.select_rows("users", filters={"chat_id": TEST_CHAT_ID}, limit=1)
        assert rows[0]["username"] == "UpdatedUser"

    def test_get_user_returns_row(self, user_repo):
        user = user_repo.get_user(TEST_CHAT_ID)
        assert user["chat_id"] == TEST_CHAT_ID

    def test_preferred_genres_jsonb(self, user_repo, sb):
        user_repo.upsert_user(
            TEST_CHAT_ID,
            username="E2EUser",
            patch={"preferred_genres": json.dumps(["Action", "Drama"])},
        )
        rows, _ = sb.select_rows("users", filters={"chat_id": TEST_CHAT_ID}, limit=1)
        raw = rows[0].get("preferred_genres", [])
        genres = raw if isinstance(raw, list) else json.loads(raw)
        assert "Action" in genres


# ---------------------------------------------------------------------------
# T02 — sessions table
# ---------------------------------------------------------------------------

class TestSessionsTable:
    """Covers: full session row upsert, all answer_* columns, state machine."""

    def test_upsert_creates_row(self, session_repo, sb):
        row = {
            "chat_id": TEST_CHAT_ID,
            "session_state": "questioning",
            "question_index": 2,
            "answers_mood": "happy",
            "answers_genre": "Action",
            "answers_language": "English",
            "answers_era": "2000s",
            "answers_context": "solo",
            "answers_time": "2h",
            "answers_avoid": "Horror",
            "answers_favorites": "Inception",
            "answers_rating": "7+",
            "last_recs_json": "[]",
            "overflow_buffer_json": "[]",
            "sim_depth": 0,
        }
        session_repo.upsert_session(TEST_CHAT_ID, row)
        rows, err = sb.select_rows("sessions", filters={"chat_id": TEST_CHAT_ID}, limit=1)
        assert not err
        assert rows, "sessions row missing from DB"
        assert rows[0]["session_state"] == "questioning"
        assert rows[0]["question_index"] == 2
        assert rows[0]["answers_mood"] == "happy"
        assert rows[0]["answers_genre"] == "Action"

    def test_get_session_returns_answers(self, session_repo):
        row = session_repo.get_session(TEST_CHAT_ID)
        assert row.get("answers_language") == "English"
        assert row.get("answers_era") == "2000s"

    def test_session_reset_clears_state(self, session_repo, sb):
        session_repo.upsert_session(TEST_CHAT_ID, {
            "chat_id": TEST_CHAT_ID,
            "session_state": "idle",
            "question_index": 0,
            "answers_mood": None,
            "answers_genre": None,
            "last_recs_json": "[]",
            "overflow_buffer_json": "[]",
            "sim_depth": 0,
        })
        rows, _ = sb.select_rows("sessions", filters={"chat_id": TEST_CHAT_ID}, limit=1)
        assert rows[0]["session_state"] == "idle"
        assert not rows[0].get("answers_mood")


# ---------------------------------------------------------------------------
# T03 — history table
# ---------------------------------------------------------------------------

class TestHistoryTable:
    """Covers: history insert, mark_watched, get_history pagination."""

    def test_log_recommendations_writes_rows(self, history_repo, sb):
        """BUG #1 check — history must be written after recommendations."""
        history_repo.log_recommendations(TEST_CHAT_ID, [{
            "movie_id": TEST_MOVIE_ID,
            "title": TEST_MOVIE_TITLE,
            "year": "2024",
            "genres": "Drama",
            "language": "English",
            "rating": "7.5",
        }])
        rows, err = sb.select_rows(
            "history",
            filters={"chat_id": TEST_CHAT_ID, "movie_id": TEST_MOVIE_ID},
            limit=1,
        )
        assert not err
        assert rows, (
            "history row missing — BUG #1: recommendation_service.get_recommendations() "
            "does not call history_repo.log_recommendations() after enrichment"
        )
        assert rows[0]["title"] == TEST_MOVIE_TITLE
        assert rows[0]["watched"] is False

    def test_mark_watched_sets_flag(self, history_repo, sb):
        ok = history_repo.mark_watched(TEST_CHAT_ID, TEST_MOVIE_ID)
        assert ok, "mark_watched returned False"
        rows, _ = sb.select_rows(
            "history",
            filters={"chat_id": TEST_CHAT_ID, "movie_id": TEST_MOVIE_ID},
            limit=1,
        )
        assert rows[0]["watched"] is True
        assert rows[0]["watched_at"] is not None

    def test_get_history_contains_row(self, history_repo):
        rows = history_repo.get_history(TEST_CHAT_ID, page=1)
        assert any(r["movie_id"] == TEST_MOVIE_ID for r in rows)

    def test_idempotent_upsert(self, history_repo, sb):
        """Re-logging the same movie_id must not create a duplicate row."""
        history_repo.log_recommendations(TEST_CHAT_ID, [{
            "movie_id": TEST_MOVIE_ID,
            "title": TEST_MOVIE_TITLE,
            "year": "2024",
            "genres": "Drama",
            "language": "English",
            "rating": "7.5",
        }])
        rows, _ = sb.select_rows(
            "history",
            filters={"chat_id": TEST_CHAT_ID, "movie_id": TEST_MOVIE_ID},
        )
        assert len(rows) == 1, f"Duplicate history rows: {len(rows)} (expected 1)"


# ---------------------------------------------------------------------------
# T04 — watchlist table
# ---------------------------------------------------------------------------

class TestWatchlistTable:
    """Covers: add/remove, is_in_watchlist, genres NOT NULL (Bug #9), pagination."""

    def test_add_to_watchlist_writes_row(self, watchlist_repo, sb):
        ok = watchlist_repo.add_to_watchlist(TEST_CHAT_ID, {
            "movie_id": TEST_MOVIE_ID,
            "title": TEST_MOVIE_TITLE,
            "year": "2024",
            "language": "English",
            "rating": "7.5",
            "genres": "Drama",
        })
        assert ok, "add_to_watchlist returned False"
        rows, err = sb.select_rows(
            "watchlist",
            filters={"chat_id": TEST_CHAT_ID, "movie_id": TEST_MOVIE_ID},
            limit=1,
        )
        assert not err
        assert rows, "watchlist row missing from DB"

    def test_genres_not_null_or_empty(self, watchlist_repo, sb):
        """BUG #9: genres must never be None — DB has NOT NULL constraint."""
        rows, _ = sb.select_rows(
            "watchlist",
            filters={"chat_id": TEST_CHAT_ID, "movie_id": TEST_MOVIE_ID},
            limit=1,
        )
        genres = rows[0].get("genres")
        assert genres is not None, "genres is NULL in watchlist — BUG #9"
        assert genres != "", "genres is empty string in watchlist — BUG #9"

    def test_is_in_watchlist_returns_true(self, watchlist_repo):
        assert watchlist_repo.is_in_watchlist(TEST_CHAT_ID, TEST_MOVIE_ID)

    def test_is_in_watchlist_returns_false_for_unknown(self, watchlist_repo):
        assert not watchlist_repo.is_in_watchlist(TEST_CHAT_ID, "tt_nonexistent_xyz")

    def test_remove_from_watchlist(self, watchlist_repo, sb):
        watchlist_repo.remove_from_watchlist(TEST_CHAT_ID, TEST_MOVIE_ID)
        rows, _ = sb.select_rows(
            "watchlist",
            filters={"chat_id": TEST_CHAT_ID, "movie_id": TEST_MOVIE_ID},
            limit=1,
        )
        assert not rows, "Row still in DB after remove_from_watchlist"

    def test_pagination(self, watchlist_repo):
        """Insert 12 rows, verify page 1 returns 10 and page 2 returns 2."""
        for i in range(12):
            watchlist_repo.add_to_watchlist(TEST_CHAT_ID, {
                "movie_id": f"{TEST_MOVIE_ID}_p{i}",
                "title": f"Paginated Movie {i}",
                "year": "2024",
                "language": "English",
                "rating": "7.0",
                "genres": "Action",
            })
        page1 = watchlist_repo.get_watchlist(TEST_CHAT_ID, page=1)
        page2 = watchlist_repo.get_watchlist(TEST_CHAT_ID, page=2)
        assert len(page1) == 10, f"Page 1 expected 10 rows, got {len(page1)}"
        assert len(page2) == 2, f"Page 2 expected 2 rows, got {len(page2)}"


# ---------------------------------------------------------------------------
# T05 — feedback table
# ---------------------------------------------------------------------------

class TestFeedbackTable:
    """Covers: like/dislike insert, CHECK constraint on reaction_type, upsert."""

    def test_like_stored(self, feedback_repo, sb):
        feedback_repo.upsert_feedback(TEST_CHAT_ID, TEST_MOVIE_ID, "like")
        rows, err = sb.select_rows(
            "feedback",
            filters={"chat_id": TEST_CHAT_ID, "movie_id": TEST_MOVIE_ID},
            limit=1,
        )
        assert not err
        assert rows, "feedback row missing"
        assert rows[0]["reaction_type"] == "like"

    def test_dislike_overwrites_like(self, feedback_repo, sb):
        feedback_repo.upsert_feedback(TEST_CHAT_ID, TEST_MOVIE_ID, "dislike")
        rows, _ = sb.select_rows(
            "feedback",
            filters={"chat_id": TEST_CHAT_ID, "movie_id": TEST_MOVIE_ID},
            limit=1,
        )
        assert rows[0]["reaction_type"] == "dislike"

    def test_get_feedback_returns_correct_value(self, feedback_repo):
        val = feedback_repo.get_feedback(TEST_CHAT_ID, TEST_MOVIE_ID)
        assert val == "dislike"

    def test_invalid_reaction_type_rejected(self, feedback_repo):
        """DB CHECK constraint must reject values other than like/dislike."""
        with pytest.raises(Exception):
            feedback_repo.upsert_feedback(TEST_CHAT_ID, TEST_MOVIE_ID, "love")


# ---------------------------------------------------------------------------
# T06 — admins table (BUG #5)
# ---------------------------------------------------------------------------

class TestAdminsTable:
    """BUG #5: admins table must be seeded from ADMIN_CHAT_IDS env var."""

    def test_manual_admin_insert(self, admin_repo, sb):
        """Direct insert into admins table must make is_admin() return True."""
        sb.upsert_rows("admins", [{"chat_id": TEST_ADMIN_CHAT_ID}], on_conflict="chat_id")
        rows, err = sb.select_rows("admins", filters={"chat_id": TEST_ADMIN_CHAT_ID}, limit=1)
        assert not err
        assert rows, "admin row not found after upsert"
        assert admin_repo.is_admin(TEST_ADMIN_CHAT_ID), "is_admin() returned False after DB insert"

    def test_non_admin_returns_false(self, admin_repo):
        assert not admin_repo.is_admin("totally_random_stranger_9999")


# ---------------------------------------------------------------------------
# T07 — app_config table
# ---------------------------------------------------------------------------

class TestAppConfigTable:

    def test_set_and_get_config(self, admin_repo):
        admin_repo.set_config("e2e_test_key", "hello_world")
        val = admin_repo.get_config("e2e_test_key")
        assert val == "hello_world", f"Expected 'hello_world', got '{val}'"

    def test_overwrite_config(self, admin_repo):
        admin_repo.set_config("e2e_test_key", "updated_value")
        val = admin_repo.get_config("e2e_test_key")
        assert val == "updated_value"

    def test_missing_key_returns_none(self, admin_repo):
        val = admin_repo.get_config("nonexistent_key_xyz_e2e")
        assert val is None


# ---------------------------------------------------------------------------
# T08 — bot_stats table (BUG #3)
# ---------------------------------------------------------------------------

class TestBotStatsTable:
    """BUG #3: increment_stat() must actually persist values to bot_stats."""

    def test_increment_creates_and_increments(self, admin_repo, sb):
        metric = f"e2e_test_metric_{_RUN_ID}"
        admin_repo.increment_stat(metric, by=1)
        admin_repo.increment_stat(metric, by=1)
        stats = admin_repo.get_all_stats()
        assert metric in stats, f"Metric '{metric}' not found in bot_stats — BUG #3"
        assert stats[metric] >= 2, f"Expected >=2, got {stats[metric]}"
        # Cleanup
        try:
            sb.delete_rows("bot_stats", filters={"metric_name": metric})
        except Exception:
            pass

    def test_total_interactions_increments_via_worker(self, admin_repo, sb):
        """Simulate worker calling increment_stat('total_interactions')."""
        before = admin_repo.get_all_stats().get("total_interactions", 0)
        admin_repo.increment_stat("total_interactions")
        after = admin_repo.get_all_stats().get("total_interactions", 0)
        assert after == before + 1, f"total_interactions not incremented: {before} → {after}"


# ---------------------------------------------------------------------------
# T09 — error_logs table
# ---------------------------------------------------------------------------

class TestErrorLogsTable:

    def test_error_batcher_writes_to_db(self, sb):
        """Emit an error via error_batcher and confirm it reaches the DB."""
        from services.logging_service import error_batcher
        error_batcher.emit({
            "chat_id": TEST_CHAT_ID,
            "error_type": "e2e_test_error",
            "error_message": "E2E test error message",
            "workflow_step": "test_step",
            "intent": "test_intent",
            "request_id": f"e2e_{_RUN_ID}",
            "raw_payload": "{}",
            "timestamp": __import__("utils.time_utils", fromlist=["utc_now_iso"]).utc_now_iso(),
        })
        error_batcher.flush()
        time.sleep(0.5)  # allow async flush
        rows, err = sb.select_rows(
            "error_logs",
            filters={"chat_id": TEST_CHAT_ID},
            limit=10,
        )
        assert not err
        assert rows, "error_logs row not written — batch flush may not have run"
        assert any(r.get("error_type") == "e2e_test_error" for r in rows)


# ---------------------------------------------------------------------------
# T10 — api_usage table (BUG #2)
# ---------------------------------------------------------------------------

class TestApiUsageTable:
    """BUG #2: api_usage table was never written anywhere before this fix."""

    def test_log_writes_row(self, api_usage_repo, sb):
        api_usage_repo.log(
            provider="omdb",
            action="get_movie",
            chat_id=TEST_CHAT_ID,
            total_tokens=None,
        )
        rows, err = sb.select_rows(
            "api_usage",
            filters={"chat_id": TEST_CHAT_ID},
            limit=5,
        )
        assert not err
        assert rows, "api_usage row missing — BUG #2: ApiUsageRepository.log() not called"
        assert rows[0]["provider"] == "omdb"

    def test_log_with_tokens(self, api_usage_repo, sb):
        api_usage_repo.log(
            provider="perplexity",
            action="recommend",
            chat_id=TEST_CHAT_ID,
            prompt_tokens=120,
            completion_tokens=80,
            total_tokens=200,
        )
        rows, _ = sb.select_rows(
            "api_usage",
            filters={"chat_id": TEST_CHAT_ID},
            limit=10,
        )
        plex_rows = [r for r in rows if r.get("provider") == "perplexity"]
        assert plex_rows, "Perplexity api_usage row missing"
        assert plex_rows[0]["total_tokens"] == 200

    def test_log_api_usage_via_logging_service(self, sb):
        """Confirm LoggingService.log_api_usage() delegates to ApiUsageRepository."""
        from services.logging_service import LoggingService
        LoggingService.log_api_usage(
            provider="watchmode",
            action="get_streaming",
            chat_id=TEST_CHAT_ID,
            total_tokens=0,
        )
        rows, _ = sb.select_rows(
            "api_usage",
            filters={"chat_id": TEST_CHAT_ID},
            limit=10,
        )
        wm_rows = [r for r in rows if r.get("provider") == "watchmode"]
        assert wm_rows, "watchmode api_usage row missing after LoggingService.log_api_usage()"


# ---------------------------------------------------------------------------
# T11 — movie_metadata table
# ---------------------------------------------------------------------------

class TestMovieMetadataTable:

    def test_upsert_and_get_metadata(self, metadata_repo, sb):
        payload = {"imdbID": TEST_MOVIE_ID, "Title": TEST_MOVIE_TITLE, "Year": "2024"}
        metadata_repo.upsert_metadata(TEST_MOVIE_ID, payload)
        rows, err = sb.select_rows(
            "movie_metadata",
            filters={"movie_id": TEST_MOVIE_ID},
            limit=1,
        )
        assert not err
        assert rows, "movie_metadata row missing"
        data = rows[0].get("data_json", {})
        if isinstance(data, str):
            data = json.loads(data)
        assert data.get("Title") == TEST_MOVIE_TITLE

    def test_get_metadata_returns_dict(self, metadata_repo):
        data = metadata_repo.get_metadata(TEST_MOVIE_ID)
        assert data is not None
        assert data.get("imdbID") == TEST_MOVIE_ID

    def test_upsert_updates_existing(self, metadata_repo, sb):
        updated = {"imdbID": TEST_MOVIE_ID, "Title": "Updated Title", "Year": "2025"}
        metadata_repo.upsert_metadata(TEST_MOVIE_ID, updated)
        rows, _ = sb.select_rows(
            "movie_metadata",
            filters={"movie_id": TEST_MOVIE_ID},
            limit=1,
        )
        data = rows[0].get("data_json", {})
        if isinstance(data, str):
            data = json.loads(data)
        assert data.get("Title") == "Updated Title"
        # Confirm still only one row
        all_rows, _ = sb.select_rows(
            "movie_metadata",
            filters={"movie_id": TEST_MOVIE_ID},
        )
        assert len(all_rows) == 1, f"Expected 1 metadata row, got {len(all_rows)}"


# ---------------------------------------------------------------------------
# T12 — user_interactions table (BUG #8)
# ---------------------------------------------------------------------------

class TestUserInteractionsTable:
    """BUG #8: bot_response column was always empty string before fix."""

    def test_log_interaction_writes_row(self, sb):
        from services.logging_service import LoggingService, interaction_batcher
        LoggingService.log_interaction(
            chat_id=TEST_CHAT_ID,
            input_text="/trending",
            response_text="Here are some trending movies!",
            intent="trending",
            latency_ms=123,
            username="E2EUser",
            request_id=f"e2e_{_RUN_ID}",
        )
        interaction_batcher.flush()
        time.sleep(0.5)
        rows, err = sb.select_rows(
            "user_interactions",
            filters={"chat_id": TEST_CHAT_ID},
            limit=5,
        )
        assert not err
        assert rows, "user_interactions row missing"
        assert rows[0]["intent"] == "trending"

    def test_bot_response_is_populated(self, sb):
        """BUG #8 check — bot_response must not be empty string."""
        rows, _ = sb.select_rows(
            "user_interactions",
            filters={"chat_id": TEST_CHAT_ID},
            limit=5,
        )
        for row in rows:
            response = row.get("bot_response", "")
            assert response, (
                f"bot_response is empty for row {row.get('id')} — "
                "BUG #8: worker_service never captured handler response text"
            )
