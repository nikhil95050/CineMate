"""Worker: routes intents to handlers.

Fallback path (Feature 10):
  When detect_intent returns 'fallback' AND the user message is long enough,
  SemanticService.classify_intent is called once to attempt smarter routing.
  A _semantic_attempted guard prevents recursion / double-classification.

BUG #3 FIX: increment bot_stats (total_interactions / total_errors) on every intent.
BUG #8 FIX: capture bot_response_text via contextvars so log_interaction is populated.
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any, Dict

from models import SessionModel, UserModel
from services.logging_service import LoggingService
from utils.time_utils import utc_now_iso

# BUG #8 FIX — handlers set this context var with their final text response
_bot_response_ctx: ContextVar[str] = ContextVar("bot_response_text", default="")

# Minimum text length before semantic routing is attempted
_SEMANTIC_MIN_LEN = 8


def set_bot_response(text: str) -> None:
    """Called by any handler to record the text sent back to the user."""
    _bot_response_ctx.set(text or "")


async def run_intent_job(
    intent: str,
    chat_id: str,
    username: str,
    input_text: str,
    session: Dict[str, Any],
    user: Dict[str, Any],
    request_id: str,
    callback_query_id: str | None = None,
    message_id: int | None = None,
    user_sent_at: str | None = None,
    _semantic_attempted: bool = False,
) -> None:
    chat_id_str = str(chat_id)
    session_model = SessionModel.from_row({"chat_id": chat_id_str, **(session or {})})
    user_model    = UserModel.from_row({"chat_id": chat_id_str, "username": username, **(user or {})})

    kwargs = dict(
        chat_id=chat_id_str,
        username=username,
        input_text=input_text,
        session=session,
        user=user,
        callback_query_id=callback_query_id,
        message_id=message_id,
    )

    t_start = time.time()
    # BUG #8 FIX — reset context var for this job
    _bot_response_ctx.set("")

    try:
        if intent == "start":
            from handlers.user_handlers import handle_start
            await handle_start(chat_id_str, username, session, user)

        elif intent == "help":
            from handlers.user_handlers import handle_help
            await handle_help(**kwargs)

        elif intent == "reset":
            from handlers.user_handlers import handle_reset
            await handle_reset(**kwargs)

        elif intent == "watchlist":
            from handlers.history_handlers import handle_watchlist
            await handle_watchlist(**kwargs)

        elif intent == "history":
            from handlers.history_handlers import handle_history
            await handle_history(**kwargs)

        elif intent == "watched":
            from handlers.history_handlers import handle_watched
            await handle_watched(**kwargs)

        elif intent == "save":
            from handlers.history_handlers import handle_save
            await handle_save(**kwargs)

        elif intent == "questioning":
            from handlers.rec_handlers import handle_questioning
            await handle_questioning(**kwargs)

        elif intent == "movie":
            from handlers.movie_handlers import handle_movie
            await handle_movie(**kwargs)

        elif intent == "trending":
            from handlers.movie_handlers import handle_trending
            await handle_trending(**kwargs)

        elif intent == "surprise":
            from handlers.movie_handlers import handle_surprise
            await handle_surprise(**kwargs)

        elif intent == "more_like":
            from handlers.movie_handlers import handle_more_like
            await handle_more_like(**kwargs)

        elif intent == "more_suggestions":
            from handlers.movie_handlers import handle_more_suggestions
            await handle_more_suggestions(**kwargs)

        elif intent == "like":
            from handlers.feedback_handlers import handle_like
            await handle_like(**kwargs)

        elif intent == "dislike":
            from handlers.feedback_handlers import handle_dislike
            await handle_dislike(**kwargs)

        elif intent == "min_rating":
            from handlers.feedback_handlers import handle_min_rating
            await handle_min_rating(**kwargs)

        elif intent == "star":
            from handlers.discovery_handlers import handle_star
            await handle_star(**kwargs)

        elif intent == "share":
            from handlers.discovery_handlers import handle_share
            await handle_share(**kwargs)

        elif intent == "admin_health":
            from handlers.admin import handle_admin_health
            await handle_admin_health(**kwargs)

        elif intent == "admin_stats":
            from handlers.admin import handle_admin_stats
            await handle_admin_stats(**kwargs)

        elif intent == "admin_clear_cache":
            from handlers.admin import handle_admin_clear_cache
            await handle_admin_clear_cache(**kwargs)

        elif intent == "admin_errors":
            from handlers.admin import handle_admin_errors
            await handle_admin_errors(**kwargs)

        elif intent == "admin_usage":
            from handlers.admin import handle_admin_usage
            await handle_admin_usage(**kwargs)

        elif intent == "admin_broadcast":
            from handlers.admin import handle_admin_broadcast
            await handle_admin_broadcast(**kwargs)

        elif intent == "admin_broadcast_confirm":
            from handlers.admin import handle_admin_broadcast_confirm
            await handle_admin_broadcast_confirm(**kwargs)

        elif intent == "admin_broadcast_cancel":
            from handlers.admin import handle_admin_broadcast_cancel
            await handle_admin_broadcast_cancel(**kwargs)

        elif intent == "admin_disable_provider":
            from handlers.admin import handle_admin_disable_provider
            await handle_admin_disable_provider(**kwargs)

        elif intent == "admin_enable_provider":
            from handlers.admin import handle_admin_enable_provider
            await handle_admin_enable_provider(**kwargs)

        elif intent in ("search", "movie_search"):
            from handlers.movie_handlers import handle_movie
            await handle_movie(**kwargs)

        else:
            if (
                intent == "fallback"
                and not _semantic_attempted
                and len((input_text or "").strip()) >= _SEMANTIC_MIN_LEN
            ):
                classified = await _semantic_classify(input_text)
                if classified and classified != "unknown":
                    LoggingService.log_event(
                        chat_id=chat_id_str,
                        intent=f"fallback→{classified}",
                        step="semantic_redirect",
                        request_id=request_id,
                        provider="semantic",
                        status="success",
                    )
                    await run_intent_job(
                        intent=classified,
                        chat_id=chat_id_str,
                        username=username,
                        input_text=input_text,
                        session=session,
                        user=user,
                        request_id=request_id,
                        callback_query_id=callback_query_id,
                        message_id=message_id,
                        user_sent_at=user_sent_at,
                        _semantic_attempted=True,
                    )
                    return

            from handlers.user_handlers import handle_fallback
            await handle_fallback(**kwargs)

        latency_ms = int((time.time() - t_start) * 1000)
        bot_replied_at = utc_now_iso()

        # BUG #3 FIX — increment total_interactions stat
        try:
            from repositories.admin_repository import AdminRepository
            AdminRepository().increment_stat("total_interactions")
        except Exception:
            pass

        # BUG #8 FIX — read the response text set by the handler
        bot_response_text = _bot_response_ctx.get("")

        LoggingService.log_event(
            chat_id=chat_id_str, intent=intent, step="completed",
            request_id=request_id, provider="worker", status="success",
            latency_ms=latency_ms,
        )
        LoggingService.log_interaction(
            chat_id=chat_id_str,
            input_text=input_text,
            response_text=bot_response_text,
            intent=intent,
            latency_ms=latency_ms,
            user_sent_at=user_sent_at or utc_now_iso(),
            bot_replied_at=bot_replied_at,
            username=username,
            request_id=request_id,
        )

    except Exception as e:
        latency_ms = int((time.time() - t_start) * 1000)

        # BUG #3 FIX — increment total_errors stat
        try:
            from repositories.admin_repository import AdminRepository
            AdminRepository().increment_stat("total_errors")
        except Exception:
            pass

        LoggingService.log_event(
            chat_id=chat_id_str, intent=intent, step="failed",
            request_id=request_id, provider="worker", status="error",
            error_type="worker_exception", extra={"error": str(e)},
            latency_ms=latency_ms,
        )
        raise


async def _semantic_classify(text: str) -> str:
    """Best-effort semantic classification; returns 'unknown' on any error."""
    try:
        from services.container import semantic_service
        return await semantic_service.classify_intent(text)
    except Exception as exc:
        import logging
        logging.getLogger("worker_service").warning(
            "[worker] semantic classify failed: %s", exc
        )
        return "unknown"
