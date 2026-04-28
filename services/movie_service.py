"""MovieService — history & watchlist business logic.

All public methods are synchronous to match the existing SessionService /
UserService patterns. Async wrappers (HistoryService, WatchlistService) are
kept for backwards compatibility with any existing callers.
"""
from __future__ import annotations

import random
import logging
from typing import Any, Dict, List, Optional

from models.domain import MovieModel, UserModel, SessionModel

from utils.formatters import format_history_list, format_watchlist_list  # noqa: F401

logger = logging.getLogger("movie_service")

PAGE_SIZE = 10


# ---------------------------------------------------------------------------
# MovieService
# ---------------------------------------------------------------------------

class MovieService:
    """Thin service layer over HistoryRepository and WatchlistRepository."""

    def __init__(
        self,
        history_repo: Any | None = None,
        watchlist_repo: Any | None = None,
    ) -> None:
        self.history_repo = history_repo
        self.watchlist_repo = watchlist_repo

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def add_to_history(
        self, chat_id: str, movies: List[MovieModel]
    ) -> None:
        if not self.history_repo:
            return
        rows = [m.to_history_row(chat_id) for m in movies]
        try:
            self.history_repo.log_recommendations(chat_id, rows)
        except Exception as exc:
            logger.warning("[MovieService] add_to_history failed: %s", exc)

    def get_history(
        self, chat_id: str, page: int = 1
    ) -> List[Dict[str, Any]]:
        if not self.history_repo:
            return []
        try:
            return self.history_repo.get_history(chat_id, page=page) or []
        except Exception as exc:
            logger.warning("[MovieService] get_history failed: %s", exc)
            return []

    def get_history_page_count(self, chat_id: str) -> int:
        if not self.history_repo:
            return 1
        try:
            total = self.history_repo.get_total_count(chat_id)
            return max(1, -(-total // PAGE_SIZE))
        except Exception:
            return 1

    def mark_watched(
        self, chat_id: str, movie_id: str
    ) -> bool:
        if not self.history_repo:
            return False
        try:
            return self.history_repo.mark_watched(chat_id, movie_id)
        except Exception as exc:
            logger.warning("[MovieService] mark_watched failed: %s", exc)
            return False

    def get_movie_from_history(
        self, chat_id: str, movie_id: str
    ) -> Optional[MovieModel]:
        if not self.history_repo:
            return None
        try:
            row = self.history_repo.get_by_movie_id(chat_id, movie_id)
            return MovieModel.from_history_row(row) if row else None
        except Exception as exc:
            logger.warning(
                "[MovieService] get_movie_from_history failed: %s", exc
            )
            return None

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

    def is_in_watchlist(self, chat_id: str, movie_id: str) -> bool:
        if not self.watchlist_repo:
            return False
        try:
            return bool(self.watchlist_repo.is_in_watchlist(chat_id, movie_id))
        except Exception as exc:
            logger.warning("[MovieService] is_in_watchlist failed: %s", exc)
            return False

    def add_to_watchlist(
        self, chat_id: str, movie: MovieModel
    ) -> bool:
        if not self.watchlist_repo:
            return False
        row = movie.to_watchlist_row(chat_id)
        try:
            return self.watchlist_repo.add_to_watchlist(chat_id, row)
        except Exception as exc:
            logger.warning("[MovieService] add_to_watchlist failed: %s", exc)
            return False

    def get_watchlist(
        self, chat_id: str, page: int = 1
    ) -> List[Dict[str, Any]]:
        if not self.watchlist_repo:
            return []
        try:
            return self.watchlist_repo.get_watchlist(chat_id, page=page) or []
        except Exception as exc:
            logger.warning("[MovieService] get_watchlist failed: %s", exc)
            return []

    def get_watchlist_page_count(self, chat_id: str) -> int:
        if not self.watchlist_repo:
            return 1
        try:
            total = self.watchlist_repo.get_total_count(chat_id)
            return max(1, -(-total // PAGE_SIZE))
        except Exception:
            return 1

    def get_random_watchlist_reminder(
        self, chat_id: str
    ) -> Optional[MovieModel]:
        if not self.watchlist_repo:
            return None
        try:
            rows = self.watchlist_repo.get_watchlist(chat_id, page=1) or []
            if not rows:
                return None
            row = random.choice(rows)
            return MovieModel.from_history_row(row)
        except Exception as exc:
            logger.warning(
                "[MovieService] get_random_watchlist_reminder failed: %s", exc
            )
            return None


# ---------------------------------------------------------------------------
# Backwards-compatible async shims (used by existing handler stubs)
# ---------------------------------------------------------------------------

class WatchlistService:
    """Async shim — delegates to MovieService."""

    def __init__(self, watchlist_repo: Any | None = None) -> None:
        self._svc = MovieService(watchlist_repo=watchlist_repo)

    def add_to_watchlist(self, chat_id: str, movie: MovieModel) -> bool:
        return self._svc.add_to_watchlist(chat_id, movie)

    def get_watchlist(self, chat_id: str, page: int = 1) -> List[Dict[str, Any]]:
        return self._svc.get_watchlist(chat_id, page=page)

    async def add(self, chat_id: str, movie: MovieModel) -> None:
        self._svc.add_to_watchlist(chat_id, movie)


class HistoryService:
    """Async shim — delegates to MovieService."""

    def __init__(self, history_repo: Any | None = None) -> None:
        self._svc = MovieService(history_repo=history_repo)

    def add_to_history(self, chat_id: str, movie: MovieModel) -> None:
        self._svc.add_to_history(chat_id, [movie])

    def get_history(self, chat_id: str, page: int = 1) -> List[Dict[str, Any]]:
        return self._svc.get_history(chat_id, page=page)

    async def add(self, chat_id: str, movies: List[MovieModel]) -> None:
        self._svc.add_to_history(chat_id, movies)


# ---------------------------------------------------------------------------
# ISSUE 6 FIX: removed the duplicate stripped UserService and SessionService
# stubs that were previously defined here.  Any code that accidentally
# imported UserService or SessionService from this module would have silently
# received versions missing recompute_taste_profile, update_min_rating,
# reset_session, and all feedback/history dependencies.
#
# The canonical implementations live in:
#   services/user_service.py    -> UserService
#   services/session_service.py -> SessionService
#
# container.py already imports from those correct modules.
# ---------------------------------------------------------------------------
