"""
Qanuni.AI — Database Models (SQLite)
=====================================
Lightweight SQLite schema for users, conversations, messages,
and guest usage tracking.

Thread-safe: uses connection-per-call pattern.
"""

import json
import sqlite3
import time
import uuid
import threading
from typing import Optional
from pathlib import Path

from config import BASE_DIR

# Database file path
DB_PATH = str(BASE_DIR / "data" / "qanuni.db")

_db_lock = threading.Lock()
_initialized = False


def get_conn() -> sqlite3.Connection:
    """Create a new SQLite connection for the current thread."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")     # Better concurrent reads
    conn.execute("PRAGMA foreign_keys=ON")       # Enforce FK constraints
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call multiple times."""
    global _initialized
    if _initialized:
        return

    with _db_lock:
        if _initialized:
            return

        # Ensure data directory exists
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

        conn = get_conn()
        try:
            conn.executescript("""
                -- Users table
                CREATE TABLE IF NOT EXISTS users (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    email       TEXT UNIQUE NOT NULL,
                    password    TEXT NOT NULL,
                    name        TEXT DEFAULT '',
                    created_at  REAL NOT NULL,
                    last_login  REAL
                );

                -- Conversations table
                CREATE TABLE IF NOT EXISTS conversations (
                    id          TEXT PRIMARY KEY,
                    user_id     INTEGER,
                    guest_id    TEXT,
                    title       TEXT DEFAULT 'محادثة جديدة',
                    mode        TEXT DEFAULT 'research',
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );

                -- Messages table
                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    sources         TEXT DEFAULT '[]',
                    created_at      REAL NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                );

                -- Guest usage tracking
                CREATE TABLE IF NOT EXISTS guest_usage (
                    guest_id        TEXT PRIMARY KEY,
                    message_count   INTEGER DEFAULT 0,
                    first_seen      REAL NOT NULL,
                    last_seen       REAL NOT NULL
                );

                -- Indexes
                CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
                CREATE INDEX IF NOT EXISTS idx_conv_guest ON conversations(guest_id);
                CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);
                CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
                CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at);
            """)
            conn.commit()
            _initialized = True
            print("[DB] Qanuni database initialized successfully")
        finally:
            conn.close()


# ── User Operations ──────────────────────────────────────────────────

def create_user(email: str, password_hash: str, name: str = "") -> Optional[int]:
    """Create a new user. Returns user ID or None if email exists."""
    conn = get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO users (email, password, name, created_at) VALUES (?, ?, ?, ?)",
            (email.lower().strip(), password_hash, name, time.time()),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_user_by_email(email: str) -> Optional[dict]:
    """Fetch user by email."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> Optional[dict]:
    """Fetch user by ID."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, email, name, created_at, last_login FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_last_login(user_id: int):
    """Update user's last login timestamp."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE users SET last_login = ? WHERE id = ?",
            (time.time(), user_id),
        )
        conn.commit()
    finally:
        conn.close()


# ── Guest Operations ─────────────────────────────────────────────────

def get_guest_usage(guest_id: str) -> dict:
    """Get guest usage stats. Creates entry if not exists."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM guest_usage WHERE guest_id = ?", (guest_id,)
        ).fetchone()

        if row:
            return dict(row)

        # Create new guest entry
        now = time.time()
        conn.execute(
            "INSERT INTO guest_usage (guest_id, message_count, first_seen, last_seen) VALUES (?, 0, ?, ?)",
            (guest_id, now, now),
        )
        conn.commit()
        return {
            "guest_id": guest_id,
            "message_count": 0,
            "first_seen": now,
            "last_seen": now,
        }
    finally:
        conn.close()


