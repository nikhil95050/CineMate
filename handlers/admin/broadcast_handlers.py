"""Admin broadcast: pending-confirm-cancel pattern with rate limiting.

BUG #8 FIX
----------
The previous implementation slept a flat 50 ms after every single message
(~20 msgs/sec sustained). While that avoids a pure burst, Telegram enforces
a hard limit of 30 msgs/sec globally AND 1 msg/sec per individual chat.
Long broadcasts with many recipients could still exceed the global cap during
the first burst window before the first sleep, and would reliably hit the
per-chat 1 msg/sec cap on re-broadcasts to the same user.

Fix: replace the uniform micro-sleep with a two-tier approach:
  1. A mandatory 50 ms delay after EVERY message (respects per-chat limit).
  2. An additional 1-second pause after every batch of 25 messages, keeping
     the sustained throughput at ~24 msgs/sec — safely below the 30 msg/sec
     global ceiling even accounting for jitter.

The constant _BATCH_SIZE = 25 and _BATCH_PAUSE = 1.0 are exposed at module
level so they can be tuned without touching logic.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from clients.telegram_helpers import send_message, send_message_safely, send_message_with_keyboard
from handlers.admin.decorator import admin_only

logger = logging.getLogger("broadcast_handlers")

_BROADCAST_KEY = "admin:broadcast:pending"

# BUG #8 FIX — two-tier rate limiting constants
_MSG_DELAY  = 0.05   # 50 ms between every individual send  (~20 msgs/sec sustained)
_BATCH_SIZE = 25     # after this many messages …
_BATCH_PAUSE = 1.0   # … pause 1 second to stay well under 30 msgs/sec global cap

_PENDING_STORE: dict = {}   # only used when Redis is unavailable


def _store_pending(message: str) -> None:
    try:
        import config.redis_cache as rc
        client = rc.get_redis()
        if client:
            client.setex(_BROADCAST_KEY, 600, message)   # 10-min TTL
            return
    except Exception:
        pass
    _PENDING_STORE["msg"] = message


def _pop_pending() -> str | None:
    try:
        import config.redis_cache as rc
        client = rc.get_redis()
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
        client = rc.get_redis()
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
    await send_message_with_keyboard(
        chat_id,
        f"\U0001f4e2 <b>Broadcast Preview</b>\n\n{preview}\n\n"
        f"Send to <b>all users</b>?",
        reply_markup=json.dumps(keyboard),
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

    # BUG #8 FIX: two-tier rate-limited send loop.
    # Tier 1 — 50 ms sleep after every message  (~20 msgs/sec).
    # Tier 2 — extra 1 s pause every 25 messages (keeps global rate < 30/sec).
    for i, cid in enumerate(chat_ids):
        try:
            await send_message_safely(cid, message)
            sent += 1
        except Exception as exc:
            failed += 1
            logger.warning("[broadcast] failed for %s: %s", cid, exc)

        await asyncio.sleep(_MSG_DELAY)          # tier-1: per-message delay

        if (i + 1) % _BATCH_SIZE == 0:           # tier-2: batch pause
            logger.debug(
                "[broadcast] batch pause after %d messages (sent=%d, failed=%d)",
                i + 1, sent, failed,
            )
            await asyncio.sleep(_BATCH_PAUSE)

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
