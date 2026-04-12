"""
Unit tests for RecommendationService.

All external I/O is mocked. No real API keys, Redis, or Supabase needed.

Key facts from the source:
- Constructor : RecommendationService(discovery=<DiscoveryService|None>)
- get_recommendations(session: SessionModel, user: UserModel, mode, chat_id, ...)
  takes real model objects, NOT .to_row() dicts.
- session_service is imported INSIDE the function body:
      from services.container import session_service
  Python resolves this at call-time from services.container, so we must patch
  services.container.session_service  (not services.recommendation_service.session_service).
- enrich_movies is a module-level fn from services.enrichment_service;
  patch it at services.recommendation_service.enrich_movies.
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from models.domain import MovieModel, SessionModel, UserModel
from services.recommendation_service import (
    RecommendationService,
    _movie_passes_filters,
    BATCH_SIZE,
)

# ---------------------------------------------------------------------------
# Patch paths  (derived from actual source, not guessed)
# ---------------------------------------------------------------------------
# enrich_movies is imported at module-top in recommendation_service:
#   from services.enrichment_service import enrich_movies
# so it IS a module-level name in recommendation_service.
_ENRICH_PATH = "services.recommendation_service.enrich_movies"

# session_service is imported lazily INSIDE get_recommendations:
#   from services.container import session_service
# Python re-executes that import each call and reads the name from
# services.container, so we patch it there.
_SESSION_SVC_PATH = "services.container.session_service"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(
    last_recs_json: str = "[]",
    overflow_buffer_json: str = "[]",
    answers_rating: str = "",
) -> SessionModel:
    return SessionModel.from_row({
        "chat_id": "123",
        "last_recs_json": last_recs_json,
        "overflow_buffer_json": overflow_buffer_json,
        "answers_rating": answers_rating,
    })


def _make_user(
    preferred_genres: list | None = None,
    disliked_genres: list | None = None,
    avg_rating_preference: float | None = None,
) -> UserModel:
    return UserModel.from_row({
        "chat_id": "123",
        "preferred_genres": preferred_genres or [],
        "disliked_genres": disliked_genres or [],
        "avg_rating_preference": avg_rating_preference,
    })


def _fake_movies(n: int = 8, base_rating: float = 7.5) -> list[MovieModel]:
    return [
        MovieModel(movie_id=f"tt{i:07d}", title=f"Movie {i}", rating=base_rating)
        for i in range(1, n + 1)
    ]


def _make_mock_discovery(return_movies: list[MovieModel] | None = None) -> MagicMock:
    mock_disc = MagicMock()
    mock_disc.discover = AsyncMock(return_value=return_movies or [])
    return mock_disc


def _svc(discover_return: list[MovieModel] | None = None) -> RecommendationService:
    """RecommendationService with discovery mocked. Uses correct kwarg: discovery=."""
    return RecommendationService(discovery=_make_mock_discovery(discover_return))


def _mock_session_svc() -> MagicMock:
    """Minimal mock that satisfies get_session / upsert_session calls."""
    svc = MagicMock()
    svc.get_session.return_value = _make_session()
    svc.upsert_session.return_value = None
    return svc


# ---------------------------------------------------------------------------
# _movie_passes_filters  (module-level pure function — no I/O)
# ---------------------------------------------------------------------------

class TestMoviePassesFilters:
    def test_excludes_id_in_excluded_set(self):
        movie = MovieModel(movie_id="tt123", title="Old")
        assert not _movie_passes_filters(movie, {"tt123"}, None, [])

    def test_passes_when_not_in_excluded_set(self):
        movie = MovieModel(movie_id="tt456", title="New")
        assert _movie_passes_filters(movie, {"tt123"}, None, [])

    def test_excludes_below_min_rating(self):
        movie = MovieModel(movie_id="tt1", title="Low", rating=5.0)
        assert not _movie_passes_filters(movie, set(), 7.0, [])

    def test_passes_equal_to_min_rating(self):
        movie = MovieModel(movie_id="tt2", title="Exact", rating=7.0)
        assert _movie_passes_filters(movie, set(), 7.0, [])

    def test_passes_above_min_rating(self):
        movie = MovieModel(movie_id="tt3", title="High", rating=8.5)
        assert _movie_passes_filters(movie, set(), 7.0, [])

    def test_passes_when_no_rating_and_min_set(self):
        # rating is None => `movie.rating and movie.rating < min` is False -> passes
        movie = MovieModel(movie_id="tt4", title="Unrated")
        assert _movie_passes_filters(movie, set(), 8.0, [])

    def test_excludes_disliked_genre(self):
        movie = MovieModel(movie_id="tt5", title="Scary", rating=9.0, genres="Horror, Thriller")
        assert not _movie_passes_filters(movie, set(), None, ["horror"])

    def test_passes_non_disliked_genre(self):
        movie = MovieModel(movie_id="tt6", title="Fun", rating=7.5, genres="Comedy")
        assert _movie_passes_filters(movie, set(), None, ["horror"])

    def test_passes_when_no_genres_and_disliked_set(self):
        movie = MovieModel(movie_id="tt7", title="NoGenre")
        assert _movie_passes_filters(movie, set(), None, ["horror"])


# ---------------------------------------------------------------------------
# Constructor
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_accepts_no_args(self):
        with patch("services.recommendation_service.DiscoveryService"):
            svc = RecommendationService()
            assert svc is not None

    def test_accepts_discovery_kwarg(self):
        mock_disc = _make_mock_discovery()
        svc = RecommendationService(discovery=mock_disc)
        assert svc._discovery is mock_disc


# ---------------------------------------------------------------------------
# get_recommendations  (async, end-to-end with mocked I/O)
# ---------------------------------------------------------------------------

class TestGetRecommendations:

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        fake = _fake_movies(3)
        svc = _svc(discover_return=fake)

        with patch(_ENRICH_PATH, new=AsyncMock(return_value=fake)), \
             patch(_SESSION_SVC_PATH, _mock_session_svc()):
            result = await svc.get_recommendations(
                _make_session(), _make_user(), mode="trending", chat_id="123"
            )

        assert isinstance(result, list)
        assert all(isinstance(r, dict) for r in result)

    @pytest.mark.asyncio
    async def test_excludes_movies_in_last_recs(self):
        existing = [{"movie_id": "tt0000001", "title": "Old Movie"}]
        session = _make_session(last_recs_json=json.dumps(existing))
        candidates = [
            MovieModel(movie_id="tt0000001", title="Old Movie"),  # must be excluded
            MovieModel(movie_id="tt0000002", title="New Movie"),
        ]
        svc = _svc(discover_return=candidates)

        with patch(_ENRICH_PATH, new=AsyncMock(side_effect=lambda movies: movies)), \
             patch(_SESSION_SVC_PATH, _mock_session_svc()):
            result = await svc.get_recommendations(
                session, _make_user(), mode="trending", chat_id="123"
            )

        ids = [r.get("movie_id") for r in result]
        assert "tt0000001" not in ids
        assert "tt0000002" in ids

    @pytest.mark.asyncio
    async def test_deduplicates_same_title(self):
        dupes = [
            MovieModel(movie_id="tt0000001", title="Same Film"),
            MovieModel(movie_id="tt0000001", title="Same Film"),  # duplicate title
            MovieModel(movie_id="tt0000003", title="Different Film"),
        ]
        svc = _svc(discover_return=dupes)

        with patch(_ENRICH_PATH, new=AsyncMock(side_effect=lambda movies: movies)), \
             patch(_SESSION_SVC_PATH, _mock_session_svc()):
            result = await svc.get_recommendations(
                _make_session(), _make_user(), mode="trending", chat_id="123"
            )

        titles = [r.get("title") for r in result]
        assert titles.count("Same Film") <= 1
        assert "Different Film" in titles

    @pytest.mark.asyncio
    async def test_respects_min_rating_via_session_answer(self):
        candidates = [
            MovieModel(movie_id="tt0000001", title="High Rated", rating=8.5),
            MovieModel(movie_id="tt0000002", title="Low Rated",  rating=6.0),
        ]
        # "8+" resolves to min_rating=8.0 inside _resolve_min_rating
        session = _make_session(answers_rating="8+")
        svc = _svc(discover_return=candidates)

        with patch(_ENRICH_PATH, new=AsyncMock(side_effect=lambda movies: movies)), \
             patch(_SESSION_SVC_PATH, _mock_session_svc()):
            result = await svc.get_recommendations(
                session, _make_user(), mode="trending", chat_id="123"
            )

        titles = [r.get("title") for r in result]
        assert "High Rated" in titles
        assert "Low Rated" not in titles

    @pytest.mark.asyncio
    async def test_returns_at_most_batch_size(self):
        many = _fake_movies(BATCH_SIZE + 4)
        svc = _svc(discover_return=many)

        with patch(_ENRICH_PATH, new=AsyncMock(side_effect=lambda movies: movies[:BATCH_SIZE])), \
             patch(_SESSION_SVC_PATH, _mock_session_svc()):
            result = await svc.get_recommendations(
                _make_session(), _make_user(), mode="trending", chat_id="123"
            )

        assert len(result) <= BATCH_SIZE

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_empty(self):
        svc = _svc(discover_return=[])

        with patch(_ENRICH_PATH, new=AsyncMock(return_value=[])), \
             patch(_SESSION_SVC_PATH, _mock_session_svc()):
            result = await svc.get_recommendations(
                _make_session(), _make_user(), mode="trending", chat_id="123"
            )

        assert result == []


# ---------------------------------------------------------------------------
# _resolve_min_rating  (synchronous method — no I/O)
# ---------------------------------------------------------------------------

class TestResolveMinRating:
    def test_session_answer_8plus(self):
        svc = _svc()
        assert svc._resolve_min_rating(_make_session(answers_rating="8+"), _make_user()) == 8.0

    def test_session_answer_7plus(self):
        svc = _svc()
        assert svc._resolve_min_rating(_make_session(answers_rating="7+"), _make_user()) == 7.0

    def test_session_answer_any_returns_none(self):
        svc = _svc()
        assert svc._resolve_min_rating(_make_session(answers_rating="any"), _make_user()) is None

    def test_falls_back_to_user_preference(self):
        svc = _svc()
        user = _make_user(avg_rating_preference=7.5)
        assert svc._resolve_min_rating(_make_session(answers_rating=""), user) == 7.5

    def test_returns_none_when_no_preference(self):
        svc = _svc()
        assert svc._resolve_min_rating(_make_session(), _make_user()) is None
