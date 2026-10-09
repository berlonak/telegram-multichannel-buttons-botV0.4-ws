# copyright by berlonak
# telegram: @Kilax123
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from sqlite3 import Row
from typing import Any, Optional

from .config import Settings
from .db import Database
from .parser import make_reply_markup, parse_post
from .telegram import TelegramAPI

log = logging.getLogger(__name__)


class PostingBot:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.db = Database(settings.database_path)
        self.api = TelegramAPI(settings)

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------
    def answer(self, chat_id: Any, text: str, reply_markup: Optional[dict[str, Any]] = None) -> None:
        self.api.answer_message(chat_id, text, reply_markup)

    def is_owner(self, user_id: int) -> bool:
        return int(user_id) in self.settings.owner_ids

    @staticmethod
    def command_name(text: str) -> str:
        if not text.startswith("/"):
            return ""
        return text.split(maxsplit=1)[0].split("@", 1)[0].lower()

    @staticmethod
    def command_args(text: str) -> str:
        parts = text.split(maxsplit=1)
        return parts[1].strip() if len(parts) > 1 else ""

    @staticmethod
    def normalize_channel_ref(raw: str) -> str:
        ref = raw.strip()
        if not ref:
            return ref
        if ref.lstrip("-").isdigit():
            return ref
        for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
            if ref.startswith(prefix):
                ref = ref[len(prefix):].split("/", 1)[0]
                break
        if not ref.startswith("@"):
            ref = "@" + ref
        return ref

    @staticmethod
    def channel_key(channel_id: Any) -> str:
        return hashlib.sha1(str(channel_id).encode("utf-8")).hexdigest()[:10]

    @staticmethod
    def channel_title(channel: Row | dict[str, Any]) -> str:
        title = channel["title"] or channel["username"] or channel["chat_id"]
        username = channel["username"]
        return f"{title} (@{username})" if username else str(title)

    def resolve_channel_arg(self, user_id: int, value: str) -> Optional[Row]:
        value = value.strip()
        channels = self.db.channels_for_user(user_id, self.is_owner(user_id))
        if not value:
            return None
        if value.isdigit():
            index = int(value) - 1
            if 0 <= index < len(channels):
                return channels[index]
        normalized = self.normalize_channel_ref(value)
        needle = normalized.lstrip("@").lower()
        for channel in channels:
            if value == str(channel["chat_id"]):
                return channel
            if needle == self.channel_key(channel["chat_id"]):
                return channel
            username = (channel["username"] or "").lower()
            if username and needle == username:
                return channel
        return None

    def channel_by_key_for_user(self, user_id: int, key: str) -> Optional[Row]:
        for channel in self.db.channels_for_user(user_id, self.is_owner(user_id)):
            if self.channel_key(channel["chat_id"]) == key:
                return channel
        return None

    def require_access(self, user_id: int) -> bool:
        return bool(self.db.channels_for_user(user_id, self.is_owner(user_id)))

    # ------------------------------------------------------------------
    # Telegram access checks
    # ------------------------------------------------------------------
    def resolve_chat(self, ref: str) -> dict[str, Any]:
        return self.api.call("getChat", {"chat_id": self.normalize_channel_ref(ref)})["result"]

    def get_chat_member(self, chat_id: Any, user_id: Any) -> dict[str, Any]:
        return self.api.call("getChatMember", {"chat_id": chat_id, "user_id": user_id})["result"]

    @staticmethod
    def is_admin(member: dict[str, Any]) -> bool:
        return member.get("status") in {"creator", "administrator"}

    @staticmethod
    def bot_can_post(member: dict[str, Any]) -> bool:
        if member.get("status") == "creator":
            return True
        return member.get("status") == "administrator" and bool(member.get("can_post_messages"))

    def add_or_update_channel(self, user_id: int, ref: str) -> Row:
        if not self.settings.allow_self_add_channels and not self.is_owner(user_id):
            raise RuntimeError("Новые каналы подключает только владелец бота.")

        chat = self.resolve_chat(ref)
        if chat.get("type") != "channel":
            raise RuntimeError("Нужен именно канал. Группы здесь не подключаем.")

        user_member = self.get_chat_member(chat["id"], user_id)
        if not self.is_owner(user_id) and not self.is_admin(user_member):
            raise RuntimeError("Ты должен быть админом этого канала.")

        bot_member = self.get_chat_member(chat["id"], self.api.get_me()["id"])
        if not self.bot_can_post(bot_member):
            raise RuntimeError("Добавь бота админом в канал и дай право публиковать посты.")

        channel = self.db.upsert_channel(chat, user_id)
        self.db.set_active_channel(user_id, channel["chat_id"])
        return channel

    # ------------------------------------------------------------------
    # Keyboards
    # ------------------------------------------------------------------
    def post_controls(self, user_id: int, draft_key: str) -> list[list[dict[str, Any]]]:
        controls: list[list[dict[str, Any]]] = []
        active = self.db.get_active_channel(user_id, self.is_owner(user_id))
        if active:
            label = self.channel_title(active)
            if len(label) > 31:
                label = label[:28] + "..."
            controls.append([
                {
                    "text": f"✅ Опубликовать в {label}",
                    "callback_data": f"pub:{draft_key}:active",
                    "style": "success",
                }
            ])
        controls.append([
            {"text": "📌 Выбрать канал", "callback_data": f"pick:{draft_key}", "style": "primary"},
            {"text": "✖ Отмена", "callback_data": f"cancel:{draft_key}", "style": "danger"},
        ])
        return controls

    def channels_keyboard(self, user_id: int, draft_key: Optional[str] = None) -> Optional[dict[str, Any]]:
        channels = self.db.channels_for_user(user_id, self.is_owner(user_id))
        if not channels:
            return None
        rows: list[list[dict[str, Any]]] = []
        for channel in channels[:40]:
            title = self.channel_title(channel)
            if len(title) > 38:
                title = title[:35] + "..."
            key = self.channel_key(channel["chat_id"])
            prefix = "pub" if draft_key else "use"
            callback_data = f"{prefix}:{draft_key}:{key}" if draft_key else f"use:{key}"
            rows.append([{"text": title, "callback_data": callback_data}])
        return {"inline_keyboard": rows}

    # ------------------------------------------------------------------
    # Texts
    # ------------------------------------------------------------------
    def help_text(self) -> str:
        return (
            "Бот для постинга в каналы с URL-кнопками.\n\n"
            "Пост отправляется так:\n"
            "текст поста\n"
            "---\n"
            "!r Красная - https://example.com\n"
            "!g Зелёная - https://example.com | !b Синяя - https://t.me/example\n\n"
            "Цвета: !r красный, !g зелёный, !b синий. Без префикса — обычная кнопка.\n\n"
            "Команды:\n"
            "/addchannel @channel — подключить канал\n"
            "/channels — мои каналы\n"
            "/usechannel 1 — выбрать активный канал\n"
            "/channel — текущий канал\n"
            "/adduser @channel 123456789 — дать доступ\n"
            "/removeuser @channel 123456789 — убрать доступ\n"
            "/makeadmin @channel 123456789 — сделать менеджером\n"
            "/unmakeadmin @channel 123456789 — снять менеджера\n"
            "/users @channel — пользователи канала\n"
            "/removechannel @channel — отключить канал\n"
            "/myid — показать твой Telegram ID"
        )

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------
    def handle_start(self, chat_id: Any) -> None:
        self.answer(chat_id, self.help_text())

    def handle_myid(self, chat_id: Any, user_id: int) -> None:
        self.db.ensure_user(user_id)
        self.answer(chat_id, f"Твой Telegram ID: {user_id}")

    def handle_addchannel(self, chat_id: Any, user_id: int, args: str) -> None:
        if not args:
            self.answer(chat_id, "Формат: /addchannel @channel")
            return
        try:
            channel = self.add_or_update_channel(user_id, args)
        except Exception as exc:
            self.answer(chat_id, f"Канал не подключён: {exc}")
            return
        self.answer(chat_id, f"Канал подключён: {self.channel_title(channel)}")

    def handle_channels(self, chat_id: Any, user_id: int) -> None:
        channels = self.db.channels_for_user(user_id, self.is_owner(user_id))
        if not channels:
            self.answer(chat_id, "Каналы не подключены. Добавь бота админом в канал и отправь /addchannel @channel")
            return
        active = self.db.get_active_channel(user_id, self.is_owner(user_id))
        active_id = str(active["chat_id"]) if active else None
        lines = ["Каналы:"]
        for index, channel in enumerate(channels, 1):
            mark = " ✅" if str(channel["chat_id"]) == active_id else ""
            lines.append(f"{index}. {self.channel_title(channel)}{mark}")
        self.answer(chat_id, "\n".join(lines), self.channels_keyboard(user_id))

    def handle_channel(self, chat_id: Any, user_id: int) -> None:
        active = self.db.get_active_channel(user_id, self.is_owner(user_id))
        if not active:
            self.answer(chat_id, "Активный канал не выбран. Посмотри список: /channels")
            return
        self.answer(chat_id, f"Активный канал: {self.channel_title(active)}")

    def handle_usechannel(self, chat_id: Any, user_id: int, args: str) -> None:
        if not args:
            self.answer(chat_id, "Формат: /usechannel 1 или /usechannel @channel")
            return
        channel = self.resolve_channel_arg(user_id, args)
        if not channel:
            self.answer(chat_id, "Канал не найден. Посмотри список: /channels")
            return
        self.db.set_active_channel(user_id, channel["chat_id"])
        self.answer(chat_id, f"Активный канал выбран: {self.channel_title(channel)}")

    @staticmethod
    def parse_channel_user_args(args: str) -> Optional[tuple[str, int]]:
        parts = args.split()
        if len(parts) != 2 or not parts[1].isdigit():
            return None
        return parts[0], int(parts[1])

    def handle_adduser(self, chat_id: Any, user_id: int, args: str) -> None:
        parsed = self.parse_channel_user_args(args)
        if not parsed:
            self.answer(chat_id, "Формат: /adduser <канал> <telegram_id>")
            return
        channel_ref, target_user = parsed
        channel = self.resolve_channel_arg(user_id, channel_ref)
        if not channel:
            self.answer(chat_id, "Канал не найден.")
            return
        if not self.db.can_manage(user_id, channel["chat_id"], self.is_owner(user_id)):
            self.answer(chat_id, "Нет прав управлять доступами этого канала.")
            return
        self.db.add_membership(channel["chat_id"], target_user, "publisher")
        self.answer(chat_id, f"Доступ выдан: {target_user} → {self.channel_title(channel)}")

    def handle_removeuser(self, chat_id: Any, user_id: int, args: str) -> None:
        parsed = self.parse_channel_user_args(args)
        if not parsed:
            self.answer(chat_id, "Формат: /removeuser <канал> <telegram_id>")
            return
        channel_ref, target_user = parsed
        channel = self.resolve_channel_arg(user_id, channel_ref)
        if not channel:
            self.answer(chat_id, "Канал не найден.")
            return
        if not self.db.can_manage(user_id, channel["chat_id"], self.is_owner(user_id)):
            self.answer(chat_id, "Нет прав управлять доступами этого канала.")
            return
        target_membership = self.db.membership(channel["chat_id"], target_user)
        if target_membership and target_membership["role"] == "manager" and not self.is_owner(user_id):
            self.answer(chat_id, "Менеджера может убрать только владелец бота.")
            return
        self.db.remove_membership(channel["chat_id"], target_user)
        self.answer(chat_id, f"Доступ убран: {target_user} → {self.channel_title(channel)}")

    def handle_makeadmin(self, chat_id: Any, user_id: int, args: str) -> None:
        parsed = self.parse_channel_user_args(args)
        if not parsed:
            self.answer(chat_id, "Формат: /makeadmin <канал> <telegram_id>")
            return
        channel_ref, target_user = parsed
        channel = self.resolve_channel_arg(user_id, channel_ref)
        if not channel:
            self.answer(chat_id, "Канал не найден.")
            return
        if not self.db.can_manage(user_id, channel["chat_id"], self.is_owner(user_id)):
            self.answer(chat_id, "Нет прав управлять менеджерами этого канала.")
            return
        self.db.set_membership_role(channel["chat_id"], target_user, "manager")
        self.answer(chat_id, f"Менеджер добавлен: {target_user} → {self.channel_title(channel)}")

    def handle_unmakeadmin(self, chat_id: Any, user_id: int, args: str) -> None:
        parsed = self.parse_channel_user_args(args)
        if not parsed:
            self.answer(chat_id, "Формат: /unmakeadmin <канал> <telegram_id>")
            return
        channel_ref, target_user = parsed
        channel = self.resolve_channel_arg(user_id, channel_ref)
        if not channel:
            self.answer(chat_id, "Канал не найден.")
            return
        if not self.db.can_manage(user_id, channel["chat_id"], self.is_owner(user_id)):
            self.answer(chat_id, "Нет прав управлять менеджерами этого канала.")
            return
        if int(target_user) == int(user_id) and not self.is_owner(user_id):
            self.answer(chat_id, "Нельзя снять менеджера с самого себя.")
            return
        self.db.set_membership_role(channel["chat_id"], target_user, "publisher")
        self.answer(chat_id, f"Менеджер снят: {target_user} → {self.channel_title(channel)}")

    def handle_users(self, chat_id: Any, user_id: int, args: str) -> None:
        if not args:
            self.answer(chat_id, "Формат: /users <канал>")
            return
        channel = self.resolve_channel_arg(user_id, args)
        if not channel:
            self.answer(chat_id, "Канал не найден.")
            return
        if not self.db.can_manage(user_id, channel["chat_id"], self.is_owner(user_id)):
            self.answer(chat_id, "Нет прав смотреть пользователей этого канала.")
            return
        lines = [f"Пользователи {self.channel_title(channel)}:"]
        for membership in self.db.memberships_for_channel(channel["chat_id"]):
            role = "менеджер" if membership["role"] == "manager" else "публикация"
            lines.append(f"• {membership['user_id']} — {role}")
        self.answer(chat_id, "\n".join(lines))

    def handle_removechannel(self, chat_id: Any, user_id: int, args: str) -> None:
        if not args:
            self.answer(chat_id, "Формат: /removechannel <канал>")
            return
        channel = self.resolve_channel_arg(user_id, args)
        if not channel:
            self.answer(chat_id, "Канал не найден.")
            return
        if not self.db.can_manage(user_id, channel["chat_id"], self.is_owner(user_id)):
            self.answer(chat_id, "Нет прав отключить этот канал.")
            return
        self.db.deactivate_channel(channel["chat_id"])
        self.answer(chat_id, f"Канал отключён: {self.channel_title(channel)}")

    def handle_refreshchannel(self, chat_id: Any, user_id: int, args: str) -> None:
        if not args:
            self.answer(chat_id, "Формат: /refreshchannel <канал>")
            return
        channel = self.resolve_channel_arg(user_id, args)
        if not channel:
            self.answer(chat_id, "Канал не найден.")
            return
        try:
            refreshed = self.add_or_update_channel(user_id, str(channel["chat_id"]))
        except Exception as exc:
            self.answer(chat_id, f"Не обновил канал: {exc}")
            return
        self.answer(chat_id, f"Канал обновлён: {self.channel_title(refreshed)}")

    # ------------------------------------------------------------------
    # Posting flow
    # ------------------------------------------------------------------
    def handle_post_message(self, message: dict[str, Any]) -> None:
        chat_id = message["chat"]["id"]
        user_id = int(message.get("from", {}).get("id", 0))
        self.db.ensure_user(user_id)

        if not self.require_access(user_id):
            self.answer(
                chat_id,
                "Нет доступных каналов.\n\n"
                "Если канал твой: добавь бота админом в канал и отправь /addchannel @channel.\n"
                "Если канал уже подключён: попроси менеджера добавить тебя через /adduser.",
            )
            return

        try:
            parsed = parse_post(message)
            reply_markup = make_reply_markup(parsed.buttons)
            draft_key = secrets.token_urlsafe(6)
            self.db.save_draft(
                draft_key=draft_key,
                user_id=user_id,
                content_type=parsed.content.type,
                file_id=parsed.content.file_id,
                text=parsed.text,
                entities=parsed.entities,
                reply_markup=reply_markup,
                ttl_hours=self.settings.preview_ttl_hours,
            )
            preview_markup = make_reply_markup(parsed.buttons, self.post_controls(user_id, draft_key))
            self.api.send_content(
                chat_id=chat_id,
                content_type=parsed.content.type,
                text=parsed.text,
                reply_markup=preview_markup,
                file_id=parsed.content.file_id,
                entities=parsed.entities,
            )
        except Exception as exc:
            self.answer(chat_id, f"Пост не собран: {exc}")

    def publish_draft(self, user_id: int, draft_key: str, channel_id: Optional[Any] = None) -> str:
        draft = self.db.get_draft(draft_key)
        if not draft:
            raise RuntimeError("Черновик не найден. Отправь пост заново.")
        if int(draft["user_id"]) != int(user_id):
            raise RuntimeError("Этот черновик создан другим пользователем.")
        if int(draft["expires_at"]) < int(time.time()):
            self.db.delete_draft(draft_key)
            raise RuntimeError("Черновик устарел. Отправь пост заново.")

        if channel_id is None:
            channel = self.db.get_active_channel(user_id, self.is_owner(user_id))
            if not channel:
                raise RuntimeError("Активный канал не выбран.")
        else:
            channel = self.db.get_channel(channel_id)
            if not channel or not self.db.has_access(user_id, channel["chat_id"], self.is_owner(user_id)):
                raise RuntimeError("Нет доступа к выбранному каналу.")

        self.api.send_content(
            chat_id=channel["chat_id"],
            content_type=draft["content_type"],
            text=draft["text"],
            reply_markup=draft["reply_markup"],
            file_id=draft["file_id"],
            entities=draft["entities"],
        )
        self.db.delete_draft(draft_key)
        return self.channel_title(channel)

    # ------------------------------------------------------------------
    # Update routing
    # ------------------------------------------------------------------
    def handle_message(self, message: dict[str, Any]) -> None:
        chat = message.get("chat", {})
        if chat.get("type") != "private":
            return

        chat_id = chat["id"]
        user_id = int(message.get("from", {}).get("id", 0))
        text = message.get("text", "")
        command = self.command_name(text)
        args = self.command_args(text)

        if command:
            self.db.ensure_user(user_id)
            handlers = {
                "/start": lambda: self.handle_start(chat_id),
                "/help": lambda: self.handle_start(chat_id),
                "/myid": lambda: self.handle_myid(chat_id, user_id),
                "/addchannel": lambda: self.handle_addchannel(chat_id, user_id, args),
                "/refreshchannel": lambda: self.handle_refreshchannel(chat_id, user_id, args),
                "/channels": lambda: self.handle_channels(chat_id, user_id),
                "/channel": lambda: self.handle_channel(chat_id, user_id),
                "/usechannel": lambda: self.handle_usechannel(chat_id, user_id, args),
                "/adduser": lambda: self.handle_adduser(chat_id, user_id, args),
                "/removeuser": lambda: self.handle_removeuser(chat_id, user_id, args),
                "/makeadmin": lambda: self.handle_makeadmin(chat_id, user_id, args),
                "/unmakeadmin": lambda: self.handle_unmakeadmin(chat_id, user_id, args),
                "/users": lambda: self.handle_users(chat_id, user_id, args),
                "/removechannel": lambda: self.handle_removechannel(chat_id, user_id, args),
            }
            handler = handlers.get(command)
            if handler:
                handler()
            else:
                self.answer(chat_id, "Команда неизвестна. Список команд: /help")
            return

        self.handle_post_message(message)

    def handle_callback(self, callback: dict[str, Any]) -> None:
        callback_id = callback["id"]
        message = callback.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        user_id = int(callback.get("from", {}).get("id", 0))
        data = callback.get("data", "")
        self.db.ensure_user(user_id)

        try:
            if data.startswith("use:"):
                key = data.split(":", 1)[1]
                channel = self.channel_by_key_for_user(user_id, key)
                if not channel:
                    raise RuntimeError("Канал не найден.")
                self.db.set_active_channel(user_id, channel["chat_id"])
                self.api.answer_callback(callback_id, "Канал выбран")
                if chat_id:
                    self.answer(chat_id, f"Активный канал: {self.channel_title(channel)}")
                return

            if data.startswith("pick:"):
                draft_key = data.split(":", 1)[1]
                markup = self.channels_keyboard(user_id, draft_key)
                self.api.answer_callback(callback_id)
                if chat_id:
                    self.answer(chat_id, "Куда публикуем?", markup)
                return

            if data.startswith("cancel:"):
                draft_key = data.split(":", 1)[1]
                draft = self.db.get_draft(draft_key)
                if draft and int(draft["user_id"]) == int(user_id):
                    self.db.delete_draft(draft_key)
                self.api.answer_callback(callback_id, "Отменено")
                if chat_id:
                    self.answer(chat_id, "Черновик удалён.")
                return

            if data.startswith("pub:"):
                parts = data.split(":")
                if len(parts) != 3:
                    raise RuntimeError("Некорректная кнопка публикации.")
                draft_key, target = parts[1], parts[2]
                channel_id: Optional[str] = None
                if target != "active":
                    channel = self.channel_by_key_for_user(user_id, target)
                    if not channel:
                        raise RuntimeError("Канал не найден.")
                    channel_id = channel["chat_id"]
                title = self.publish_draft(user_id, draft_key, channel_id)
                self.api.answer_callback(callback_id, "Опубликовано")
                if chat_id:
                    self.answer(chat_id, f"Опубликовано в {title}.")
                return

            self.api.answer_callback(callback_id, "Кнопка устарела", show_alert=True)
        except Exception as exc:
            self.api.answer_callback(callback_id, str(exc), show_alert=True)

    def handle_update(self, update: dict[str, Any]) -> None:
        if "message" in update:
            self.handle_message(update["message"])
        elif "callback_query" in update:
            self.handle_callback(update["callback_query"])

    def run(self) -> None:
        me = self.api.get_me()
        log.info("Bot started as @%s", me.get("username", me.get("id")))
        self.db.cleanup_expired_drafts()

        offset = int(self.db.get_state("update_offset", "0") or "0")
        last_cleanup = time.time()

        while True:
            try:
                updates = self.api.get_updates(offset=offset, timeout=self.settings.poll_timeout)
                for update in updates:
                    update_id = int(update["update_id"])
                    try:
                        self.handle_update(update)
                    except Exception:
                        log.exception("Unhandled update error")
                    offset = update_id + 1
                    self.db.set_state("update_offset", str(offset))

                if time.time() - last_cleanup > 3600:
                    deleted = self.db.cleanup_expired_drafts()
                    if deleted:
                        log.info("Expired drafts deleted: %s", deleted)
                    last_cleanup = time.time()
            except KeyboardInterrupt:
                log.info("Bot stopped")
                return
            except Exception:
                log.exception("Polling error")
                time.sleep(3)