def increment_guest_usage(guest_id: str) -> int:
    """Increment guest message count. Returns new count."""
    conn = get_conn()
    try:
        now = time.time()
        conn.execute(
            """INSERT INTO guest_usage (guest_id, message_count, first_seen, last_seen)
               VALUES (?, 1, ?, ?)
               ON CONFLICT(guest_id) DO UPDATE SET
                   message_count = message_count + 1,
                   last_seen = ?""",
            (guest_id, now, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT message_count FROM guest_usage WHERE guest_id = ?", (guest_id,)
        ).fetchone()
        return row["message_count"] if row else 1
    finally:
        conn.close()


# ── Conversation Operations ──────────────────────────────────────────

def create_conversation(
    user_id: Optional[int] = None,
    guest_id: Optional[str] = None,
    title: str = "محادثة جديدة",
    mode: str = "research",
) -> str:
    """Create a new conversation. Returns conversation ID."""
    conv_id = str(uuid.uuid4())
    now = time.time()
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO conversations (id, user_id, guest_id, title, mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (conv_id, user_id, guest_id, title, mode, now, now),
        )
        conn.commit()
        return conv_id
    finally:
        conn.close()


def get_conversations(
    user_id: Optional[int] = None,
    guest_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """List conversations for a user or guest, newest first."""
    conn = get_conn()
    try:
        if user_id:
            rows = conn.execute(
                """SELECT id, title, mode, created_at, updated_at
                   FROM conversations WHERE user_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (user_id, limit),
            ).fetchall()
        elif guest_id:
            rows = conn.execute(
                """SELECT id, title, mode, created_at, updated_at
                   FROM conversations WHERE guest_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (guest_id, limit),
            ).fetchall()
        else:
            return []

        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_conversation(conv_id: str) -> Optional[dict]:
    """Get a single conversation by ID."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_conversation_title(conv_id: str, title: str):
    """Update conversation title."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), conv_id),
        )
        conn.commit()
    finally:
        conn.close()


def touch_conversation(conv_id: str):
    """Update conversation's updated_at timestamp."""
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (time.time(), conv_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_conversation(conv_id: str) -> bool:
    """Delete a conversation and all its messages (CASCADE)."""
    conn = get_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conv_id,)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def migrate_guest_to_user(guest_id: str, user_id: int) -> int:
    """
    Migrate all guest conversations to a registered user.
    Returns number of conversations migrated.
    """
    conn = get_conn()
    try:
        cursor = conn.execute(
            "UPDATE conversations SET user_id = ?, guest_id = NULL WHERE guest_id = ?",
            (user_id, guest_id),
        )
        conn.commit()
        count = cursor.rowcount
        if count > 0:
            print(f"[DB] Migrated {count} conversations from guest '{guest_id}' to user {user_id}")
        return count
    finally:
        conn.close()


def search_conversations(
    query: str,
    user_id: Optional[int] = None,
    guest_id: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    """Search conversations by title or message content."""
    conn = get_conn()
    try:
        search_term = f"%{query}%"
        if user_id:
            rows = conn.execute(
                """SELECT DISTINCT c.id, c.title, c.mode, c.created_at, c.updated_at
                   FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.id
                   WHERE c.user_id = ? AND (c.title LIKE ? OR m.content LIKE ?)
                   ORDER BY c.updated_at DESC LIMIT ?""",
                (user_id, search_term, search_term, limit),
            ).fetchall()
        elif guest_id:
            rows = conn.execute(
                """SELECT DISTINCT c.id, c.title, c.mode, c.created_at, c.updated_at
                   FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.id
                   WHERE c.guest_id = ? AND (c.title LIKE ? OR m.content LIKE ?)
                   ORDER BY c.updated_at DESC LIMIT ?""",
                (guest_id, search_term, search_term, limit),
            ).fetchall()
        else:
            return []

        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Message Operations ───────────────────────────────────────────────

def add_message(
    conversation_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
) -> int:
    """Add a message to a conversation. Returns message ID."""
    conn = get_conn()
    try:
        cursor = conn.execute(
            """INSERT INTO messages (conversation_id, role, content, sources, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                conversation_id,
                role,
                content,
                json.dumps(sources or [], ensure_ascii=False),
                time.time(),
            ),
        )
        # Also touch the conversation
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (time.time(), conversation_id),
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_messages(conversation_id: str, limit: int = 200) -> list[dict]:
    """Get all messages for a conversation, oldest first."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT id, role, content, sources, created_at
               FROM messages WHERE conversation_id = ?
               ORDER BY created_at ASC LIMIT ?""",
            (conversation_id, limit),
        ).fetchall()
        result = []
        for r in rows:
            msg = dict(r)
            try:
                msg["sources"] = json.loads(msg["sources"])
            except (json.JSONDecodeError, TypeError):
                msg["sources"] = []
            result.append(msg)
        return result
    finally:
        conn.close()


def get_conversation_message_count(conversation_id: str) -> int:
    """Get the number of messages in a conversation."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return row["cnt"] if row else 0
    finally:
        conn.close()
