"""Handlers for /star and /share commands.

/star <name>
  - Calls DiscoveryService.get_star_movies(name)
  - Adds results to history + updates last_recs
  - Sends movie cards via send_movies_async
  - Graceful fallback when star is unknown or LLM returns nothing

/share
  - Builds a formatted text card from session.last_recs_json
  - Includes title, year, rating, genres, reason and streaming info
  - Instructs the user to forward the message
  - Graceful fallback when last_recs is empty
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from clients.telegram_card import send_movies_async
from clients.telegram_helpers import send_message, show_typing
from models.domain import MovieModel, SessionModel
from services.container import (
    discovery_service,
    history_service,
    rec_service,
    session_service,
    user_service,
)
from services.logging_service import get_logger

logger = get_logger("discovery_handlers")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _movie_to_dict(movie: MovieModel) -> Dict[str, Any]:
    """Serialise a MovieModel to a plain dict exactly once.

    All callers (session persistence, send_movies_async) share this single
    serialisation so there is no repeated model_dump() round-trip and no
    risk of a custom serialiser silently dropping fields on a second pass.
    """
    return movie.model_dump()


def _streaming_label(streaming: Any) -> str:
    """Return a safe string label for a streaming value.

    Accepts str, list, dict, or None without raising.  Returns an empty
    string when the value carries no meaningful information.
    """
    if streaming is None:
        return ""
    if isinstance(streaming, dict):
        # e.g. {"Netflix": "https://..."} — join keys as the display label
        label = ", ".join(str(k) for k in streaming if k)
    elif isinstance(streaming, list):
        label = ", ".join(str(s) for s in streaming if s)
    else:
        label = str(streaming)
    return "" if label.upper() in ("", "N/A", "NONE") else label


# ---------------------------------------------------------------------------
# /star
# ---------------------------------------------------------------------------

async def handle_star(
    chat_id: Any,
    input_text: str = "",
    session: Optional[Dict[str, Any]] = None,
    user: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Handle /star <actor or director name>."""
    text = (input_text or "").strip()

    # Strip command prefix
    for prefix in ("/star ", "star "):
        if text.lower().startswith(prefix):
            star_name = text[len(prefix):].strip()
            break
    else:
        star_name = ""

    if not star_name:
        await send_message(
            chat_id,
            "\U0001f31f <b>Star Filmography</b>\n\n"
            "Tell me an actor or director's name:\n"
            "<code>/star Leonardo DiCaprio</code>\n"
            "<code>/star Christopher Nolan</code>",
        )
        return

    chat_id_str = str(chat_id)
    await show_typing(chat_id)
    await send_message(
        chat_id,
        f"\U0001f3ac Fetching <b>{star_name}</b>'s filmography\u2026",
    )

    # --- Fetch movies ---------------------------------------------------------
    try:
        movies: List[MovieModel] = await discovery_service.get_star_movies(
            star_name=star_name,
            chat_id=chat_id_str,
        )
    except Exception as exc:
        logger.warning("[handle_star] get_star_movies raised: %s", exc)
        movies = []

    # --- Graceful fallback when nothing found --------------------------------
    if not movies:
        await send_message(
            chat_id,
            f"\U0001f614 Sorry, I couldn't find filmography info for "
            f"<b>{star_name}</b>.\n\n"
            "Double-check the spelling or try a different name:\n"
            "<code>/star Meryl Streep</code>",
        )
        return

    # --- Serialise exactly once so all consumers share the same dict list ----
    movie_dicts: List[Dict[str, Any]] = [_movie_to_dict(m) for m in movies]

    # --- Persist to history (per-movie, logged individually) -----------------
    history_failures = 0
    for movie in movies:
        try:
            history_service.add_to_history(chat_id_str, movie)
        except Exception as exc:
            history_failures += 1
            logger.warning(
                "[handle_star] history add failed for %s: %s",
                getattr(movie, 'movie_id', '?'),
                exc,
            )
    if history_failures:
        logger.warning(
            "[handle_star] %d/%d movies failed to persist to history for chat_id=%s",
            history_failures,
            len(movies),
            chat_id_str,
        )

    # --- Update last_recs in session -----------------------------------------
    try:
        session_model: SessionModel = session_service.get_session(chat_id_str)
        session_model.last_recs_json = json.dumps(movie_dicts)
        session_service.upsert_session(session_model)
    except Exception as exc:
        logger.warning("[handle_star] session update failed: %s", exc)

    # --- Send cards (reuse already-serialised dicts, no second model_dump) ---
    await send_movies_async(chat_id, movie_dicts)


