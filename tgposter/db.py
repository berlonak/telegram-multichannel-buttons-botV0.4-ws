# copyright by berlonak
# telegram: @Kilax123
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    active_channel_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    chat_id TEXT PRIMARY KEY,
    username TEXT,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    added_by INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_channels_username
ON channels(lower(username))
WHERE username IS NOT NULL AND username != '';

CREATE TABLE IF NOT EXISTS memberships (
    channel_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('publisher', 'manager')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(channel_id, user_id),
    FOREIGN KEY(channel_id) REFERENCES channels(chat_id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);

CREATE TABLE IF NOT EXISTS drafts (
    draft_key TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    file_id TEXT,
    text TEXT NOT NULL,
    entities_json TEXT NOT NULL,
    reply_markup_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_drafts_user ON drafts(user_id);
CREATE INDEX IF NOT EXISTS idx_drafts_expires ON drafts(expires_at);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        cursor = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return cursor

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)).fetchall())

    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.query_one("SELECT value FROM state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.execute(
            """
            INSERT INTO state(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def ensure_user(self, user_id: int) -> None:
        now = int(time.time())
        self.execute(
            """
            INSERT INTO users(user_id, created_at, updated_at) VALUES(?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (int(user_id), now, now),
        )

    def upsert_channel(self, chat: dict[str, Any], added_by: int) -> sqlite3.Row:
        now = int(time.time())
        chat_id = str(chat["id"])
        username = chat.get("username") or None
        title = chat.get("title") or chat.get("first_name") or username or chat_id
        chat_type = chat.get("type", "channel")

        self.ensure_user(added_by)
        self.execute(
            """
            INSERT INTO channels(chat_id, username, title, type, added_by, is_active, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                username = excluded.username,
                title = excluded.title,
                type = excluded.type,
                is_active = 1,
                updated_at = excluded.updated_at
            """,
            (chat_id, username, title, chat_type, int(added_by), now, now),
        )
        self.add_membership(chat_id, int(added_by), "manager")
        return self.get_channel(chat_id)  # type: ignore[return-value]

    def get_channel(self, channel_id: Any) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM channels WHERE chat_id = ? AND is_active = 1", (str(channel_id),))

    def get_channel_by_username(self, username: str) -> Optional[sqlite3.Row]:
        username = username.strip().lstrip("@").lower()
        if not username:
            return None
        return self.query_one(
            "SELECT * FROM channels WHERE lower(username) = ? AND is_active = 1",
            (username,),
        )

    def all_channels(self) -> list[sqlite3.Row]:
        return self.query_all("SELECT * FROM channels WHERE is_active = 1 ORDER BY title COLLATE NOCASE")

    def channels_for_user(self, user_id: int, is_global_owner: bool) -> list[sqlite3.Row]:
        if is_global_owner:
            return self.all_channels()
        return self.query_all(
            """
            SELECT c.*
            FROM channels c
            JOIN memberships m ON m.channel_id = c.chat_id
            WHERE c.is_active = 1 AND m.user_id = ?
            ORDER BY c.title COLLATE NOCASE
            """,
            (int(user_id),),
        )

    def add_membership(self, channel_id: Any, user_id: int, role: str = "publisher") -> None:
        if role not in {"publisher", "manager"}:
            raise ValueError("Unknown role")
        now = int(time.time())
        self.ensure_user(user_id)
        self.execute(
            """
            INSERT INTO memberships(channel_id, user_id, role, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, user_id) DO UPDATE SET
                role = CASE
                    WHEN memberships.role = 'manager' THEN 'manager'
                    ELSE excluded.role
                END,
                updated_at = excluded.updated_at
            """,
            (str(channel_id), int(user_id), role, now, now),
        )

    def set_membership_role(self, channel_id: Any, user_id: int, role: str) -> None:
        if role not in {"publisher", "manager"}:
            raise ValueError("Unknown role")
        now = int(time.time())
        self.ensure_user(user_id)
        self.execute(
            """
            INSERT INTO memberships(channel_id, user_id, role, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(channel_id, user_id) DO UPDATE SET role = excluded.role, updated_at = excluded.updated_at
            """,
            (str(channel_id), int(user_id), role, now, now),
        )

    def remove_membership(self, channel_id: Any, user_id: int) -> None:
        self.execute("DELETE FROM memberships WHERE channel_id = ? AND user_id = ?", (str(channel_id), int(user_id)))
        user = self.query_one("SELECT active_channel_id FROM users WHERE user_id = ?", (int(user_id),))
        if user and str(user["active_channel_id"]) == str(channel_id):
            self.set_active_channel(user_id, None)

    def membership(self, channel_id: Any, user_id: int) -> Optional[sqlite3.Row]:
        return self.query_one(
            "SELECT * FROM memberships WHERE channel_id = ? AND user_id = ?",
            (str(channel_id), int(user_id)),
        )

    def memberships_for_channel(self, channel_id: Any) -> list[sqlite3.Row]:
        return self.query_all(
            "SELECT * FROM memberships WHERE channel_id = ? ORDER BY role DESC, user_id",
            (str(channel_id),),
        )

    def has_access(self, user_id: int, channel_id: Any, is_global_owner: bool) -> bool:
        if is_global_owner:
            return True
        return self.membership(channel_id, user_id) is not None

    def can_manage(self, user_id: int, channel_id: Any, is_global_owner: bool) -> bool:
        if is_global_owner:
            return True
        membership = self.membership(channel_id, user_id)
        return bool(membership and membership["role"] == "manager")

    def set_active_channel(self, user_id: int, channel_id: Optional[Any]) -> None:
        self.ensure_user(user_id)
        now = int(time.time())
        self.execute(
            "UPDATE users SET active_channel_id = ?, updated_at = ? WHERE user_id = ?",
            (None if channel_id is None else str(channel_id), now, int(user_id)),
        )

    def get_active_channel(self, user_id: int, is_global_owner: bool) -> Optional[sqlite3.Row]:
        user = self.query_one("SELECT active_channel_id FROM users WHERE user_id = ?", (int(user_id),))
        if not user or not user["active_channel_id"]:
            return None
        channel = self.get_channel(user["active_channel_id"])
        if not channel:
            return None
        if not self.has_access(user_id, channel["chat_id"], is_global_owner):
            return None
        return channel

    def deactivate_channel(self, channel_id: Any) -> None:
        now = int(time.time())
        self.execute("UPDATE channels SET is_active = 0, updated_at = ? WHERE chat_id = ?", (now, str(channel_id)))
        self.execute("DELETE FROM memberships WHERE channel_id = ?", (str(channel_id),))
        self.execute("UPDATE users SET active_channel_id = NULL WHERE active_channel_id = ?", (str(channel_id),))

    def save_draft(
        self,
        draft_key: str,
        user_id: int,
        content_type: str,
        file_id: Optional[str],
        text: str,
        entities: list[dict[str, Any]],
        reply_markup: dict[str, Any],
        ttl_hours: int,
    ) -> None:
        now = int(time.time())
        self.ensure_user(user_id)
        self.execute(
            """
            INSERT INTO drafts(draft_key, user_id, content_type, file_id, text, entities_json, reply_markup_json, created_at, expires_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(draft_key) DO UPDATE SET
                text = excluded.text,
                entities_json = excluded.entities_json,
                reply_markup_json = excluded.reply_markup_json,
                expires_at = excluded.expires_at
            """,
            (
                draft_key,
                int(user_id),
                content_type,
                file_id,
                text,
                json.dumps(entities, ensure_ascii=False),
                json.dumps(reply_markup, ensure_ascii=False),
                now,
                now + ttl_hours * 3600,
            ),
        )

    def get_draft(self, draft_key: str) -> Optional[dict[str, Any]]:
        row = self.query_one("SELECT * FROM drafts WHERE draft_key = ?", (draft_key,))
        if not row:
            return None
        return {
            "draft_key": row["draft_key"],
            "user_id": int(row["user_id"]),
            "content_type": row["content_type"],
            "file_id": row["file_id"],
            "text": row["text"],
            "entities": json.loads(row["entities_json"]),
            "reply_markup": json.loads(row["reply_markup_json"]),
            "created_at": int(row["created_at"]),
            "expires_at": int(row["expires_at"]),
        }

    def delete_draft(self, draft_key: str) -> None:
        self.execute("DELETE FROM drafts WHERE draft_key = ?", (draft_key,))

    def cleanup_expired_drafts(self) -> int:
        cursor = self.execute("DELETE FROM drafts WHERE expires_at < ?", (int(time.time()),))
        return int(cursor.rowcount or 0)
