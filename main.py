import os
import asyncio
import uuid
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

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
    asyncio.ensure_future(_keepalive_loop())
    yield


app = FastAPI(title="CineMate Bot API", lifespan=lifespan)


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

@app.get("/webhook/{token}")
async def webhook_get(token: str):
    return JSONResponse({
        "ok": True,
        "info": "CineMate webhook is active. Telegram sends POST requests here.",
    })


@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request):
    if not BOT_TOKEN or token != BOT_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")

    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "description": "invalid JSON"})

    normalized = normalize_input(update)
    chat_id = normalized.get("chat_id")
    if chat_id is None:
        return JSONResponse({"ok": True})

    # -- Dedup ---------------------------------------------------------------
    # Looked up via module object so monkeypatch.setattr(redis_cache, ...) works.
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
    # Looked up via module object so monkeypatch.setattr(redis_cache, ...) works.
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

    # -- Load real user row (CC-1) -------------------------------------------
    user_row = {}
    try:
        from services.container import user_service as _us
        user_model = _us.get_user(str(chat_id))
        user_row = user_model.to_row()
    except Exception:
        user_row = {}

    intent = detect_intent(input_text, session_row)
    request_id = str(uuid.uuid4())

    # -- Dispatch via services.enqueue_job (P2-1) ----------------------------
    # Wrapped in try/except so that any RQ/Redis failure is logged and
    # swallowed -- Telegram requires HTTP 200 from every webhook call or it
    # will keep retrying the same update indefinitely.
    # Accessed via module attribute so monkeypatch.setattr('services.enqueue_job')
    # intercepts the call correctly in tests after reload().
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
