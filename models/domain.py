from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field, field_validator
from utils.time_utils import utc_now_iso


def _parse_jsonb_list(v: Any) -> list:
    """Coerce a Supabase JSONB column that may arrive as a JSON string into a list."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        stripped = v.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, ValueError):
                pass
        # Fallback: treat as comma-separated plain string (legacy)
        return [g.strip() for g in stripped.split(",") if g.strip()]
    return list(v)


def _parse_jsonb_dict(v: Any) -> Optional[dict]:
    """Coerce a Supabase JSONB column that may arrive as a JSON string into a dict."""
    if v is None:
        return None
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        stripped = v.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else None
        except (json.JSONDecodeError, ValueError):
            return None
    return None


class StreamingInfo(BaseModel):
    """Structured streaming availability for a movie.

    Each entry describes a single platform on which the movie is available.
    The ``display`` field is the human-readable summary previously stored in
    ``MovieModel.streaming`` and is preserved for backwards compatibility.
    """

    display: str = Field(
        default="",
        description="Human-readable summary, e.g. 'Netflix, Prime Video'",
    )
    platforms: List[str] = Field(
        default_factory=list,
        description="Ordered list of platform names where the movie is available",
    )
    rent: List[str] = Field(
        default_factory=list,
        description="Platforms where the movie can be rented",
    )
    buy: List[str] = Field(
        default_factory=list,
        description="Platforms where the movie can be purchased",
    )

    @classmethod
    def from_display_string(cls, raw: str | None) -> "StreamingInfo":
        """Build a StreamingInfo from a legacy plain-text streaming string.

        Parses comma/semicolon-separated platform names into ``platforms``.
        Always safe to call — returns an empty StreamingInfo when *raw* is
        None or blank.
        """
        if not raw or not raw.strip():
            return cls()
        display = raw.strip()
        # Split on commas or semicolons, strip whitespace, drop empties
        parts = [
            p.strip()
            for p in display.replace(";", ",").split(",")
            if p.strip() and p.strip().upper() not in ("N/A", "NONE", "UNAVAILABLE")
        ]
        return cls(display=display, platforms=parts)

    @property
    def is_available(self) -> bool:
        """True when at least one streaming/rent/buy platform is known."""
        return bool(self.platforms or self.rent or self.buy)

    def to_display(self) -> str:
        """Return human-readable summary, falling back to platform list."""
        if self.display:
            return self.display
        all_platforms = list(dict.fromkeys(self.platforms + self.rent + self.buy))
        return ", ".join(all_platforms) if all_platforms else "Not available"

    @field_validator("platforms", "rent", "buy", mode="before")
    @classmethod
    def _coerce_list(cls, v: Any) -> list:
        return _parse_jsonb_list(v)


class MovieModel(BaseModel):
    """Normalized movie entity used inside the bot.

    This model is designed to remain compatible with the JSON/dict shapes used
    by the original Antigravity-main project (history, watchlist, OMDb/LLM
    payloads). It is intentionally permissive and provides helpers to convert to
    and from those shapes.
    """

    movie_id: str = Field(..., description="Stable identifier, usually IMDb ID")
    title: str = Field(..., description="Movie title")
    year: Optional[str] = Field(None, description="Release year as string")
    rating: Optional[float] = Field(None, description="IMDb rating as float")

    # Stored as a comma-separated string in Supabase; we keep string here for
    # compatibility but provide helpers to work with lists.
    genres: Optional[str] = Field(None, description="Comma-separated genres")
    language: Optional[str] = Field("English", description="Primary language")

    description: Optional[str] = Field(None, description="Short plot/overview")
    poster: Optional[str] = Field(None, description="Poster URL")
    trailer: Optional[str] = Field(None, description="Trailer URL or search link")

    # --- Streaming availability ---
    # ``streaming`` keeps the legacy plain-string for backwards compatibility
    # with DB columns, formatter code, and existing history rows.
    # ``streaming_info`` is the new structured representation that allows
    # per-platform access, rent/buy distinction, and availability checks.
    # They are kept in sync: whenever ``streaming`` is set via from_history_row
    # or model construction, ``streaming_info`` is derived automatically.
    streaming: Optional[str] = Field(
        None, description="Human-readable streaming availability summary (legacy)"
    )
    streaming_info: StreamingInfo = Field(
        default_factory=StreamingInfo,
        description="Structured streaming availability with per-platform detail",
    )

    reason: Optional[str] = Field(
        None, description="Why this movie was recommended (LLM explanation)"
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("streaming_info", mode="before")
    @classmethod
    def _coerce_streaming_info(cls, v: Any) -> Any:
        """Accept a raw dict, a StreamingInfo instance, or None."""
        if v is None:
            return StreamingInfo()
        if isinstance(v, StreamingInfo):
            return v
        if isinstance(v, dict):
            return StreamingInfo(**v)
        # Fallback: treat as a display string
        if isinstance(v, str):
            return StreamingInfo.from_display_string(v)
        return StreamingInfo()

    def model_post_init(self, __context: Any) -> None:  # type: ignore[override]
        """Sync streaming_info.display with streaming when streaming_info was
        not explicitly supplied (i.e. the caller only set streaming)."""
        if self.streaming and not self.streaming_info.display:
            # Derive structured info from the legacy string
            object.__setattr__(
                self,
                "streaming_info",
                StreamingInfo.from_display_string(self.streaming),
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def genre_list(self) -> List[str]:
        if not self.genres:
            return []
        return [g.strip() for g in self.genres.split(",") if g.strip()]

    # ------------------------------------------------------------------
    # Conversion helpers
    # ------------------------------------------------------------------

    def to_history_row(self, chat_id: str) -> Dict[str, Any]:
        """Shape compatible with HistoryRepository._map_to_supabase.

        Fields not managed by this model (recommended_at, watched, watched_at)
        are left for the repository/service layer to populate.
        """
        return {
            "chat_id": str(chat_id),
            "movie_id": self.movie_id,
            "title": self.title,
            "year": self.year or "",
            "genres": self.genres or ", ".join(self.genre_list),
            "language": self.language or "",
            "rating": str(self.rating) if self.rating is not None else "",
        }

    @classmethod
    def from_history_row(cls, row: Dict[str, Any]) -> "MovieModel":
        rating_raw = row.get("rating")
        try:
            rating = float(rating_raw) if rating_raw not in (None, "") else None
        except ValueError:
            rating = None

        streaming_raw: str | None = row.get("streaming") or None
        streaming_info = StreamingInfo.from_display_string(streaming_raw)

        return cls(
            movie_id=str(row.get("movie_id", "")),
            title=row.get("title", ""),
            year=str(row.get("year", "")) or None,
            rating=rating,
            genres=row.get("genres") or None,
            language=row.get("language") or None,
            description=row.get("description") or None,
            poster=row.get("poster") or None,
            trailer=row.get("trailer") or None,
            streaming=streaming_raw,
            streaming_info=streaming_info,
        )

    def to_watchlist_row(self, chat_id: str) -> Dict[str, Any]:
        """Shape compatible with WatchlistRepository._map_to_supabase."""
        return {
            "chat_id": str(chat_id),
            "movie_id": self.movie_id,
            "title": self.title,
            "year": self.year or "",
            "language": self.language or "",
            "rating": str(self.rating) if self.rating is not None else "",
            "genres": self.genres or ", ".join(self.genre_list),
        }


class UserModel(BaseModel):
    """User profile used throughout the bot.

    This corresponds to the `users` table schema and UserRepository expectations
    in the original project.
    """

    chat_id: str
    username: Optional[str] = "User"

    preferred_genres: List[str] = Field(default_factory=list)
    disliked_genres: List[str] = Field(default_factory=list)

    preferred_language: Optional[str] = None
    preferred_era: Optional[str] = None
    watch_context: Optional[str] = None

    avg_rating_preference: Optional[float] = None
    subscriptions: List[str] = Field(default_factory=list)

    user_taste_vector: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Field validators — coerce Supabase JSONB columns that arrive as
    # raw JSON strings (e.g. '[]', '["Action"]', '{"genres": [...]}')
    # into proper Python types before Pydantic validates them.
    # ------------------------------------------------------------------

    @field_validator("preferred_genres", "disliked_genres", "subscriptions", mode="before")
    @classmethod
    def _ensure_list(cls, v: Any) -> list:  # type: ignore[override]
        return _parse_jsonb_list(v)

    @field_validator("user_taste_vector", mode="before")
    @classmethod
    def _ensure_dict(cls, v: Any) -> Optional[dict]:  # type: ignore[override]
        return _parse_jsonb_dict(v)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "UserModel":
        rating_raw = row.get("avg_rating_preference")
        try:
            rating = float(rating_raw) if rating_raw not in (None, "") else None
        except (ValueError, TypeError):
            rating = None

        return cls(
            chat_id=str(row.get("chat_id", "")),
            username=row.get("username") or "User",
            # Pass raw value — _ensure_list validator handles str/list/None
            preferred_genres=row.get("preferred_genres") or [],
            disliked_genres=row.get("disliked_genres") or [],
            preferred_language=row.get("preferred_language") or None,
            preferred_era=row.get("preferred_era") or None,
            watch_context=row.get("watch_context") or None,
            avg_rating_preference=rating,
            subscriptions=row.get("subscriptions") or [],
            user_taste_vector=row.get("user_taste_vector") or None,
        )

    def to_row(self) -> Dict[str, Any]:
        """Serialise the model to a dict suitable for Supabase REST upsert.

        Fix #10 changes:
        - updated_at is now included so every write stamps the row timestamp.
        - JSONB list columns (preferred_genres, disliked_genres, subscriptions)
          are serialised to JSON strings so Supabase REST accepts them.
        - user_taste_vector (JSONB dict) is serialised to a JSON string when
          present; None is sent as None so the column is explicitly written
          rather than silently omitted.
        """
        return {
            "chat_id": self.chat_id,
            "username": self.username,
            # JSONB list columns — must be JSON strings for Supabase REST
            "preferred_genres": json.dumps(self.preferred_genres),
            "disliked_genres": json.dumps(self.disliked_genres),
            "preferred_language": self.preferred_language,
            "preferred_era": self.preferred_era,
            "watch_context": self.watch_context,
            "avg_rating_preference": self.avg_rating_preference,
            # JSONB list column
            "subscriptions": json.dumps(self.subscriptions),
            # JSONB dict column — serialise to string or send None explicitly
            "user_taste_vector": (
                json.dumps(self.user_taste_vector)
                if self.user_taste_vector is not None
                else None
            ),
            # Fix #10: stamp updated_at on every write
            "updated_at": utc_now_iso(),
        }


# ---------------------------------------------------------------------------
# BUG #7 FIX — Explicit question-index → answers_* column mapping
# ---------------------------------------------------------------------------
# This tuple is the single source of truth for the questionnaire flow.
# Index N in this tuple corresponds to question_index N in the sessions table.
# Any handler that stores an answer must use:
#
#   col = QUESTION_COLUMNS[session.question_index]
#   setattr(session, col, user_answer)
#
# rather than an ad-hoc integer offset or a hard-coded column name.
#
# _validate_question_columns() runs at import time and raises AssertionError
# when the tuple length drifts from SessionModel._TOTAL_QUESTIONS so that
# off-by-one bugs are caught immediately at startup, before any user
# interaction occurs.

QUESTION_COLUMNS: Tuple[str, ...] = (
    "answers_mood",       # Q0 — What mood are you in?
    "answers_genre",      # Q1 — Which genres?
    "answers_language",   # Q2 — Language preference?
    "answers_era",        # Q3 — Era / decade?
    "answers_context",    # Q4 — Who are you watching with?
    "answers_time",       # Q5 — How much time do you have?
    "answers_avoid",      # Q6 — Anything to avoid?
    "answers_favorites",  # Q7 — Favourite movies for reference?
    "answers_rating",     # Q8 — Minimum rating?
)


class SessionModel(BaseModel):
    """Conversation/session state.

    Mirrors the `sessions` table schema and SessionRepository expectations.

    BUG #7 FIX
    ----------
    ``_TOTAL_QUESTIONS`` declares the canonical number of questionnaire steps.
    It must equal ``len(QUESTION_COLUMNS)`` — verified at module import time
    by ``_validate_question_columns()`` below the class definition.

    Handlers that need to map ``question_index`` to a column name should use::

        from models.domain import QUESTION_COLUMNS
        col = QUESTION_COLUMNS[session.question_index]
    """

    # Number of questionnaire questions — must match len(QUESTION_COLUMNS).
    _TOTAL_QUESTIONS: int = len(QUESTION_COLUMNS)  # = 9

    chat_id: str
    session_state: str = "idle"
    question_index: int = 0

    pending_question: Optional[str] = None
    answers_mood: Optional[str] = None
    answers_genre: Optional[str] = None
    answers_language: Optional[str] = None
    answers_era: Optional[str] = None
    answers_context: Optional[str] = None
    answers_time: Optional[str] = None
    answers_avoid: Optional[str] = None
    answers_favorites: Optional[str] = None
    answers_rating: Optional[str] = None

    last_recs_json: str = "[]"
    overflow_buffer_json: str = "[]"
    sim_depth: int = 0

    updated_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "SessionModel":
        return cls(
            chat_id=str(row.get("chat_id", "")),
            session_state=row.get("session_state") or "idle",
            question_index=int(row.get("question_index") or 0),
            pending_question=row.get("pending_question") or None,
            answers_mood=row.get("answers_mood") or None,
            answers_genre=row.get("answers_genre") or None,
            answers_language=row.get("answers_language") or None,
            answers_era=row.get("answers_era") or None,
            answers_context=row.get("answers_context") or None,
            answers_time=row.get("answers_time") or None,
            answers_avoid=row.get("answers_avoid") or None,
            answers_favorites=row.get("answers_favorites") or None,
            answers_rating=row.get("answers_rating") or None,
            last_recs_json=row.get("last_recs_json") or "[]",
            overflow_buffer_json=row.get("overflow_buffer_json") or "[]",
            sim_depth=int(row.get("sim_depth") or 0),
            updated_at=row.get("updated_at") or None,
        )

    def to_row(self) -> Dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "session_state": self.session_state,
            "question_index": self.question_index,
            "pending_question": self.pending_question or "",
            "answers_mood": self.answers_mood or "",
            "answers_genre": self.answers_genre or "",
            "answers_language": self.answers_language or "",
            "answers_era": self.answers_era or "",
            "answers_context": self.answers_context or "",
            "answers_time": self.answers_time or "",
            "answers_avoid": self.answers_avoid or "",
            "answers_favorites": self.answers_favorites or "",
            "answers_rating": self.answers_rating or "",
            "last_recs_json": self.last_recs_json,
            "overflow_buffer_json": self.overflow_buffer_json,
            "sim_depth": int(self.sim_depth),
            "updated_at": self.updated_at or utc_now_iso(),
        }


def _validate_question_columns() -> None:
    """Assert at import time that QUESTION_COLUMNS is consistent with SessionModel.

    Checks:
    1. len(QUESTION_COLUMNS) == SessionModel._TOTAL_QUESTIONS
    2. Every column name in QUESTION_COLUMNS is an actual field on SessionModel.

    Raises AssertionError immediately so CI and startup both catch mismatches
    before any user interaction occurs.
    """
    total = SessionModel._TOTAL_QUESTIONS
    assert len(QUESTION_COLUMNS) == total, (
        f"QUESTION_COLUMNS has {len(QUESTION_COLUMNS)} entries but "
        f"SessionModel._TOTAL_QUESTIONS is {total}. "
        "Update QUESTION_COLUMNS or _TOTAL_QUESTIONS to match."
    )
    session_fields = set(SessionModel.model_fields.keys())
    for col in QUESTION_COLUMNS:
        assert col in session_fields, (
            f"QUESTION_COLUMNS references '{col}' which is not a field "
            "on SessionModel. Check for typos or schema drift."
        )


# Run validation once when the module is imported.
_validate_question_columns()
