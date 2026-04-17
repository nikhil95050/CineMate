from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("telegram_client")


class TelegramClient:
    _instance: Optional["TelegramClient"] = None

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=20.0)

    @classmethod
    def get_instance(cls) -> "TelegramClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def bot_token() -> str:
        return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    @classmethod
    def base_url(cls) -> str:
        token = cls.bot_token()
        return f"https://api.telegram.org/bot{token}" if token else ""

    async def _post(self, method: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        base_url = self.base_url()
        if not base_url:
            logger.warning("TELEGRAM_BOT_TOKEN is not configured; skipping %s", method)
            return None

        response = await self._client.post(f"{base_url}/{method}", json=payload)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok", False):
            raise RuntimeError(data.get("description") or f"Telegram {method} failed")
        return data.get("result")

    async def send_message(
        self,
        chat_id: Any,
        text: str,
        parse_mode: str = "HTML",
        reply_markup: Optional[Any] = None,
        disable_web_page_preview: bool = True,
        **kwargs,
    ) -> Optional[dict[str, Any]]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
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
        disable_web_page_preview: bool = True,
        **kwargs,
    ) -> Optional[dict[str, Any]]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
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
        **kwargs,
    ) -> Optional[dict[str, Any]]:
        payload: dict[str, Any] = {
            "callback_query_id": callback_query_id,
            "text": text,
            "show_alert": show_alert,
        }
        payload.update(kwargs)
        return await self._post("answerCallbackQuery", payload)

    async def send_chat_action(
        self,
        chat_id: Any,
        action: str = "typing",
        **kwargs,
    ) -> Optional[dict[str, Any]]:
        payload: dict[str, Any] = {"chat_id": chat_id, "action": action}
        payload.update(kwargs)
        return await self._post("sendChatAction", payload)
