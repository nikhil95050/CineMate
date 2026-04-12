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

logger = logging.getLogger("movie_service")

PAGE_SIZE = 10


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_history_list(
    rows: List[Dict[str, Any]],
    page: int,
    total_pages: int,
) -> str:
    """Return an HTML-formatted string for a page of history rows."""
    if not rows:
        return (
            "\U0001f5c2 <b>Your History</b>\n\n"
            "No recommendations yet. Send /start to discover your first movie!"
        )
    offset = (page - 1) * PAGE_SIZE
    lines = [f"\U0001f5c2 <b>Recommendation History</b> \u2014 Page {page}/{total_pages}\n"]
    for i, row in enumerate(rows, start=offset + 1):
        title = row.get("title") or "Unknown"
        year = row.get("year") or ""
        rating = row.get("rating") or ""
        watched = row.get("watched", False)
        entry = f"{i}. <b>{title}</b>"
        if year:
            entry += f" ({year})"
        if rating:
            entry += f" \u2b50 {rating}"
        if watched:
            entry += " \u2714\ufe0f"
        lines.append(entry)
    return "\n".join(lines)


def format_watchlist_list(
    rows: List[Dict[str, Any]],
    page: int,
    total_pages: int,
) -> str:
    """Return an HTML-formatted string for a page of watchlist rows."""
    if not rows:
        return (
            "\U0001f4c2 <b>Your Watchlist</b>\n\n"
            "Nothing saved yet. Tap <b>Save to Watchlist</b> on any recommendation!"
        )
    offset = (page - 1) * PAGE_SIZE
    lines = [f"\U0001f4c2 <b>Watchlist</b> \u2014 Page {page}/{total_pages}\n"]
    for i, row in enumerate(rows, start=offset + 1):
        title = row.get("title") or "Unknown"
        year = row.get("year") or ""
        rating = row.get("rating") or ""
        entry = f"{i}. <b>{title}</b>"
        if year:
            entry += f" ({year})"
        if rating:
            entry += f" \u2b50 {rating}"
        lines.append(entry)
    return "\n".join(lines)


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
        """Return raw history dicts for formatting (pagination layer)."""
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
            return max(1, -(-total // PAGE_SIZE))  # ceiling division
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
        """Return a MovieModel for the given movie_id, or None if not found.

        Previously returned a raw dict; now consistently returns a typed
        MovieModel so callers always work with the domain model, not dicts.
        Callers that need a dict can call .model_dump().
        """
        if not self.history_repo:
            return None
        try:
            row = self.history_repo.get_by_movie_id(chat_id, movie_id)
            if row is None:
                return None
            return MovieModel.from_history_row(row)
        except Exception as exc:
            logger.warning(
                "[MovieService] get_movie_from_history failed: %s", exc
            )
            return None

    # ------------------------------------------------------------------
    # Watchlist
    # ------------------------------------------------------------------

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
        """Return raw watchlist dicts for formatting (pagination layer)."""
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
        """Return a random unwatched watchlist item as a MovieModel, or None.

        Previously returned a raw dict; now consistently returns a typed
        MovieModel so callers always work with the domain model.
        """
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

    async def get_watchlist(
        self, chat_id: str, page: int = 1
    ) -> List[Dict[str, Any]]:
        return self._svc.get_watchlist(chat_id, page=page)

    async def add(
        self, chat_id: str, movie: MovieModel
    ) -> None:
        """Accept a MovieModel directly — consistent with the service layer.

        Previously accepted a raw dict and called MovieModel.from_history_row()
        internally, which masked type errors. Callers must now pass a MovieModel.
        """
        self._svc.add_to_watchlist(chat_id, movie)


class HistoryService:
    """Async shim — delegates to MovieService."""

    def __init__(self, history_repo: Any | None = None) -> None:
        self._svc = MovieService(history_repo=history_repo)

    async def get_history(
        self, chat_id: str, page: int = 1
    ) -> List[Dict[str, Any]]:
        return self._svc.get_history(chat_id, page=page)

    async def add(
        self, chat_id: str, movies: List[MovieModel]
    ) -> None:
        """Accept a list of MovieModel objects — consistent with the service layer.

        Previously accepted raw dicts and converted internally. Callers must
        now pass typed MovieModel instances.
        """
        self._svc.add_to_history(chat_id, movies)


# ---------------------------------------------------------------------------
# UserService / SessionService
# ---------------------------------------------------------------------------

class UserService:
    def __init__(self, user_repo: Any | None = None) -> None:
        self.user_repo = user_repo

    def get_user(self, chat_id: str) -> UserModel:
        if not self.user_repo:
            return UserModel(chat_id=str(chat_id))
        row = self.user_repo.get_user(chat_id)
        return UserModel.from_row(row)

    def upsert_user(self, user: UserModel) -> None:
        if not self.user_repo:
            return
        self.user_repo.upsert_user(
            user.chat_id, user.username, patch=user.to_row()
        )


class SessionService:
    def __init__(self, session_repo: Any | None = None) -> None:
        self.session_repo = session_repo

    def get_session(self, chat_id: str) -> SessionModel:
        if not self.session_repo:
            return SessionModel(chat_id=str(chat_id))
        row = self.session_repo.get_session(chat_id)
        return SessionModel.from_row(row)

    def upsert_session(self, session: SessionModel) -> None:
        if not self.session_repo:
            return
        self.session_repo.upsert_session(session.chat_id, session.to_row())

    def reset_session(self, chat_id: str) -> SessionModel:
        session = SessionModel(chat_id=str(chat_id))
        self.upsert_session(session)
        return session
