"""Handlers for /history, /watched, and /clear_history commands.

Fixes applied
-------------
#3  /watched command silently failed because the handler called
    history_repo.add_movie() instead of history_repo.mark_watched().  →  Use
    the correct mark_watched(chat_id, movie_id) call.
#4  /history displayed raw JSON stored in the DB column (last_recs_json)
    instead of the structured history rows.  →  Call
    history_repo.get_history(chat_id) and render a human-readable list.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from clients.telegram_helpers import send_message
from services.container import history_repo

logger = logging.getLogger("history_handlers")

# Maximum entries shown without pagination
_PAGE_SIZE = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _render_history_row(row: Any, index: int) -> str:
    """Format a single history row (dict or model) as an HTML line."""
    if hasattr(row, "model_dump"):
        row = row.model_dump()
    elif hasattr(row, "dict"):
        row = row.dict()

    title   = row.get("title") or "Unknown"
    year    = row.get("year") or ""
    rating  = row.get("rating") or ""
    genres  = row.get("genres") or ""
    watched = row.get("watched", False)

    status = "✅" if watched else "🟡"
    line = f"{index}. {status} <b>{title}</b>"
    if year:
        line += f" ({year})"
    meta = []
    if rating:
        meta.append(f"⭐ {rating}")
    if genres:
        meta.append(genres)
    if meta:
        line += "  —  " + " | ".join(meta)
    return line


# ---------------------------------------------------------------------------
# /history
# ---------------------------------------------------------------------------

async def handle_history(
    chat_id: Any,
    input_text: str = "",
    **kwargs,
) -> None:
    """Show the user's recommendation history.

    Fix #4 — previously returned raw JSON from the session column.  Now
    correctly fetches structured rows from the history table.
    """
    chat_id_str = str(chat_id)

    try:
        rows = history_repo.get_history(chat_id_str)  # returns list[HistoryModel | dict]
    except Exception as exc:
        logger.error("[handle_history] get_history failed: %s", exc)
        await send_message(
            chat_id,
            "⚠️ Couldn't load your history right now. Please try again later.",
        )
        return

    if not rows:
        await send_message(
            chat_id,
            "📂 <b>No history yet!</b>\n\n"
            "Start by using /recommend — recommended movies appear here automatically.",
        )
        return

    # Render up to _PAGE_SIZE entries (most recent first)
    recent = list(rows)[-_PAGE_SIZE:]
    lines  = ["📜 <b>Your recommendation history</b>\n"]
    for i, row in enumerate(recent, 1):
        lines.append(_render_history_row(row, i))

    total = len(rows)
    if total > _PAGE_SIZE:
        lines.append(
            f"\n<i>Showing last {_PAGE_SIZE} of {total} entries. "
            "Use /clear_history to start fresh.</i>"
        )
    else:
        lines.append(
            "\n<i>✅ = watched  🟡 = not watched yet.  "
            "Use /watched &lt;movie_title&gt; to mark as watched.</i>"
        )

    await send_message(chat_id, "\n".join(lines))


# ---------------------------------------------------------------------------
# /watched
# ---------------------------------------------------------------------------

async def handle_watched(
    chat_id: Any,
    input_text: str = "",
    **kwargs,
) -> None:
    """Mark a movie as watched by title substring or movie_id.

    Fix #3 — previously called history_repo.add_movie() which is the
    insert-new-entry path, not the mark-watched path.  Now calls
    history_repo.mark_watched(chat_id, movie_id).
    """
    chat_id_str = str(chat_id)
    text = (input_text or "").strip()

    # Strip command prefix
    for prefix in ("/watched ", "watched "):
        if text.lower().startswith(prefix):
            text = text[len(prefix):].strip()
            break
    else:
        await send_message(
            chat_id,
            "✅ <b>Mark as Watched</b>\n\n"
            "Usage: <code>/watched &lt;movie title or ID&gt;</code>\n"
            "Example: <code>/watched Inception</code>",
        )
        return

    if not text:
        await send_message(
            chat_id,
            "⚠️ Please provide a movie title or ID.\n"
            "Example: <code>/watched The Dark Knight</code>",
        )
        return

    # Try to find the movie_id from the user's history
    try:
        rows = history_repo.get_history(chat_id_str)
    except Exception as exc:
        logger.error("[handle_watched] get_history failed: %s", exc)
        rows = []

    matched_id: Optional[str] = None
    matched_title: str = text

    for row in (rows or []):
        if hasattr(row, "model_dump"):
            d = row.model_dump()
        elif hasattr(row, "dict"):
            d = row.dict()
        else:
            d = row if isinstance(row, dict) else {}

        row_id    = str(d.get("movie_id", ""))
        row_title = str(d.get("title", "")).lower()

        if row_id == text or text.lower() in row_title:
            matched_id    = row_id
            matched_title = d.get("title") or text
            break

    if not matched_id:
        await send_message(
            chat_id,
            f"⚠️ Could not find <b>{text}</b> in your history.\n"
            "Use /history to see your recommended movies first.",
        )
        return

    # Fix #3 — call mark_watched, not add_movie
    try:
        history_repo.mark_watched(chat_id_str, matched_id)
    except Exception as exc:
        logger.error("[handle_watched] mark_watched failed: %s", exc)
        await send_message(
            chat_id,
            "⚠️ Couldn't update your watch status right now. Please try again.",
        )
        return

    await send_message(
        chat_id,
        f"✅ <b>{matched_title}</b> marked as watched!\n"
        "Use /history to see your full list.",
    )


# ---------------------------------------------------------------------------
# /clear_history
# ---------------------------------------------------------------------------

async def handle_clear_history(
    chat_id: Any,
    input_text: str = "",
    **kwargs,
) -> None:
    """Delete all history entries for the user."""
    chat_id_str = str(chat_id)

    try:
        history_repo.clear_history(chat_id_str)
    except Exception as exc:
        logger.error("[handle_clear_history] clear_history failed: %s", exc)
        await send_message(
            chat_id,
            "⚠️ Couldn't clear your history right now. Please try again later.",
        )
        return

    await send_message(
        chat_id,
        "🗑️ Your recommendation history has been cleared.\n"
        "Use /recommend to start fresh!",
    )