# ---------------------------------------------------------------------------
# /share
# ---------------------------------------------------------------------------

_MAX_SHARE_ITEMS = 5  # Cap to keep the card readable / forwardable


def _build_share_card(
    recs: List[Dict[str, Any]],
    header: str = "\U0001f3ac My CineMate Picks",
) -> str:
    """Build a nicely formatted text card from a list of movie dicts.

    Each entry shows: title, year, rating, genres, reason, and streaming.
    The card is designed to be forwarded directly in Telegram.
    streaming may be a str, list, or dict — _streaming_label() handles all.
    """
    lines: List[str] = [f"<b>{header}</b>", ""]

    for i, rec in enumerate(recs[:_MAX_SHARE_ITEMS], start=1):
        title = rec.get("title") or "Unknown"
        year = rec.get("year") or ""
        rating = rec.get("rating")
        genres = rec.get("genres") or ""
        reason = rec.get("reason") or ""
        streaming_raw = rec.get("streaming") or rec.get("streaming_platforms")
        streaming_label = _streaming_label(streaming_raw)

        # Title line: "1. Inception (2010)  ⭐ 8.8"
        title_line = f"<b>{i}. {title}</b>"
        if year:
            title_line += f" ({year})"
        if rating:
            try:
                title_line += f"  \u2b50 {float(rating):.1f}"
            except (ValueError, TypeError):
                pass
        lines.append(title_line)

        # Genres on own line if present
        if genres:
            lines.append(f"\U0001f3f7\ufe0f {genres}")

        # Reason (curator note)
        if reason:
            lines.append(f"\U0001f4ac {reason}")

        # Streaming info — only show when meaningful
        if streaming_label:
            lines.append(f"\U0001f4fa {streaming_label}")

        lines.append("")  # blank separator

    lines.append("\U0001f916 Powered by <b>CineMate</b> \u2014 your AI movie companion")
    lines.append("Forward this to a friend who loves movies! \U0001f44b")

    return "\n".join(lines)


async def handle_share(
    chat_id: Any,
    input_text: str = "",
    session: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> None:
    """Handle /share — build a forwardable recommendation card from last_recs."""
    chat_id_str = str(chat_id)

    # --- Resolve last_recs from session -------------------------------------
    recs: List[Dict[str, Any]] = []
    try:
        session_model: SessionModel = session_service.get_session(chat_id_str)
        raw_json = session_model.last_recs_json or "[]"
        parsed = json.loads(raw_json)
        recs = parsed if isinstance(parsed, list) else []
    except Exception as exc:
        logger.warning("[handle_share] failed to load last_recs: %s", exc)

    # --- Fallback when nothing to share ------------------------------------
    if not recs:
        await send_message(
            chat_id,
            "\U0001f4f2 <b>Nothing to share yet!</b>\n\n"
            "Get some recommendations first, then use /share to create a card.\n"
            "Try: <code>/trending</code>, <code>/surprise</code>, or "
            "<code>/star Leonardo DiCaprio</code>",
        )
        return

    # --- Build and send the card -------------------------------------------
    card = _build_share_card(recs)
    await send_message(chat_id, card)

    # Friendly follow-up
    count = min(len(recs), _MAX_SHARE_ITEMS)
    await send_message(
        chat_id,
        f"\u2b06\ufe0f Tap and hold the message above to <b>forward</b> it to friends. "
        f"({count} movie{'s' if count != 1 else ''} included)",
    )
