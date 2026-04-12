"""Integration tests for Feature 7: feedback, taste profile, and rating preference.

These tests write REAL dummy data to Supabase, assert against it, then
clean up every row they inserted.  They are skipped automatically when
Supabase credentials are not present in the environment.

IMPORTANT: This file uses a module-level autouse fixture (``_use_real_supabase``)
that RESTORES the real Supabase credentials for the duration of every test in
this module, overriding the conftest._isolate_from_supabase patch that would
otherwise blank SUPABASE_URL and make is_configured() return False.

Run:
    python -m pytest tests/test_feedback_integration.py -v -m integration

Or run alongside unit tests (they will be skipped if no creds):
    python -m pytest tests/ -v
"""
from __future__ import annotations

import json
import os
import time
import pytest

from dotenv import load_dotenv
load_dotenv()  # ensure .env is loaded before we read credentials


# ---------------------------------------------------------------------------
# Module-level skip: skip entire file if Supabase creds are genuinely absent
# ---------------------------------------------------------------------------

_SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
_SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    or os.environ.get("SUPABASE_API_KEY", "").strip()
)

if not (_SUPABASE_URL and _SUPABASE_KEY):
    pytest.skip(
        "Supabase credentials not configured — skipping integration tests",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Override conftest patches: restore real Supabase config for this module
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _use_real_supabase():
    """Undo the conftest._isolate_from_supabase patch for integration tests.

    conftest blanks SUPABASE_URL and patches is_configured -> False for every
    test.  Integration tests need the REAL credentials, so this autouse fixture
    restores them for every test in this module.
    """
    import config.supabase_client as _sb

    # Restore real values on the module object
    _sb.SUPABASE_URL     = _SUPABASE_URL
    _sb.SUPABASE_API_KEY = _SUPABASE_KEY
    _sb.REST_BASE        = f"{_SUPABASE_URL}/rest/v1"

    # Restore the real is_configured function (conftest may have replaced it)
    def _real_is_configured() -> bool:
        return bool(_sb.SUPABASE_URL and _sb.SUPABASE_API_KEY)

    _sb.is_configured = _real_is_configured

    # Restore on EVERY repository module that captured the sb reference,
    # including UserRepository (_ur) which was previously missing and caused
    # get_user() to fall back to the empty in-memory store.
    try:
        import repositories.history_repository as _hr
        import repositories.watchlist_repository as _wr
        import repositories.feedback_repository as _fr
        import repositories.user_repository as _ur
        _hr.sb.is_configured = _real_is_configured
        _wr.sb.is_configured = _real_is_configured
        _fr.sb.is_configured = _real_is_configured
        _ur.sb.is_configured = _real_is_configured
    except Exception:
        pass

    yield  # run the test

    # No teardown needed — conftest will re-apply its patch for the next test


pytestmark = pytest.mark.integration

import config.supabase_client as sb
from repositories.feedback_repository import FeedbackRepository
from repositories.history_repository import HistoryRepository
from repositories.user_repository import UserRepository
from services.user_service import UserService


# ---------------------------------------------------------------------------
# Dummy data constants
# ---------------------------------------------------------------------------

CHAT_ID = "test_integ_feedback_u1"
MOVIE_A = "tt_integ_001"   # liked
MOVIE_B = "tt_integ_002"   # liked
MOVIE_C = "tt_integ_003"   # disliked
MOVIE_D = "tt_integ_004"   # liked then flipped to dislike

HISTORY_ROWS = [
    {
        "movie_id": MOVIE_A, "title": "Inception",
        "year": "2010", "genres": "Sci-Fi,Thriller",
        "language": "en", "rating": "8.8",
    },
    {
        "movie_id": MOVIE_B, "title": "The Dark Knight",
        "year": "2008", "genres": "Action,Crime,Drama",
        "language": "en", "rating": "9.0",
    },
    {
        "movie_id": MOVIE_C, "title": "Transformers",
        "year": "2007", "genres": "Action,Sci-Fi",
        "language": "en", "rating": "7.1",
    },
    {
        "movie_id": MOVIE_D, "title": "Twilight",
        "year": "2008", "genres": "Romance,Fantasy",
        "language": "en", "rating": "5.2",
    },
]


def _make_user_service() -> UserService:
    """Wire UserService with real repo instances."""
    return UserService(
        user_repo=UserRepository(),
        feedback_repo=FeedbackRepository(),
        history_repo=HistoryRepository(),
    )


def _get_rating_pref(row: dict) -> float:
    """Read avg_rating_preference from a user row safely.

    Uses an explicit None check instead of a falsy `or` fallback so that
    the valid boundary value 0.0 is not confused with a missing value.
    Returns -1.0 only when the field is genuinely absent / None.
    """
    raw = row.get("avg_rating_preference")
    if raw is None:
        return -1.0
    try:
        return float(raw)
    except (ValueError, TypeError):
        return -1.0


# ---------------------------------------------------------------------------
# Module-scoped fixture: seed tables, yield repos, delete all seeded rows
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def seeded_db():
    """Insert dummy history + user rows, yield repos, then delete everything."""
    # Restore real Supabase config for module-scoped setup (fixture runs once)
    import config.supabase_client as _sb
    _sb.SUPABASE_URL     = _SUPABASE_URL
    _sb.SUPABASE_API_KEY = _SUPABASE_KEY
    _sb.REST_BASE        = f"{_SUPABASE_URL}/rest/v1"
    _sb.is_configured    = lambda: bool(_sb.SUPABASE_URL and _sb.SUPABASE_API_KEY)

    hist_repo = HistoryRepository()
    fb_repo   = FeedbackRepository()
    user_repo = UserRepository()

    # Seed history
    hist_repo.log_recommendations(CHAT_ID, HISTORY_ROWS)

    # Seed user row
    sb.insert_rows(
        "users",
        [{
            "chat_id":               CHAT_ID,
            "username":              "IntegTestUser",
            "preferred_genres":      json.dumps([]),
            "disliked_genres":       json.dumps([]),
            "preferred_language":    "en",
            "subscriptions":         json.dumps([]),
            "avg_rating_preference": None,
            "user_taste_vector":     None,
        }],
        upsert=True,
        on_conflict="chat_id",
    )

    yield hist_repo, fb_repo, user_repo

    # Teardown
    sb.delete_rows("feedback", {"chat_id": CHAT_ID})
    sb.delete_rows("history",  {"chat_id": CHAT_ID})
    sb.delete_rows("users",    {"chat_id": CHAT_ID})


# ---------------------------------------------------------------------------
# 1. FeedbackRepository
# ---------------------------------------------------------------------------

class TestFeedbackRepositoryIntegration:

    def test_like_is_persisted(self, seeded_db):
        _, fb_repo, _ = seeded_db
        fb_repo.log_reaction(CHAT_ID, MOVIE_A, "like")
        time.sleep(0.3)
        assert fb_repo.get_reaction(CHAT_ID, MOVIE_A) == "like"

    def test_dislike_is_persisted(self, seeded_db):
        _, fb_repo, _ = seeded_db
        fb_repo.log_reaction(CHAT_ID, MOVIE_C, "dislike")
        time.sleep(0.3)
        assert fb_repo.get_reaction(CHAT_ID, MOVIE_C) == "dislike"

    def test_reaction_flip_like_to_dislike(self, seeded_db):
        _, fb_repo, _ = seeded_db
        fb_repo.log_reaction(CHAT_ID, MOVIE_D, "like")
        time.sleep(0.2)
        fb_repo.log_reaction(CHAT_ID, MOVIE_D, "dislike")
        time.sleep(0.3)
        assert fb_repo.get_reaction(CHAT_ID, MOVIE_D) == "dislike"

    def test_get_likes_returns_only_likes(self, seeded_db):
        _, fb_repo, _ = seeded_db
        fb_repo.log_reaction(CHAT_ID, MOVIE_A, "like")
        fb_repo.log_reaction(CHAT_ID, MOVIE_B, "like")
        time.sleep(0.4)
        likes = fb_repo.get_likes(CHAT_ID)
        liked_ids = {r["movie_id"] for r in likes}
        assert MOVIE_A in liked_ids
        assert MOVIE_B in liked_ids
        assert MOVIE_C not in liked_ids

    def test_get_dislikes_returns_only_dislikes(self, seeded_db):
        _, fb_repo, _ = seeded_db
        time.sleep(0.2)
        dislikes = fb_repo.get_dislikes(CHAT_ID)
        disliked_ids = {r["movie_id"] for r in dislikes}
        assert MOVIE_C in disliked_ids
        assert MOVIE_A not in disliked_ids

    def test_invalid_reaction_type_is_ignored(self, seeded_db):
        _, fb_repo, _ = seeded_db
        movie_x = "tt_integ_invalid_999"
        fb_repo.log_reaction(CHAT_ID, movie_x, "meh")  # type: ignore[arg-type]
        time.sleep(0.2)
        assert fb_repo.get_reaction(CHAT_ID, movie_x) is None


# ---------------------------------------------------------------------------
# 2. HistoryRepository
# ---------------------------------------------------------------------------

class TestHistoryRepositoryIntegration:

    def test_history_rows_exist(self, seeded_db):
        hist_repo, _, _ = seeded_db
        rows = hist_repo.get_history(CHAT_ID, page=1)
        ids = {r["movie_id"] for r in rows}
        for mid in (MOVIE_A, MOVIE_B, MOVIE_C, MOVIE_D):
            assert mid in ids, f"{mid} not found in history"

    def test_get_by_movie_id_returns_correct_row(self, seeded_db):
        hist_repo, _, _ = seeded_db
        row = hist_repo.get_by_movie_id(CHAT_ID, MOVIE_A)
        assert row is not None
        assert row["title"] == "Inception"
        assert row["genres"] == "Sci-Fi,Thriller"

    def test_total_count_matches_seeded_rows(self, seeded_db):
        hist_repo, _, _ = seeded_db
        count = hist_repo.get_total_count(CHAT_ID)
        assert count >= len(HISTORY_ROWS)


# ---------------------------------------------------------------------------
# 3. Taste profile recomputation
# ---------------------------------------------------------------------------

class TestTasteProfileIntegration:

    def test_recompute_taste_profile_updates_preferred_genres(self, seeded_db):
        """After liking Sci-Fi and Action movies, preferred_genres reflects them."""
        hist_repo, fb_repo, _ = seeded_db

        fb_repo.log_reaction(CHAT_ID, MOVIE_A, "like")  # Sci-Fi,Thriller
        fb_repo.log_reaction(CHAT_ID, MOVIE_B, "like")  # Action,Crime,Drama
        time.sleep(0.4)

        user_svc = _make_user_service()
        user_svc.recompute_taste_profile(CHAT_ID)
        time.sleep(0.4)

        # Read back via UserRepository (not raw sb.select_rows)
        user_repo = UserRepository()
        row = user_repo.get_user(CHAT_ID)
        genres_raw = row.get("preferred_genres", "[]")
        genres = json.loads(genres_raw) if isinstance(genres_raw, str) else genres_raw

        assert isinstance(genres, list) and len(genres) > 0, (
            f"preferred_genres must be non-empty after liking, got {genres!r}"
        )
        liked_genres = {"Sci-Fi", "Action", "Thriller", "Crime", "Drama"}
        assert set(genres) & liked_genres, (
            f"{genres} has no overlap with expected liked genres {liked_genres}"
        )

    def test_recompute_is_safe_when_no_likes_exist(self, seeded_db):
        """recompute_taste_profile must not crash when no likes exist."""
        clean_id = "test_integ_empty_likes_u2"
        try:
            sb.insert_rows(
                "users",
                [{"chat_id": clean_id, "username": "EmptyLikesUser",
                  "preferred_genres": json.dumps([]),
                  "disliked_genres":  json.dumps([]),
                  "subscriptions":    json.dumps([])}],
                upsert=True, on_conflict="chat_id",
            )
            user_svc = _make_user_service()
            user_svc.recompute_taste_profile(clean_id)  # must not raise
        finally:
            sb.delete_rows("users", {"chat_id": clean_id})


# ---------------------------------------------------------------------------
# 4. Rating preference  (uses update_min_rating — the real method name)
# ---------------------------------------------------------------------------

class TestRatingPreferenceIntegration:

    def test_set_rating_preference_persists(self, seeded_db):
        """update_min_rating(7.5) writes avg_rating_preference to Supabase."""
        user_svc = _make_user_service()
        user_svc.update_min_rating(CHAT_ID, 7.5)
        time.sleep(0.3)

        user_repo = UserRepository()
        row = user_repo.get_user(CHAT_ID)
        pref = float(row.get("avg_rating_preference") or 0)
        assert pref == 7.5, f"Expected 7.5, got {pref}"

    def test_rating_preference_rejects_out_of_range(self, seeded_db):
        """Values outside [0, 10] must be rejected — no DB overwrite."""
        user_svc = _make_user_service()
        user_svc.update_min_rating(CHAT_ID, 6.0)
        time.sleep(0.3)

        for bad in (-1.0, 11.0, 10.1):
            try:
                user_svc.update_min_rating(CHAT_ID, bad)
            except (ValueError, AssertionError):
                pass  # explicit rejection is fine
        time.sleep(0.3)

        user_repo = UserRepository()
        row = user_repo.get_user(CHAT_ID)
        pref = float(row.get("avg_rating_preference") or 0)
        assert pref == 6.0, (
            f"Invalid rating must not overwrite valid pref, got {pref}"
        )

    def test_rating_boundary_values_accepted(self, seeded_db):
        """0 and 10 are valid boundary values."""
        user_svc = _make_user_service()
        user_repo = UserRepository()

        for boundary in (0.0, 10.0):
            user_svc.update_min_rating(CHAT_ID, boundary)
            time.sleep(0.3)
            row = user_repo.get_user(CHAT_ID)
            # Use explicit None check to avoid falsy 0.0 being treated as missing
            pref = _get_rating_pref(row)
            assert pref == boundary, f"Boundary {boundary} should be accepted, got {pref}"


# ---------------------------------------------------------------------------
# 5. Edge cases — empty feedback table
# ---------------------------------------------------------------------------

class TestFeedbackEdgeCasesIntegration:

    def test_get_likes_empty_returns_empty_list(self):
        """get_likes on a user with zero rows must return []."""
        fb_repo = FeedbackRepository()
        likes = fb_repo.get_likes("test_integ_ghost_user_zzz")
        assert likes == [], f"Expected [], got {likes}"

    def test_get_reaction_unknown_user_returns_none(self):
        """get_reaction for completely unknown user/movie must return None."""
        fb_repo = FeedbackRepository()
        result = fb_repo.get_reaction("test_integ_ghost_user_zzz", "tt_ghost_999")
        assert result is None
