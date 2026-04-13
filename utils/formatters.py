"""Presentation-layer formatting helpers for history and watchlist.

These live in utils/ (not services/) because they are pure string-rendering
functions with no business logic or I/O — they belong in the utility layer.
"""
from __future__ import annotations

from typing import Any, Dict, List

PAGE_SIZE = 10


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
