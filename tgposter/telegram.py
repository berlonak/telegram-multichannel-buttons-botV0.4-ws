# copyright by berlonak
# telegram: @Kilax123
from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from .config import Settings

log = logging.getLogger(__name__)


class TelegramAPI:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._me: Optional[dict[str, Any]] = None

    def call(self, method: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        url = f"{self.settings.api_url}/{method}"
        last_error: Optional[Exception] = None

        for attempt in range(4):
            try:
                response = requests.post(url, json=payload or {}, timeout=self.settings.api_timeout)
                data = response.json()
            except Exception as exc:
                last_error = exc
                if attempt == 3:
                    raise RuntimeError(f"Telegram request failed: {exc}") from exc
                time.sleep(1 + attempt)
                continue

            if data.get("ok"):
                return data

            params = data.get("parameters") or {}
            retry_after = params.get("retry_after")
            description = data.get("description", "Unknown Telegram API error")

            if retry_after and attempt < 3:
                delay = max(1, int(retry_after))
                log.warning("Telegram rate limit: retry in %s sec", delay)
                time.sleep(delay)
                continue

            if response.status_code >= 500 and attempt < 3:
                time.sleep(1 + attempt)
                continue

            raise RuntimeError(description)

        if last_error:
            raise RuntimeError(str(last_error)) from last_error
        raise RuntimeError("Telegram request failed")

    def get_me(self) -> dict[str, Any]:
        if self._me is None:
            self._me = self.call("getMe")["result"]
        return self._me

    def get_updates(self, offset: int, timeout: int) -> list[dict[str, Any]]:
        payload = {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        return self.call("getUpdates", payload).get("result", [])

    def answer_message(self, chat_id: Any, text: str, reply_markup: Optional[dict[str, Any]] = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self.call("sendMessage", payload)

    def answer_callback(self, callback_query_id: str, text: str = "", show_alert: bool = False) -> None:
        self.call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text, "show_alert": show_alert})

    def send_content(
        self,
        chat_id: Any,
        content_type: str,
        text: str,
        reply_markup: dict[str, Any],
        file_id: Optional[str] = None,
        entities: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        entities = entities or []
        if content_type == "text":
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": text,
                "entities": entities,
                "reply_markup": reply_markup,
                "disable_web_page_preview": True,
            }
            return self.call("sendMessage", payload)["result"]

        if not file_id:
            raise RuntimeError("file_id is required for media content")

        method_by_type = {
            "photo": "sendPhoto",
            "video": "sendVideo",
            "document": "sendDocument",
            "animation": "sendAnimation",
        }
        file_field_by_type = {
            "photo": "photo",
            "video": "video",
            "document": "document",
            "animation": "animation",
        }
        method = method_by_type.get(content_type)
        file_field = file_field_by_type.get(content_type)
        if not method or not file_field:
            raise RuntimeError(f"Unsupported content type: {content_type}")

        payload = {
            "chat_id": chat_id,
            file_field: file_id,
            "caption": text,
            "caption_entities": entities,
            "reply_markup": reply_markup,
        }
        return self.call(method, payload)["result"]
