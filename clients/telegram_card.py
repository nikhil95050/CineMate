"""Shared movie card formatting for Telegram (HTML parse mode)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from clients.telegram_helpers import send_message
from models.domain import MovieModel
from services.logging_service import get_logger

logger = get_logger("card")

# Telegram HTML message length limit (leave headroom)
_MAX_MSG_LEN = 3800


def _star_rating(rating: Optional[float]) -> str:
    if not rating:
        return ""
    filled = round(rating / 2)  # scale 10 → 5 stars
    return "⭐" * filled + f" {rating:.1f}"


def _truncate(text: str, limit: int = 200) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def build_movie_card_text(movie: Dict[str, Any]) -> str:
    """Build the HTML caption for a single movie card."""
    title = movie.get("title", "Unknown")
    year = movie.get("year") or ""
    rating = movie.get("rating")
    genres = movie.get("genres") or ""
    language = movie.get("language") or ""
    description = movie.get("description") or ""
    reason = movie.get("reason") or ""
    streaming = movie.get("streaming") or ""
    trailer = movie.get("trailer") or ""

    lines: List[str] = []
    header = f"🎬 <b>{title}</b>"
    if year:
        header += f" ({year})"
    lines.append(header)

    if rating:
        lines.append(_star_rating(float(rating)))
    if genres:
        lines.append(f"🎭 {genres}")
    if language and language.lower() not in ("english", "n/a", ""):
        lines.append(f"🌐 {language}")
    if description:
        lines.append(f"\n📖 {_truncate(description, 220)}")
    if reason:
        lines.append(f"\n💡 <i>{_truncate(reason, 160)}</i>")
    if streaming:
        lines.append(f"\n{streaming}")
    if trailer:
        lines.append(f'🎞 <a href="{trailer}">Watch Trailer</a>')

    text = "\n".join(lines)
    if len(text) > _MAX_MSG_LEN:
        text = text[:_MAX_MSG_LEN] + "…"
    return text


def build_movie_keyboard(movie: Dict[str, Any]) -> Dict[str, Any]:
    """Inline keyboard attached to each movie card."""
    movie_id = str(movie.get("movie_id", ""))
    title = str(movie.get("title", ""))[:30]
    rows = [
        [
            {"text": "👍 Like", "callback_data": f"like_{movie_id}"},
            {"text": "👎 Dislike", "callback_data": f"dislike_{movie_id}"},
        ],
        [
            {"text": "💾 Save", "callback_data": f"save_{movie_id}"},
            {"text": "✅ Watched", "callback_data": f"watched_{movie_id}"},
        ],
        [
            {"text": "🎯 More like this", "callback_data": f"more_like_{movie_id}"},
        ],
    ]
    return {"inline_keyboard": rows}


async def send_single_movie_async(chat_id: Any, movie: Dict[str, Any]) -> None:
    """Send a single movie card with its inline keyboard."""
    text = build_movie_card_text(movie)
    keyboard = build_movie_keyboard(movie)

    poster = movie.get("poster")
    if poster and poster != "N/A":
        from clients.telegram_helpers import BASE_URL
        import httpx
        payload = {
            "chat_id": chat_id,
            "photo": poster,
            "caption": text,
            "parse_mode": "HTML",
            "reply_markup": keyboard,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{BASE_URL}/sendPhoto", json=payload)
                if resp.status_code == 200:
                    return
        except Exception as exc:
            logger.warning("sendPhoto failed (%s), falling back to text", exc)

    # Fallback: text-only card
    await send_message(chat_id, text, reply_markup=keyboard)


async def send_movies_async(
    chat_id: Any,
    movies: List[Dict[str, Any]],
    append_more_button: bool = True,
) -> None:
    """Send a list of movie cards, with an optional 'More suggestions' button at the end."""
    if not movies:
        await send_message(chat_id, "🤷 No movies found. Try /trending or /surprise!")
        return

    for movie in movies:
        await send_single_movie_async(chat_id, movie)

    if append_more_button:
        keyboard = {
            "inline_keyboard": [[
                {"text": "🔄 More suggestions", "callback_data": "more_suggestions_action"},
            ]]
        }
        await send_message(chat_id, "Want more? Tap below 👇", reply_markup=keyboard)
