"""Admin broadcast: pending-confirm-cancel pattern with rate limiting."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from clients.telegram_helpers import send_message, send_message_safely
from handlers.admin.decorator import admin_only

logger = logging.getLogger("broadcast_handlers")

_BROADCAST_KEY = "admin:broadcast:pending"
_BROADCAST_DELAY = 0.05   # 50 ms between sends -> ~20 msgs/sec

_PENDING_STORE: dict = {}   # only used when Redis is unavailable


def _store_pending(message: str) -> None:
    try:
        import config.redis_cache as rc
        client = rc.get_redis()          # <-- corrected
        if client:
            client.setex(_BROADCAST_KEY, 600, message)   # 10-min TTL
            return
    except Exception:
        pass
    _PENDING_STORE["msg"] = message


def _pop_pending() -> str | None:
    try:
        import config.redis_cache as rc
        client = rc.get_redis()          # <-- corrected
        if client:
            val = client.get(_BROADCAST_KEY)
            client.delete(_BROADCAST_KEY)
            return val.decode() if isinstance(val, bytes) else val
    except Exception:
        pass
    return _PENDING_STORE.pop("msg", None)


def _cancel_pending() -> bool:
    try:
        import config.redis_cache as rc
        client = rc.get_redis()          # <-- corrected
        if client:
            deleted = client.delete(_BROADCAST_KEY)
            return bool(deleted)
    except Exception:
        pass
    return bool(_PENDING_STORE.pop("msg", None))


# ---------------------------------------------------------------------------
# /admin_broadcast  <message>
# ---------------------------------------------------------------------------

@admin_only
async def handle_admin_broadcast(
    chat_id: Any,
    input_text: str = "",
    **kwargs,
) -> None:
    text = (input_text or "").strip()
    for prefix in ("/admin_broadcast ", "admin_broadcast "):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break

    text = text.strip()
    if not text:
        await send_message(
            chat_id,
            "Usage: <code>/admin_broadcast Your message here</code>",
        )
        return

    _store_pending(text)

    preview = text[:200] + ("\u2026" if len(text) > 200 else "")
    keyboard = {
        "inline_keyboard": [[
            {"text": "\u2705 Confirm & Send", "callback_data": "admin_broadcast_confirm"},
            {"text": "\u274c Cancel",         "callback_data": "admin_broadcast_cancel"},
        ]]
    }
    import json
    try:
        from clients.telegram_helpers import send_message_with_keyboard
        await send_message_with_keyboard(
            chat_id,
            f"\U0001f4e2 <b>Broadcast Preview</b>\n\n{preview}\n\n"
            f"Send to <b>all users</b>?",
            reply_markup=json.dumps(keyboard),
        )
    except (ImportError, AttributeError):
        # Fallback if send_message_with_keyboard is not yet implemented
        await send_message(
            chat_id,
            f"\U0001f4e2 <b>Broadcast Preview</b>\n\n{preview}\n\n"
            f"Reply with <code>admin_broadcast_confirm</code> to send or "
            f"<code>admin_broadcast_cancel</code> to abort.",
        )


# ---------------------------------------------------------------------------
# admin_broadcast_confirm
# ---------------------------------------------------------------------------

@admin_only
async def handle_admin_broadcast_confirm(
    chat_id: Any,
    **kwargs,
) -> None:
    from services.container import admin_repo

    message = _pop_pending()
    if not message:
        await send_message(chat_id, "\u26a0\ufe0f No pending broadcast found (may have expired).")
        return

    chat_ids = admin_repo.get_all_user_chat_ids()
    if not chat_ids:
        await send_message(chat_id, "\u26a0\ufe0f No users found in the database.")
        return

    await send_message(
        chat_id,
        f"\U0001f4e4 Broadcasting to <b>{len(chat_ids)}</b> users\u2026"
    )

    sent = 0
    failed = 0
    for cid in chat_ids:
        try:
            await send_message_safely(cid, message)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("[broadcast] failed for %s: %s", cid, exc)
        await asyncio.sleep(_BROADCAST_DELAY)

    await send_message(
        chat_id,
        f"\u2705 Broadcast complete.\n"
        f"\u2022 Sent: <b>{sent}</b>\n"
        f"\u2022 Failed: <b>{failed}</b>",
    )


# ---------------------------------------------------------------------------
# admin_broadcast_cancel
# ---------------------------------------------------------------------------

@admin_only
async def handle_admin_broadcast_cancel(
    chat_id: Any,
    **kwargs,
) -> None:
    cancelled = _cancel_pending()
    if cancelled:
        await send_message(chat_id, "\u274c Broadcast cancelled.")
    else:
        await send_message(chat_id, "\u2139\ufe0f No pending broadcast to cancel.")
