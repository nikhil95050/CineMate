"""Local development runner using Telegram long-polling.

Run with:  python run_local.py

This bypasses the webhook entirely and polls Telegram's getUpdates API
directly. No ngrok or public URL needed. For local testing only.
"""
import asyncio
import os
import uuid
import logging

import httpx
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_local")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def normalize(update: dict) -> dict:
    """Extract chat_id, username, input_text from a raw Telegram update."""
    result = {"update_id": update.get("update_id")}

    if "message" in update:
        msg = update["message"]
        result["chat_id"] = msg.get("chat", {}).get("id")
        result["username"] = msg.get("from", {}).get("username") or msg.get("from", {}).get("first_name", "User")
        result["input_text"] = msg.get("text", "")
        result["message_id"] = msg.get("message_id")
        result["callback_query_id"] = None

    elif "callback_query" in update:
        cq = update["callback_query"]
        result["chat_id"] = cq.get("message", {}).get("chat", {}).get("id")
        result["username"] = cq.get("from", {}).get("username") or cq.get("from", {}).get("first_name", "User")
        result["input_text"] = cq.get("data", "")
        result["message_id"] = cq.get("message", {}).get("message_id")
        result["callback_query_id"] = cq.get("id")
    else:
        result["chat_id"] = None

    return result


async def process_update(update: dict, client: httpx.AsyncClient) -> None:
    from handlers.normalizer import detect_intent
    from services.container import session_service
    from services.worker_service import run_intent_job

    normalized = normalize(update)
    chat_id = normalized.get("chat_id")
    if not chat_id:
        return

    username = normalized.get("username") or ""
    input_text = normalized.get("input_text") or ""
    callback_query_id = normalized.get("callback_query_id")
    message_id = normalized.get("message_id")

    # Acknowledge callback query immediately so button spinner stops
    if callback_query_id:
        try:
            await client.post(f"{BASE_URL}/answerCallbackQuery",
                              json={"callback_query_id": callback_query_id})
        except Exception:
            pass

    try:
        session_model = session_service.get_session(str(chat_id))
        session_row = session_model.to_row()
    except Exception:
        session_row = {}

    intent = detect_intent(input_text, session_row)
    request_id = str(uuid.uuid4())

    logger.info("[%s] chat_id=%s intent=%s text=%r", request_id[:8], chat_id, intent, input_text)

    try:
        await run_intent_job(
            intent=intent,
            chat_id=str(chat_id),
            username=username,
            input_text=input_text,
            session=session_row,
            user={},
            request_id=request_id,
            callback_query_id=callback_query_id,
            message_id=message_id,
            user_sent_at=None,
        )
        logger.info("[%s] done", request_id[:8])
    except Exception as e:
        logger.error("[%s] FAILED: %s", request_id[:8], e, exc_info=True)
        try:
            from clients.telegram_helpers import send_message_safely
            await send_message_safely(
                chat_id,
                f"\u26a0\ufe0f Error: <code>{str(e)[:300]}</code>\n\nTry /start again."
            )
        except Exception:
            pass


async def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set in .env — cannot start.")
        return

    logger.info("CineMate local polling started. Send /start in Telegram!")
    logger.info("Press Ctrl+C to stop.")

    offset = None
    async with httpx.AsyncClient(timeout=35.0) as client:
        while True:
            try:
                params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
                if offset is not None:
                    params["offset"] = offset

                resp = await client.get(f"{BASE_URL}/getUpdates", params=params)
                data = resp.json()

                if not data.get("ok"):
                    logger.warning("getUpdates error: %s", data)
                    await asyncio.sleep(3)
                    continue

                updates = data.get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1
                    await process_update(update, client)

            except asyncio.CancelledError:
                logger.info("Polling stopped.")
                break
            except Exception as e:
                logger.error("Polling error: %s", e)
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
