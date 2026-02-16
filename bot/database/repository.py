from __future__ import annotations

import aiosqlite

from bot.database.models import SCHEMA


class Repository:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        # migrations for existing databases
        cursor = await self._db.execute("PRAGMA table_info(users)")
        user_cols = {row["name"] for row in await cursor.fetchall()}
        if "is_approved" not in user_cols:
            await self._db.execute("ALTER TABLE users ADD COLUMN is_approved BOOLEAN DEFAULT 0")
        cursor = await self._db.execute("PRAGMA table_info(messages)")
        msg_cols = {row["name"] for row in await cursor.fetchall()}
        if "content_type" not in msg_cols:
            await self._db.execute("ALTER TABLE messages ADD COLUMN content_type TEXT DEFAULT 'text'")
        if "image_url" not in msg_cols:
            await self._db.execute("ALTER TABLE messages ADD COLUMN image_url TEXT")
        # migrate api_usage CHECK constraint to include vision/tts
        cursor = await self._db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='api_usage'"
        )
        row = await cursor.fetchone()
        if row and "vision" not in row["sql"]:
            await self._db.executescript("""
                ALTER TABLE api_usage RENAME TO api_usage_old;
                CREATE TABLE api_usage (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL REFERENCES users(id),
                    type       TEXT NOT NULL CHECK(type IN ('chat','image','stt','vision','tts')),
                    model      TEXT,
                    tokens_used INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO api_usage SELECT * FROM api_usage_old;
                DROP TABLE api_usage_old;
                CREATE INDEX IF NOT EXISTS idx_api_usage_user ON api_usage(user_id, type);
                CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(user_id, created_at);
            """)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # ── users ──────────────────────────────────────────────

    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        await self._db.execute(
            """INSERT INTO users (id, username, first_name)
               VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name""",
            (user_id, username, first_name),
        )
        await self._db.commit()

    async def get_user(self, user_id: int) -> dict | None:
        cursor = await self._db.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def is_user_approved(self, user_id: int) -> bool:
        cursor = await self._db.execute("SELECT is_approved FROM users WHERE id = ?", (user_id,))
        row = await cursor.fetchone()
        return bool(row and row["is_approved"])

    async def set_user_approved(self, user_id: int, approved: bool) -> None:
        await self._db.execute("UPDATE users SET is_approved = ? WHERE id = ?", (int(approved), user_id))
        await self._db.commit()

    async def get_all_users(self) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT id, username, first_name, is_approved, created_at FROM users ORDER BY created_at DESC"
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_all_users_with_stats(self) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT
                   u.id, u.username, u.first_name, u.is_approved, u.created_at,
                   COALESCE(SUM(a.tokens_used), 0) AS total_tokens,
                   COUNT(a.id) AS total_requests
               FROM users u
               LEFT JOIN api_usage a ON a.user_id = u.id
               GROUP BY u.id
               ORDER BY total_tokens DESC"""
        )
        return [dict(r) for r in await cursor.fetchall()]

    # ── conversations ──────────────────────────────────────

    async def get_active_conversation(self, user_id: int) -> dict | None:
        cursor = await self._db.execute(
            "SELECT * FROM conversations WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_conversations(self, user_id: int, limit: int = 20) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT c.id, c.title, c.model, c.is_active, c.created_at,
                      COUNT(m.id) AS message_count
               FROM conversations c
               LEFT JOIN messages m ON m.conversation_id = c.id
               WHERE c.user_id = ?
               GROUP BY c.id
               ORDER BY c.created_at DESC LIMIT ?""",
            (user_id, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def switch_conversation(self, user_id: int, conv_id: int) -> bool:
        cursor = await self._db.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)
        )
        if not await cursor.fetchone():
            return False
        await self._db.execute(
            "UPDATE conversations SET is_active = 0 WHERE user_id = ? AND is_active = 1", (user_id,)
        )
        await self._db.execute("UPDATE conversations SET is_active = 1 WHERE id = ?", (conv_id,))
        await self._db.commit()
        return True

    async def create_conversation(self, user_id: int, model: str, system_prompt: str = "You are a helpful assistant.") -> int:
        await self._db.execute(
            "UPDATE conversations SET is_active = 0 WHERE user_id = ? AND is_active = 1", (user_id,)
        )
        cursor = await self._db.execute(
            "INSERT INTO conversations (user_id, model, system_prompt) VALUES (?, ?, ?)",
            (user_id, model, system_prompt),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def update_conversation_model(self, conv_id: int, model: str) -> None:
        await self._db.execute("UPDATE conversations SET model = ? WHERE id = ?", (model, conv_id))
        await self._db.commit()

    async def update_conversation_system_prompt(self, conv_id: int, prompt: str) -> None:
        await self._db.execute("UPDATE conversations SET system_prompt = ? WHERE id = ?", (prompt, conv_id))
        await self._db.commit()

    async def set_conversation_title(self, conv_id: int, title: str) -> None:
        await self._db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title, conv_id))
        await self._db.commit()

    # ── messages ───────────────────────────────────────────

    async def add_message(self, conversation_id: int, role: str, content: str,
                          tokens_used: int = 0, content_type: str = "text", image_url: str | None = None) -> int:
        cursor = await self._db.execute(
            "INSERT INTO messages (conversation_id, role, content, tokens_used, content_type, image_url) VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, role, content, tokens_used, content_type, image_url),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_messages(self, conversation_id: int, limit: int = 50) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT role, content, content_type, image_url FROM messages
               WHERE conversation_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (conversation_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]

    async def get_last_assistant_message(self, conversation_id: int) -> dict | None:
        cursor = await self._db.execute(
            """SELECT id, content FROM messages
               WHERE conversation_id = ? AND role = 'assistant'
               ORDER BY created_at DESC LIMIT 1""",
            (conversation_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def delete_last_exchange(self, conversation_id: int) -> bool:
        """Delete the last user+assistant message pair for retry."""
        cursor = await self._db.execute(
            "SELECT id, role FROM messages WHERE conversation_id = ? ORDER BY created_at DESC LIMIT 2",
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return False
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        await self._db.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", ids)
        await self._db.commit()
        return True

    async def get_user_token_usage(self, user_id: int) -> dict:
        cursor = await self._db.execute(
            """SELECT
                   COALESCE(SUM(m.tokens_used), 0) AS total_tokens,
                   COUNT(CASE WHEN m.role = 'user' THEN 1 END) AS user_messages,
                   COUNT(CASE WHEN m.role = 'assistant' THEN 1 END) AS assistant_messages
               FROM messages m
               JOIN conversations c ON c.id = m.conversation_id
               WHERE c.user_id = ?""",
            (user_id,),
        )
        return dict(await cursor.fetchone())

    async def get_user_token_usage_by_conversation(self, user_id: int) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT c.id, c.title, c.model, c.is_active, c.created_at,
                      COALESCE(SUM(m.tokens_used), 0) AS tokens,
                      COUNT(m.id) AS message_count
               FROM conversations c
               LEFT JOIN messages m ON m.conversation_id = c.id
               WHERE c.user_id = ?
               GROUP BY c.id ORDER BY c.created_at DESC LIMIT 10""",
            (user_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_all_messages_for_export(self, conversation_id: int) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_last_messages_formatted(self, conversation_id: int, limit: int = 10) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT role, content, created_at FROM messages
               WHERE conversation_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (conversation_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in reversed(rows)]

    # ── api usage ──────────────────────────────────────────

    async def log_api_usage(self, user_id: int, usage_type: str, model: str, tokens_used: int = 0) -> None:
        await self._db.execute(
            "INSERT INTO api_usage (user_id, type, model, tokens_used) VALUES (?, ?, ?, ?)",
            (user_id, usage_type, model, tokens_used),
        )
        await self._db.commit()

    async def get_api_usage_summary(self, user_id: int) -> list[dict]:
        cursor = await self._db.execute(
            """SELECT type, COUNT(*) AS count, COALESCE(SUM(tokens_used), 0) AS total_tokens
               FROM api_usage WHERE user_id = ? GROUP BY type""",
            (user_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def get_daily_tokens(self, user_id: int) -> int:
        cursor = await self._db.execute(
            """SELECT COALESCE(SUM(tokens_used), 0) AS total
               FROM api_usage
               WHERE user_id = ? AND created_at >= date('now')""",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row["total"]

    async def get_monthly_tokens(self, user_id: int) -> int:
        cursor = await self._db.execute(
            """SELECT COALESCE(SUM(tokens_used), 0) AS total
               FROM api_usage
               WHERE user_id = ? AND created_at >= date('now', 'start of month')""",
            (user_id,),
        )
        row = await cursor.fetchone()
        return row["total"]

    # ── reminders ─────────────────────────────────────────

    async def add_reminder(self, user_id: int, chat_id: int, text: str, remind_at: str) -> int:
        cursor = await self._db.execute(
            "INSERT INTO reminders (user_id, chat_id, text, remind_at) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, text, remind_at),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_pending_reminders(self) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM reminders WHERE sent = 0 AND remind_at <= datetime('now')"
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def mark_reminder_sent(self, reminder_id: int) -> None:
        await self._db.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
        await self._db.commit()

    async def get_user_reminders(self, user_id: int) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM reminders WHERE user_id = ? AND sent = 0 ORDER BY remind_at",
            (user_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def delete_reminder(self, reminder_id: int, user_id: int) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, user_id)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # ── user memory ───────────────────────────────────────

    async def add_user_fact(self, user_id: int, fact: str) -> int:
        cursor = await self._db.execute(
            "INSERT INTO user_memory (user_id, fact) VALUES (?, ?)",
            (user_id, fact),
        )
        await self._db.commit()
        return cursor.lastrowid

    async def get_user_facts(self, user_id: int, limit: int = 50) -> list[dict]:
        cursor = await self._db.execute(
            "SELECT * FROM user_memory WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
        return [dict(r) for r in await cursor.fetchall()]

    async def delete_user_fact(self, fact_id: int, user_id: int) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM user_memory WHERE id = ? AND user_id = ?", (fact_id, user_id)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def clear_user_memory(self, user_id: int) -> int:
        cursor = await self._db.execute(
            "DELETE FROM user_memory WHERE user_id = ?", (user_id,)
        )
        await self._db.commit()
        return cursor.rowcount
