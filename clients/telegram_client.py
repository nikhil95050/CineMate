"""Low-level Telegram Bot API HTTP client.

Provides a singleton ``TelegramClient`` used by telegram_helpers.py for
all outbound Telegram API calls.  Keeps a single ``httpx.AsyncClient``
for connection reuse and exposes the four methods the rest of the
codebase needs.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("telegram_client")

_TELEGRAM_API_ROOT = "https://api.telegram.org"


def _build_base_url(token: str) -> str:
    return f"{_TELEGRAM_API_ROOT}/bot{token}"


class TelegramClient:
    """Async HTTP client wrapping the Telegram Bot API.

    Usage
    -----
    Call ``TelegramClient.get_instance()`` to obtain the application-wide
    singleton.  The singleton is initialised lazily on first access using
    the ``TELEGRAM_BOT_TOKEN`` environment variable.
    """

    _instance: Optional["TelegramClient"] = None

    def __init__(self, token: str) -> None:
        self._base_url: str = _build_base_url(token)
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=15.0)

    # ------------------------------------------------------------------
    # Singleton
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls) -> "TelegramClient":
        """Return (or create) the process-wide singleton."""
        if cls._instance is None:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            if not token:
                logger.warning(
                    "[TelegramClient] TELEGRAM_BOT_TOKEN is not set; "
                    "outbound messages will fail."
                )
            cls._instance = cls(token)
        return cls._instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        """The fully-qualified base URL, e.g. https://api.telegram.org/bot<TOKEN>."""
        return self._base_url

    async def send_message(
        self,
        chat_id: Any,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Any] = None,
        **kwargs: Any,
    ) -> Optional[dict]:
        """Call sendMessage and return the decoded JSON body."""
        payload: dict = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        payload.update(kwargs)
        return await self._post("sendMessage", payload)

    async def edit_message(
        self,
        chat_id: Any,
        message_id: Any,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Any] = None,
        **kwargs: Any,
    ) -> Optional[dict]:
        """Call editMessageText and return the decoded JSON body."""
        payload: dict = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        payload.update(kwargs)
        return await self._post("editMessageText", payload)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
        show_alert: bool = False,
        **kwargs: Any,
    ) -> None:
        """Call answerCallbackQuery (fire-and-forget; result ignored)."""
        payload: dict = {
            "callback_query_id": callback_query_id,
            "show_alert": show_alert,
        }
        if text:
            payload["text"] = text
        payload.update(kwargs)
        await self._post("answerCallbackQuery", payload)

    async def send_chat_action(
        self,
        chat_id: Any,
        action: str = "typing",
    ) -> None:
        """Call sendChatAction (fire-and-forget; result ignored)."""
        await self._post("sendChatAction", {"chat_id": chat_id, "action": action})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post(self, method: str, payload: dict) -> Optional[dict]:
        url = f"{self._base_url}/{method}"
        try:
            resp = await self._http.post(url, json=payload)
            data = resp.json()
            if not data.get("ok"):
                logger.warning(
                    "[TelegramClient] %s returned ok=false: %s",
                    method,
                    data.get("description", "unknown error"),
                )
            return data
        except Exception as exc:
            logger.error("[TelegramClient] %s failed: %s", method, exc)
            return None
