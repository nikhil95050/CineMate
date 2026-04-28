"""Worker: routes intents to handlers."""
from __future__ import annotations

import asyncio
import time
import importlib
from contextvars import ContextVar
from typing import Any, Dict

from models import SessionModel, UserModel
from services.logging_service import LoggingService
from services.container import admin_repo
from utils.time_utils import utc_now_iso

_bot_response_ctx: ContextVar[str] = ContextVar("bot_response_text", default="")

_SEMANTIC_MIN_LEN = 10

INTENT_HANDLERS = {
    "start": "handlers.user_handlers.handle_start",
    "help": "handlers.user_handlers.handle_help",
    "reset": "handlers.user_handlers.handle_reset",
    "watchlist": "handlers.history_handlers.handle_watchlist",
    "history": "handlers.history_handlers.handle_history",
    "watched": "handlers.history_handlers.handle_watched",
    "save": "handlers.history_handlers.handle_save",
    # ISSUE 5 FIX: register clear_history so the implemented handler is reachable.
    "clear_history": "handlers.history_handlers.handle_clear_history",
    # ISSUE 12 FIX: register recommend so /recommend starts a clean question flow.
    "recommend": "handlers.rec_handlers.handle_recommend",
    "questioning": "handlers.rec_handlers.handle_questioning",
    "movie": "handlers.movie_handlers.handle_movie",
    "trending": "handlers.movie_handlers.handle_trending",
    "surprise": "handlers.movie_handlers.handle_surprise",
    "more_like": "handlers.movie_handlers.handle_more_like",
    "more_suggestions": "handlers.movie_handlers.handle_more_suggestions",
    "like": "handlers.feedback_handlers.handle_like",
    "dislike": "handlers.feedback_handlers.handle_dislike",
    "min_rating": "handlers.feedback_handlers.handle_min_rating",
    "star": "handlers.discovery_handlers.handle_star",
    "share": "handlers.discovery_handlers.handle_share",
    "admin_health": "handlers.admin.handle_admin_health",
    "admin_stats": "handlers.admin.handle_admin_stats",
    "admin_clear_cache": "handlers.admin.handle_admin_clear_cache",
    "admin_errors": "handlers.admin.handle_admin_errors",
    "admin_usage": "handlers.admin.handle_admin_usage",
    "admin_broadcast": "handlers.admin.handle_admin_broadcast",
    "admin_broadcast_confirm": "handlers.admin.handle_admin_broadcast_confirm",
    "admin_broadcast_cancel": "handlers.admin.handle_admin_broadcast_cancel",
    "admin_disable_provider": "handlers.admin.handle_admin_disable_provider",
    "admin_enable_provider": "handlers.admin.handle_admin_enable_provider",
    "search": "handlers.movie_handlers.handle_movie",
    "movie_search": "handlers.movie_handlers.handle_movie",
}


def get_handler_func(intent: str):
    path = INTENT_HANDLERS.get(intent)
    if not path:
        return None
    module_path, func_name = path.rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, func_name)


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
    _bot_response_ctx.set("")

    try:
        handler_func = get_handler_func(intent)
        if handler_func:
            if intent == "start":
                await handler_func(
                    chat_id=chat_id_str,
                    username=username,
                    session=session,
                    user=user,
                )
            else:
                await handler_func(**kwargs)
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
                        intent=f"fallback\u2192{classified}",
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

        try:
            from services.container import admin_repo
            asyncio.create_task(asyncio.to_thread(admin_repo.increment_stat, "total_interactions"))
        except Exception:
            pass

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

        try:
            from services.container import admin_repo
            asyncio.create_task(asyncio.to_thread(admin_repo.increment_stat, "total_errors"))
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
