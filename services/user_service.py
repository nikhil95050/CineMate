"""UserService — user CRUD + taste-profile recomputation.

This module is the authoritative home for UserService so that it can import
FeedbackRepository and HistoryRepository without creating circular dependencies
with movie_service.py.

The UserService class in movie_service.py is kept as a thin backwards-
compatible shim that delegates to this implementation.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Dict, List, Optional

from models.domain import UserModel

logger = logging.getLogger("user_service")

# Maximum number of liked movies to inspect when deriving the taste profile.
_TASTE_PROFILE_LIMIT = 50
# Top-N genres to store in preferred_genres.
_TOP_GENRES = 5


class UserService:
    """Service for user CRUD and taste-profile learning."""

    def __init__(
        self,
        user_repo: Any | None = None,
        feedback_repo: Any | None = None,
        history_repo: Any | None = None,
    ) -> None:
        self.user_repo = user_repo
        self.feedback_repo = feedback_repo
        self.history_repo = history_repo

    # ------------------------------------------------------------------
    # Basic CRUD
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Taste-profile recomputation
    # ------------------------------------------------------------------

    def recompute_taste_profile(self, chat_id: str) -> None:
        """Derive preferred_genres and user_taste_vector from recent likes.

        Algorithm:
          1. Load the most recent liked movie_ids from FeedbackRepository.
          2. For each liked movie_id, look up the history row to get genres.
          3. Count genre occurrences across all liked movies.
          4. Store the top-N genres in UserModel.preferred_genres.
          5. Write a user_taste_vector JSON blob summarising the profile.

        Safe when feedback table is empty or history entries are missing —
        those cases simply result in no update being made.
        """
        chat_id = str(chat_id)
        if not self.feedback_repo:
            logger.debug("[UserService] no feedback_repo — skipping taste recompute")
            return

        # --- Step 1: recent likes ---
        try:
            liked_rows = self.feedback_repo.get_likes(
                chat_id, limit=_TASTE_PROFILE_LIMIT
            )
        except Exception as exc:
            logger.warning(
                "[UserService] recompute_taste_profile: get_likes failed: %s", exc
            )
            return

        if not liked_rows:
            logger.debug(
                "[UserService] recompute_taste_profile: no likes yet for %s", chat_id
            )
            return

        # --- Step 2 & 3: gather genre counts from history ---
        genre_counter: Counter = Counter()
        resolved_count = 0

        for fb_row in liked_rows:
            movie_id = fb_row.get("movie_id", "")
            if not movie_id:
                continue

            history_row: Optional[Dict[str, Any]] = None
            if self.history_repo:
                try:
                    history_row = self.history_repo.get_by_movie_id(chat_id, movie_id)
                except Exception as exc:
                    logger.debug(
                        "[UserService] history lookup failed for %s: %s",
                        movie_id,
                        exc,
                    )

            if history_row:
                genres_raw: str = history_row.get("genres", "") or ""
                for g in genres_raw.split(","):
                    g = g.strip()
                    if g:
                        genre_counter[g] += 1
                resolved_count += 1

        if not genre_counter:
            logger.debug(
                "[UserService] recompute_taste_profile: no genre data resolved for %s",
                chat_id,
            )
            return

        # --- Step 4 & 5: build and persist the profile ---
        top_genres: List[str] = [g for g, _ in genre_counter.most_common(_TOP_GENRES)]

        taste_vector: Dict[str, Any] = {
            "top_genres": top_genres,
            "genre_counts": dict(genre_counter.most_common(20)),
            "liked_count": len(liked_rows),
            "resolved_count": resolved_count,
        }

        try:
            user = self.get_user(chat_id)
            user.preferred_genres = top_genres
            user.user_taste_vector = taste_vector  # type: ignore[assignment]
            self.upsert_user(user)
            logger.info(
                "[UserService] taste profile updated for %s — top genres: %s",
                chat_id,
                top_genres,
            )
        except Exception as exc:
            logger.warning(
                "[UserService] recompute_taste_profile: upsert failed: %s", exc
            )

    # ------------------------------------------------------------------
    # Rating preference update
    # ------------------------------------------------------------------

    def update_min_rating(self, chat_id: str, rating: float) -> None:
        """Persist avg_rating_preference for the given user.

        Raises ValueError for values outside the valid range [0.0, 10.0].
        The value is written via a targeted patch so that 0.0 (falsy) is
        always persisted correctly without being coerced away.
        """
        # --- Validate range first — reject before any DB access ---
        try:
            rating = float(rating)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"rating must be numeric, got {rating!r}") from exc

        if not (0.0 <= rating <= 10.0):
            raise ValueError(
                f"avg_rating_preference must be in [0, 10], got {rating}"
            )

        if not self.user_repo:
            logger.debug("[UserService] no user_repo — skipping update_min_rating")
            return

        try:
            # Use a direct targeted patch so that 0.0 is never treated as
            # falsy and silently dropped.  We still need a valid username for
            # the upsert; read the current row first (or fall back to 'User').
            existing_row = self.user_repo.get_user(chat_id)
            username = existing_row.get("username") or "User"

            patch: Dict[str, Any] = {
                "chat_id": str(chat_id),
                "avg_rating_preference": rating,
            }
            self.user_repo.upsert_user(str(chat_id), username, patch=patch)
            logger.info(
                "[UserService] avg_rating_preference set to %s for %s",
                rating,
                chat_id,
            )
        except Exception as exc:
            logger.warning(
                "[UserService] update_min_rating failed: %s", exc
            )
