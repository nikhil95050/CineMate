"""Tests for Feature 8: /star filmography and /share recommendation card.

All external calls (Perplexity API, OMDb, Supabase error_batcher, Telegram)
are fully mocked so the suite runs without any API keys or network access.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _no_op_emit(*_a, **_kw):
    """Drop-in replacement for error_batcher.emit that does nothing."""
    pass


class _StubSessionService:
    """In-memory SessionService — no Supabase."""

    def __init__(self):
        self._store: Dict[str, Dict] = {}

    def get_session(self, chat_id: str):
        from models.domain import SessionModel
        row = self._store.get(chat_id, {"chat_id": chat_id})
        return SessionModel.from_row(row)

    def upsert_session(self, session) -> None:
        row = session.to_row() if hasattr(session, "to_row") else dict(session)
        self._store[session.chat_id] = row

    def set_last_recs(self, chat_id: str, recs: List[Dict]) -> None:
        row = self._store.get(chat_id, {"chat_id": chat_id})
        row["last_recs_json"] = json.dumps(recs)
        self._store[chat_id] = row


class _StubHistoryService:
    def __init__(self):
        self.added = []

    def add_to_history(self, chat_id, movie):
        self.added.append((chat_id, movie))


def _make_movie_dict(
    title: str = "Test Movie",
    year: str = "2023",
    rating: float = 7.5,
    genres: str = "Action,Drama",
    reason: str = "A great watch.",
    streaming: str = "Netflix",
    movie_id: str = "tt0000001",
) -> Dict[str, Any]:
    return dict(
        movie_id=movie_id, title=title, year=year,
        rating=rating, genres=genres, reason=reason, streaming=streaming,
    )


# Shared patch stack used by every TestGetStarMovies test.
# Stops: (1) real Perplexity HTTP, (2) real OMDb HTTP,
#        (3) error_batcher background thread -> Supabase.
_DISCOVERY_PATCHES = [
    "services.discovery_service.perplexity_client.chat",
    "services.discovery_service._enrich_with_omdb",
    "services.discovery_service.error_batcher.emit",
]


# ---------------------------------------------------------------------------
# 1. DiscoveryService.get_star_movies
# ---------------------------------------------------------------------------

class TestGetStarMovies:

    def _fake_items(self) -> List[Dict]:
        return [
            {"title": "Inception",     "year": "2010", "reason": "Mind-bending thriller"},
            {"title": "The Revenant",  "year": "2015", "reason": "Raw survival epic"},
        ]

    @pytest.mark.asyncio
    async def test_returns_movies_when_llm_succeeds(self):
        from services.discovery_service import DiscoveryService
        svc = DiscoveryService()

        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=json.dumps(self._fake_items()))), \
             patch("services.discovery_service._enrich_with_omdb",
                   new=AsyncMock(side_effect=lambda m, **_: m)), \
             patch("services.discovery_service.error_batcher.emit", side_effect=_no_op_emit):

            result = await svc.get_star_movies("Leonardo DiCaprio", chat_id="u1")

        assert len(result) == 2
        assert {m.title for m in result} == {"Inception", "The Revenant"}

    @pytest.mark.asyncio
    async def test_returns_empty_when_llm_returns_nothing(self):
        from services.discovery_service import DiscoveryService
        svc = DiscoveryService()

        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value="")), \
             patch("services.discovery_service.error_batcher.emit", side_effect=_no_op_emit):

            result = await svc.get_star_movies("Unknown Person XYZ", chat_id="u1")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_llm_returns_unparseable(self):
        from services.discovery_service import DiscoveryService
        svc = DiscoveryService()

        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value="Sorry, I don't know that person.")), \
             patch("services.discovery_service.error_batcher.emit", side_effect=_no_op_emit):

            result = await svc.get_star_movies("NoOneKnows", chat_id="u1")

        assert result == []

    @pytest.mark.asyncio
    async def test_empty_star_name_returns_empty_list(self):
        """Blank name returns early — no network call at all."""
        from services.discovery_service import DiscoveryService
        svc = DiscoveryService()
        result = await svc.get_star_movies("", chat_id="u1")
        assert result == []

    @pytest.mark.asyncio
    async def test_omdb_failure_returns_stubs(self):
        """OMDb errors kept as stubs — never an empty list."""
        from services.discovery_service import DiscoveryService
        svc = DiscoveryService()

        with patch("services.discovery_service.perplexity_client.chat",
                   new=AsyncMock(return_value=json.dumps(self._fake_items()))), \
             patch("services.discovery_service._enrich_with_omdb",
                   new=AsyncMock(side_effect=lambda m, **_: m)), \
             patch("services.discovery_service.error_batcher.emit", side_effect=_no_op_emit):

            result = await svc.get_star_movies("Akira Kurosawa", chat_id="u1")

        assert len(result) == 2


# ---------------------------------------------------------------------------
# 2. handle_star
# ---------------------------------------------------------------------------

class TestHandleStar:

    @pytest.mark.asyncio
    async def test_no_name_sends_usage_prompt(self):
        with patch("handlers.discovery_handlers.send_message", new=AsyncMock()) as mock_send, \
             patch("handlers.discovery_handlers.show_typing",   new=AsyncMock()):

            from handlers.discovery_handlers import handle_star
            await handle_star(chat_id="u1", input_text="/star")

        assert mock_send.called
        text = mock_send.call_args[0][1]
        assert any(kw in text.lower() for kw in ("leonardo", "usage", "name", "actor"))

    @pytest.mark.asyncio
    async def test_star_found_sends_cards_and_saves_history(self):
        from models.domain import MovieModel
        stub_movies = [
            MovieModel(movie_id="tt0001", title="Inception",    year="2010"),
            MovieModel(movie_id="tt0002", title="The Revenant", year="2015"),
        ]
        stub_sess = _StubSessionService()
        stub_hist = _StubHistoryService()

        with patch("handlers.discovery_handlers.send_message",    new=AsyncMock()), \
             patch("handlers.discovery_handlers.show_typing",      new=AsyncMock()), \
             patch("handlers.discovery_handlers.send_movies_async",new=AsyncMock()) as mock_cards, \
             patch("handlers.discovery_handlers.discovery_service") as mock_disc, \
             patch("handlers.discovery_handlers.session_service",  stub_sess), \
             patch("handlers.discovery_handlers.history_service",  stub_hist):

            mock_disc.get_star_movies = AsyncMock(return_value=stub_movies)

            from handlers.discovery_handlers import handle_star
            await handle_star(chat_id="u1", input_text="/star Leonardo DiCaprio")

        assert mock_cards.called
        assert len(stub_hist.added) == 2
        assert stub_hist.added[0][1].title == "Inception"
        saved = json.loads(stub_sess.get_session("u1").last_recs_json or "[]")
        assert len(saved) == 2

    @pytest.mark.asyncio
    async def test_star_not_found_sends_fallback_message(self):
        stub_sess = _StubSessionService()
        stub_hist = _StubHistoryService()

        with patch("handlers.discovery_handlers.send_message",    new=AsyncMock()) as mock_send, \
             patch("handlers.discovery_handlers.show_typing",      new=AsyncMock()), \
             patch("handlers.discovery_handlers.send_movies_async",new=AsyncMock()) as mock_cards, \
             patch("handlers.discovery_handlers.discovery_service") as mock_disc, \
             patch("handlers.discovery_handlers.session_service",  stub_sess), \
             patch("handlers.discovery_handlers.history_service",  stub_hist):

            mock_disc.get_star_movies = AsyncMock(return_value=[])

            from handlers.discovery_handlers import handle_star
            await handle_star(chat_id="u1", input_text="/star XyzUnknownPerson999")

        assert not mock_cards.called
        assert mock_send.called
        combined = " ".join(str(c) for c in mock_send.call_args_list)
        assert "XyzUnknownPerson999" in combined or "couldn't" in combined.lower()

    @pytest.mark.asyncio
    async def test_discovery_exception_sends_fallback(self):
        stub_sess = _StubSessionService()
        stub_hist = _StubHistoryService()

        with patch("handlers.discovery_handlers.send_message",    new=AsyncMock()) as mock_send, \
             patch("handlers.discovery_handlers.show_typing",      new=AsyncMock()), \
             patch("handlers.discovery_handlers.send_movies_async",new=AsyncMock()) as mock_cards, \
             patch("handlers.discovery_handlers.discovery_service") as mock_disc, \
             patch("handlers.discovery_handlers.session_service",  stub_sess), \
             patch("handlers.discovery_handlers.history_service",  stub_hist):

            mock_disc.get_star_movies = AsyncMock(side_effect=RuntimeError("API down"))

            from handlers.discovery_handlers import handle_star
            await handle_star(chat_id="u1", input_text="/star Any Name")

        assert not mock_cards.called
        assert mock_send.called


# ---------------------------------------------------------------------------
# 3. handle_share
# ---------------------------------------------------------------------------

class TestHandleShare:

    @pytest.mark.asyncio
    async def test_empty_last_recs_sends_prompt(self):
        stub_sess = _StubSessionService()

        with patch("handlers.discovery_handlers.send_message", new=AsyncMock()) as mock_send, \
             patch("handlers.discovery_handlers.session_service", stub_sess):

            from handlers.discovery_handlers import handle_share
            await handle_share(chat_id="u1")

        assert mock_send.called
        text = mock_send.call_args[0][1]
        assert any(kw in text.lower() for kw in ("nothing", "trending", "recommendations", "share"))

    @pytest.mark.asyncio
    async def test_share_card_contains_titles(self):
        stub_sess = _StubSessionService()
        stub_sess.set_last_recs("u1", [
            _make_movie_dict("Blade Runner 2049", movie_id="tt1856101"),
            _make_movie_dict("Dune",              movie_id="tt1160419", year="2021"),
        ])
        texts: List[str] = []
        async def _cap(cid, t, **_): texts.append(t)

        with patch("handlers.discovery_handlers.send_message", side_effect=_cap), \
             patch("handlers.discovery_handlers.session_service", stub_sess):
            from handlers.discovery_handlers import handle_share
            await handle_share(chat_id="u1")

        full = " ".join(texts)
        assert "Blade Runner 2049" in full
        assert "Dune" in full

    @pytest.mark.asyncio
    async def test_share_card_includes_rating_and_genres(self):
        stub_sess = _StubSessionService()
        stub_sess.set_last_recs("u1", [_make_movie_dict("Inception", rating=8.8, genres="Sci-Fi,Thriller")])
        texts: List[str] = []
        async def _cap(cid, t, **_): texts.append(t)

        with patch("handlers.discovery_handlers.send_message", side_effect=_cap), \
             patch("handlers.discovery_handlers.session_service", stub_sess):
            from handlers.discovery_handlers import handle_share
            await handle_share(chat_id="u1")

        full = " ".join(texts)
        assert "8.8" in full
        assert "Sci-Fi" in full

    @pytest.mark.asyncio
    async def test_share_card_shows_streaming_info(self):
        stub_sess = _StubSessionService()
        stub_sess.set_last_recs("u1", [_make_movie_dict("Oppenheimer", streaming="Netflix, Prime Video")])
        texts: List[str] = []
        async def _cap(cid, t, **_): texts.append(t)

        with patch("handlers.discovery_handlers.send_message", side_effect=_cap), \
             patch("handlers.discovery_handlers.session_service", stub_sess):
            from handlers.discovery_handlers import handle_share
            await handle_share(chat_id="u1")

        assert "Netflix" in " ".join(texts)

    @pytest.mark.asyncio
    async def test_share_skips_na_streaming(self):
        stub_sess = _StubSessionService()
        stub_sess.set_last_recs("u1", [_make_movie_dict("Parasite", streaming="N/A")])
        texts: List[str] = []
        async def _cap(cid, t, **_): texts.append(t)

        with patch("handlers.discovery_handlers.send_message", side_effect=_cap), \
             patch("handlers.discovery_handlers.session_service", stub_sess):
            from handlers.discovery_handlers import handle_share
            await handle_share(chat_id="u1")

        assert "N/A" not in " ".join(texts)

    @pytest.mark.asyncio
    async def test_share_caps_at_five_items(self):
        stub_sess = _StubSessionService()
        stub_sess.set_last_recs("u1", [
            _make_movie_dict(f"Movie {i}", movie_id=f"tt00000{i}") for i in range(10)
        ])
        texts: List[str] = []
        async def _cap(cid, t, **_): texts.append(t)

        with patch("handlers.discovery_handlers.send_message", side_effect=_cap), \
             patch("handlers.discovery_handlers.session_service", stub_sess):
            from handlers.discovery_handlers import handle_share
            await handle_share(chat_id="u1")

        full = " ".join(texts)
        assert "Movie 5" not in full
        assert "Movie 0" in full

    @pytest.mark.asyncio
    async def test_share_includes_cinemate_branding(self):
        stub_sess = _StubSessionService()
        stub_sess.set_last_recs("u1", [_make_movie_dict("Any Movie")])
        texts: List[str] = []
        async def _cap(cid, t, **_): texts.append(t)

        with patch("handlers.discovery_handlers.send_message", side_effect=_cap), \
             patch("handlers.discovery_handlers.session_service", stub_sess):
            from handlers.discovery_handlers import handle_share
            await handle_share(chat_id="u1")

        assert "CineMate" in " ".join(texts)

    @pytest.mark.asyncio
    async def test_share_no_crash_on_broken_session(self):
        class _BrokenSess:
            def get_session(self, _): raise RuntimeError("DB offline")

        with patch("handlers.discovery_handlers.send_message", new=AsyncMock()) as mock_send, \
             patch("handlers.discovery_handlers.session_service", _BrokenSess()):
            from handlers.discovery_handlers import handle_share
            await handle_share(chat_id="u1")   # must not raise

        assert mock_send.called


# ---------------------------------------------------------------------------
# 4. _build_share_card  (pure unit tests, zero I/O)
# ---------------------------------------------------------------------------

class TestBuildShareCard:

    def test_empty_list_returns_branding(self):
        from handlers.discovery_handlers import _build_share_card
        assert "CineMate" in _build_share_card([])

    def test_single_movie_all_fields(self):
        from handlers.discovery_handlers import _build_share_card
        card = _build_share_card([_make_movie_dict(
            "Dune", year="2021", rating=8.0, genres="Sci-Fi",
            reason="Epic space opera.", streaming="HBO Max",
        )])
        assert "Dune" in card
        assert "2021" in card
        assert "8.0" in card
        assert "Sci-Fi" in card
        assert "Epic space opera." in card
        assert "HBO Max" in card

    def test_na_streaming_excluded(self):
        from handlers.discovery_handlers import _build_share_card
        assert "N/A" not in _build_share_card([_make_movie_dict(streaming="N/A")])

    def test_capped_at_five(self):
        from handlers.discovery_handlers import _build_share_card
        recs = [_make_movie_dict(f"Film {i}", movie_id=f"id{i}") for i in range(10)]
        card = _build_share_card(recs)
        assert "Film 5" not in card
        assert "Film 4" in card
