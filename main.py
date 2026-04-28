import os
import asyncio
import uuid
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# Import modules (not names) so monkeypatch.setattr on the module object
# intercepts calls even after importlib.reload(main) in tests.
import services
import config.redis_cache as _redis_cache

from services import LoggingService
from config.app_config import get_startup_readiness
from handlers.normalizer import normalize_input, detect_intent

logger = logging.getLogger("main")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SELF_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
KEEPALIVE_INTERVAL = 9 * 60  # ping every 9 minutes

# Admin chat IDs -- comma-separated in env, e.g. ADMIN_CHAT_IDS=123456789,987654321
ADMIN_IDS: set[str] = set(
    i.strip() for i in os.environ.get("ADMIN_CHAT_IDS", "").split(",") if i.strip()
)

# ---------------------------------------------------------------------------
# Request size limit
# ---------------------------------------------------------------------------
MAX_REQUEST_BODY_BYTES: int = int(
    os.environ.get("CINEMATE_MAX_REQUEST_BYTES", str(1 * 1024 * 1024))  # 1 MB default
)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds MAX_REQUEST_BODY_BYTES.

    Two-stage check:
    1. Content-Length header (fast path, no body read required).
    2. Actual body bytes read (covers chunked-encoding where no header is set).

    BUG-7 NOTE: Starlette caches the result of await request.body() on the
    Request object itself, so downstream handlers calling await request.json()
    will re-use the cached bytes rather than consuming the stream a second
    time.  This is safe and correct behaviour with BaseHTTPMiddleware.
    """

    async def dispatch(self, request: Request, call_next):
        # Fast path: Content-Length header present
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    logger.warning(
                        "Rejected oversized request: Content-Length=%s bytes (limit=%s)",
                        content_length,
                        MAX_REQUEST_BODY_BYTES,
                    )
                    return JSONResponse(
                        {"ok": False, "description": "Request body too large"},
                        status_code=413,
                    )
            except ValueError:
                pass  # Malformed header -- let the handler deal with it

        # Slow path: read body (Starlette caches it; downstream reads are free)
        body = await request.body()
        if len(body) > MAX_REQUEST_BODY_BYTES:
            logger.warning(
                "Rejected oversized request: actual body=%s bytes (limit=%s)",
                len(body),
                MAX_REQUEST_BODY_BYTES,
            )
            return JSONResponse(
                {"ok": False, "description": "Request body too large"},
                status_code=413,
            )

        return await call_next(request)


async def _keepalive_loop():
    """Ping /health every 9 minutes so Render never spins down."""
    url = f"{SELF_URL}/health" if SELF_URL else None
    if not url:
        logger.info("RENDER_EXTERNAL_URL not set -- keepalive disabled (local dev mode)")
        return
    logger.info("Keepalive started -> pinging %s every %d s", url, KEEPALIVE_INTERVAL)
    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            try:
                r = await client.get(url)
                logger.debug("Keepalive ping -> %s %s", url, r.status_code)
            except Exception as e:
                logger.warning("Keepalive ping failed: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # M-5 FIX: asyncio.ensure_future() is deprecated since Python 3.10+.
    # asyncio.create_task() is the correct API for scheduling a coroutine
    # as a background task when an event loop is already running.
    asyncio.create_task(_keepalive_loop())

    # M-2 FIX: clean up stale daily call counters from app_config on startup.
    # These keys have the form "provider.<name>.calls.<YYYY-MM-DD>" and
    # accumulate one per provider per day, forever.
    try:
        from datetime import datetime, timedelta, timezone
        import config.supabase_client as sb
        if sb.is_configured():
            cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
            rows, err = sb.select_rows("app_config", limit=500)
            if not err and rows:
                stale_keys = [
                    r["key"] for r in rows
                    if r.get("key", "").startswith("provider.") and ".calls." in r.get("key", "")
                    and r["key"].rsplit(".", 1)[-1] < cutoff
                ]
                for key in stale_keys:
                    sb.delete_rows("app_config", filters={"key": key})
                if stale_keys:
                    logging.getLogger("main").info(
                        "[Lifespan] Cleaned %d stale daily call counter(s) from app_config",
                        len(stale_keys),
                    )
    except Exception:
        pass  # non-critical cleanup — never block startup

    yield
    # I-10 FIX: flush all buffered log batches before process exit so no
    # interaction or error log rows are silently dropped.
    try:
        from services.logging_service import interaction_batcher, error_batcher
        interaction_batcher.shutdown()
        error_batcher.shutdown()
    except Exception:
        pass
    # M-1 FIX: close TelegramClient's httpx.AsyncClient to avoid
    # "unclosed client session" warnings on shutdown.
    try:
        from clients.telegram_client import TelegramClient
        await TelegramClient.get_instance().close()
    except Exception:
        pass


app = FastAPI(title="CineMate Bot API", lifespan=lifespan)

# Register size-limit middleware AFTER app creation so it wraps all routes.
app.add_middleware(RequestSizeLimitMiddleware)


# ---------------------------------------------------------------------------
# Health & Debug
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> dict:
    readiness = get_startup_readiness()
    return {"status": "ok", "readiness": readiness}


@app.get("/debug/start")
async def debug_start():
    """Full end-to-end smoke test that runs handle_start and returns a trace."""
    import traceback
    report = {}

    report["bot_token_set"] = bool(BOT_TOKEN)
    report["render_url"] = SELF_URL or "(not set)"

    try:
        from config import supabase_client as sb
        report["supabase_configured"] = sb.is_configured()
        report["supabase_url_prefix"] = os.environ.get("SUPABASE_URL", "")[:40]
    except Exception as e:
        report["supabase_error"] = str(e)

    try:
        from services.container import session_service
        report["session_service_ok"] = True
    except Exception as e:
        report["session_service_error"] = str(e)
        report["session_service_trace"] = traceback.format_exc()
        return report

    admin_chat_id = os.environ.get("ADMIN_CHAT_IDS", "1878846631").split(",")[0].strip()
    try:
        session = session_service.get_session(admin_chat_id)
        report["get_session_ok"] = True
        report["session_state"] = session.session_state
    except Exception as e:
        report["get_session_error"] = str(e)
        report["get_session_trace"] = traceback.format_exc()
        return report

    try:
        from handlers.user_handlers import handle_start
        report["handle_start_import_ok"] = True
    except Exception as e:
        report["handle_start_import_error"] = str(e)
        report["handle_start_import_trace"] = traceback.format_exc()
        return report

    try:
        from clients.telegram_helpers import send_message  # noqa: F401
        report["telegram_helpers_import_ok"] = True
    except Exception as e:
        report["telegram_helpers_error"] = str(e)
        return report

    try:
        await handle_start(
            chat_id=admin_chat_id,
            username="debug_test",
            session=session.to_row(),
            user={},
        )
        report["handle_start_executed"] = True
        report["result"] = "SUCCESS -- check your Telegram for the welcome message!"
    except Exception as e:
        report["handle_start_error"] = str(e)
        report["handle_start_trace"] = traceback.format_exc()

    return report


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if not BOT_TOKEN or token != BOT_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")

    secret_token = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if secret_token:
        request_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if request_secret != secret_token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "description": "invalid JSON"})

    normalized = normalize_input(update)
    chat_id = normalized.get("chat_id")
    if chat_id is None:
        return JSONResponse({"ok": True})

    # -- Dedup ---------------------------------------------------------------
    update_id = normalized.get("update_id")
    if update_id is not None:
        if not _redis_cache.mark_processed_update(str(update_id)):
            return JSONResponse({"ok": True})

    username = normalized.get("username") or ""
    input_text = normalized.get("input_text") or ""
    callback_query_id = normalized.get("callback_query_id")
    message_id = normalized.get("message_id")
    sent_at = normalized.get("sent_at")

    # -- Rate limiting -------------------------------------------------------
    user_tier = "admin" if str(chat_id) in ADMIN_IDS else "user"
    if _redis_cache.is_rate_limited(f"chat:{chat_id}", user_tier=user_tier):
        from clients.telegram_helpers import send_message_safely
        await send_message_safely(
            chat_id,
            "You're sending messages very quickly. Please slow down a little so I can keep up. \U0001f642",
        )
        return JSONResponse({"ok": True})

    # -- Load session --------------------------------------------------------
    session_row = {}
    try:
        from services.container import session_service as _ss
        session_model = _ss.get_session(str(chat_id))
        session_row = session_model.to_row()
    except Exception:
        session_row = {}

    # -- Load real user row --------------------------------------------------
    user_row = {}
    try:
        from services.container import user_service as _us
        user_model = _us.get_user(str(chat_id))
        user_row = user_model.to_row()
    except Exception:
        user_row = {}

    intent = detect_intent(input_text, session_row)
    request_id = str(uuid.uuid4())

    # -- Dispatch via services.enqueue_job -----------------------------------
    enqueue_ok = True
    try:
        services.enqueue_job(
            "services.worker_service.run_intent_job",
            intent=intent,
            chat_id=str(chat_id),
            username=username,
            input_text=input_text,
            session=session_row,
            user=user_row,
            request_id=request_id,
            callback_query_id=callback_query_id,
            message_id=message_id,
            user_sent_at=sent_at,
        )
    except Exception as exc:
        enqueue_ok = False
        logger.error(
            "enqueue_job failed for chat_id=%s intent=%s request_id=%s: %s",
            chat_id, intent, request_id, exc,
        )

    LoggingService.log_event(
        chat_id=str(chat_id),
        intent=intent,
        step="enqueued",
        request_id=request_id,
        provider="inline" if os.environ.get("CINEMATE_INLINE_JOBS", "0") in ("1", "true", "yes") else "rq",
        status="success" if enqueue_ok else "enqueue_error",
    )

    return JSONResponse({"ok": True})
