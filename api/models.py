"""
Qanuni.AI — Database Models (PostgreSQL & SQLite)
Users, conversations, messages, guest tracking, and Case Management System.
"""

import json
import sqlite3
import time
import uuid
import os
import threading
from typing import Optional, List, Dict, Any
from pathlib import Path

from config import BASE_DIR

DB_PATH = str(BASE_DIR / "data" / "qanuni.db")

# Database Dialect Detection
DATABASE_URL = os.getenv("DATABASE_URL", "")
IS_POSTGRES = DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")

_db_lock = threading.Lock()
_initialized = False
_pg_pool = None
_pg_pool_lock = threading.Lock()


def get_pg_pool():
    """Lazy initialization of PostgreSQL connection pool."""
    global _pg_pool
    if not IS_POSTGRES:
        return None
    if _pg_pool is None:
        with _pg_pool_lock:
            if _pg_pool is None:
                try:
                    from psycopg2.pool import ThreadedConnectionPool
                    # Create a pool with 1 to 20 connections
                    _pg_pool = ThreadedConnectionPool(1, 20, dsn=DATABASE_URL)
                    print("[DB] PostgreSQL thread-safe pool initialized")
                except Exception as e:
                    print(f"[DB] Error initializing PostgreSQL pool: {e}")
    return _pg_pool


class DBConnection:
    """Unified context manager wrapping SQLite or PostgreSQL connection."""

    def __init__(self):
        self.is_postgres = IS_POSTGRES
        self.conn = None
        self.cursor = None
        self._pool = get_pg_pool()

    def __enter__(self):
        if self.is_postgres:
            if self._pool:
                self.conn = self._pool.getconn()
            else:
                import psycopg2
                self.conn = psycopg2.connect(DATABASE_URL)
        else:
            self.conn = sqlite3.connect(DB_PATH)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.execute("PRAGMA foreign_keys=ON")
        
        self.cursor = self.conn.cursor()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            try:
                if exc_type is None:
                    self.conn.commit()
                else:
                    self.conn.rollback()
            except Exception:
                pass

            if self.is_postgres:
                if self._pool:
                    self._pool.putconn(self.conn)
                else:
                    self.conn.close()
            else:
                self.conn.close()

    def execute(self, sql: str, params: tuple = ()):
        """Execute a query, converting placeholders from SQLite (?) to PostgreSQL (%s) if needed."""
        if self.is_postgres:
            # Standard conversion: convert ? to %s for parameters
            sql_conv = sql.replace("?", "%s")
            
            # SQLite-specific INSERT OR IGNORE translation
            if "INSERT OR IGNORE" in sql_conv:
                sql_conv = sql_conv.replace("INSERT OR IGNORE", "INSERT")
                if "INTO users" in sql_conv:
                    sql_conv += " ON CONFLICT (email) DO NOTHING"
            
            # SQLite ON CONFLICT sets set for increments
            if "ON CONFLICT(guest_id) DO UPDATE" in sql_conv:
                sql_conv = sql_conv.replace(
                    "ON CONFLICT(guest_id) DO UPDATE SET message_count = message_count + 1, last_seen = %s",
                    "ON CONFLICT(guest_id) DO UPDATE SET message_count = guest_usage.message_count + 1, last_seen = EXCLUDED.last_seen"
                )
                # Omit the last redundant parameter
                params = params[:-1]
                
            self.cursor.execute(sql_conv, params)
        else:
            if ";" in sql and not params:
                self.cursor.executescript(sql)
            else:
                self.cursor.execute(sql, params)
        return self.cursor

    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        self.execute(sql, params)
        if self.is_postgres:
            if not self.cursor.description:
                return []
            colnames = [desc[0] for desc in self.cursor.description]
            return [dict(zip(colnames, row)) for row in self.cursor.fetchall()]
        else:
            return [dict(r) for r in self.cursor.fetchall()]

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        self.execute(sql, params)
        row = self.cursor.fetchone()
        if not row:
            return None
        if self.is_postgres:
            colnames = [desc[0] for desc in self.cursor.description]
            return dict(zip(colnames, row))
        else:
            return dict(row)


def init_db():
    """Create tables if they don't exist, using appropriate SQL dialect."""
    global _initialized
    if _initialized:
        return

    with _db_lock:
        if _initialized:
            return

        if not IS_POSTGRES:
            Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)

        with DBConnection() as db:
            if IS_POSTGRES:
                # PostgreSQL DDL
                db.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id          SERIAL PRIMARY KEY,
                        email       VARCHAR(255) UNIQUE NOT NULL,
                        password    VARCHAR(255) NOT NULL,
                        name        VARCHAR(255) DEFAULT '',
                        created_at  DOUBLE PRECISION NOT NULL,
                        last_login  DOUBLE PRECISION
                    );

                    CREATE TABLE IF NOT EXISTS conversations (
                        id          VARCHAR(255) PRIMARY KEY,
                        user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
                        guest_id    VARCHAR(255),
                        title       VARCHAR(255) DEFAULT 'محادثة جديدة',
                        mode        VARCHAR(50) DEFAULT 'research',
                        created_at  DOUBLE PRECISION NOT NULL,
                        updated_at  DOUBLE PRECISION NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS messages (
                        id              SERIAL PRIMARY KEY,
                        conversation_id VARCHAR(255) REFERENCES conversations(id) ON DELETE CASCADE,
                        role            VARCHAR(50) NOT NULL,
                        content         TEXT NOT NULL,
                        sources         TEXT DEFAULT '[]',
                        prosecution     TEXT DEFAULT NULL,
                        defense         TEXT DEFAULT NULL,
                        judge_decision  TEXT DEFAULT NULL,
                        created_at      DOUBLE PRECISION NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS guest_usage (
                        guest_id        VARCHAR(255) PRIMARY KEY,
                        message_count   INTEGER DEFAULT 0,
                        first_seen      DOUBLE PRECISION NOT NULL,
                        last_seen       DOUBLE PRECISION NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS cases (
                        id                    VARCHAR(255) PRIMARY KEY,
                        user_id               INTEGER REFERENCES users(id) ON DELETE CASCADE,
                        title                 VARCHAR(255) NOT NULL,
                        description           TEXT,
                        status                VARCHAR(50) DEFAULT 'active',
                        client_name           VARCHAR(255),
                        case_number           VARCHAR(255),
                        court                 VARCHAR(255),
                        category              VARCHAR(100),
                        success_probability   DOUBLE PRECISION DEFAULT 0.0,
                        created_at            DOUBLE PRECISION NOT NULL,
                        updated_at            DOUBLE PRECISION NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS case_attachments (
                        id          VARCHAR(255) PRIMARY KEY,
                        case_id     VARCHAR(255) REFERENCES cases(id) ON DELETE CASCADE,
                        file_name   VARCHAR(255) NOT NULL,
                        file_path   VARCHAR(500) NOT NULL,
                        file_type   VARCHAR(50),
                        uploaded_at DOUBLE PRECISION NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS case_notes (
                        id          VARCHAR(255) PRIMARY KEY,
                        case_id     VARCHAR(255) REFERENCES cases(id) ON DELETE CASCADE,
                        author_id   INTEGER REFERENCES users(id) ON DELETE CASCADE,
                        content     TEXT NOT NULL,
                        created_at  DOUBLE PRECISION NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS case_timeline (
                        id          VARCHAR(255) PRIMARY KEY,
                        case_id     VARCHAR(255) REFERENCES cases(id) ON DELETE CASCADE,
                        event_date  VARCHAR(100) NOT NULL,
                        title       VARCHAR(255) NOT NULL,
                        description TEXT,
                        is_deadline INTEGER DEFAULT 0,
                        created_at  DOUBLE PRECISION NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS case_judgments (
                        id                  VARCHAR(255) PRIMARY KEY,
                        case_id             VARCHAR(255) REFERENCES cases(id) ON DELETE CASCADE,
                        verdict             TEXT,
                        reasoning           TEXT,
                        expected_verdict    TEXT,
                        prosecution_summary TEXT,
                        defense_summary     TEXT,
                        created_at          DOUBLE PRECISION NOT NULL
                    );
                """)
            else:
                # SQLite DDL
                db.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id          INTEGER PRIMARY KEY AUTOINCREMENT,
                        email       TEXT UNIQUE NOT NULL,
                        password    TEXT NOT NULL,
                        name        TEXT DEFAULT '',
                        created_at  REAL NOT NULL,
                        last_login  REAL
                    );

                    CREATE TABLE IF NOT EXISTS conversations (
                        id          TEXT PRIMARY KEY,
                        user_id     INTEGER,
                        guest_id    TEXT,
                        title       TEXT DEFAULT 'محادثة جديدة',
                        mode        TEXT DEFAULT 'research',
                        created_at  REAL NOT NULL,
                        updated_at  REAL NOT NULL,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS messages (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        role            TEXT NOT NULL,
                        content         TEXT NOT NULL,
                        sources         TEXT DEFAULT '[]',
                        prosecution     TEXT DEFAULT NULL,
                        defense         TEXT DEFAULT NULL,
                        judge_decision  TEXT DEFAULT NULL,
                        created_at      REAL NOT NULL,
                        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS guest_usage (
                        guest_id        TEXT PRIMARY KEY,
                        message_count   INTEGER DEFAULT 0,
                        first_seen      REAL NOT NULL,
                        last_seen       REAL NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS cases (
                        id                    TEXT PRIMARY KEY,
                        user_id               INTEGER,
                        title                 TEXT NOT NULL,
                        description           TEXT,
                        status                TEXT DEFAULT 'active',
                        client_name           TEXT,
                        case_number           TEXT,
                        court                 TEXT,
                        category              TEXT,
                        success_probability   REAL DEFAULT 0.0,
                        created_at            REAL NOT NULL,
                        updated_at            REAL NOT NULL,
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS case_attachments (
                        id          TEXT PRIMARY KEY,
                        case_id     TEXT,
                        file_name   TEXT NOT NULL,
                        file_path   TEXT NOT NULL,
                        file_type   TEXT,
                        uploaded_at REAL NOT NULL,
                        FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS case_notes (
                        id          TEXT PRIMARY KEY,
                        case_id     TEXT,
                        author_id   INTEGER,
                        content     TEXT NOT NULL,
                        created_at  REAL NOT NULL,
                        FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE,
                        FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS case_timeline (
                        id          TEXT PRIMARY KEY,
                        case_id     TEXT,
                        event_date  TEXT NOT NULL,
                        title       TEXT NOT NULL,
                        description TEXT,
                        is_deadline INTEGER DEFAULT 0,
                        created_at  REAL NOT NULL,
                        FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS case_judgments (
                        id                  TEXT PRIMARY KEY,
                        case_id             TEXT,
                        verdict             TEXT,
                        reasoning           TEXT,
                        expected_verdict    TEXT,
                        prosecution_summary TEXT,
                        defense_summary     TEXT,
                        created_at          REAL NOT NULL,
                        FOREIGN KEY (case_id) REFERENCES cases(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id);
                    CREATE INDEX IF NOT EXISTS idx_conv_guest ON conversations(guest_id);
                    CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);
                    CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
                    CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at);
                """)
            
            _initialized = True
            print(f"[DB] Database initialized. Target is {'PostgreSQL' if IS_POSTGRES else 'SQLite (WAL)'}")


# ── User Operations ──────────────────────────────────────────────────

def create_user(email: str, password_hash: str, name: str = "") -> Optional[int]:
    """Create a new user. Returns ID or None if email exists."""
    with DBConnection() as db:
        try:
            sql = "INSERT INTO users (email, password, name, created_at) VALUES (?, ?, ?, ?) RETURNING id" if IS_POSTGRES else "INSERT INTO users (email, password, name, created_at) VALUES (?, ?, ?, ?)"
            if IS_POSTGRES:
                db.execute(sql, (email.lower().strip(), password_hash, name, time.time()))
                row = db.cursor.fetchone()
                return row[0] if row else None
            else:
                db.execute(sql, (email.lower().strip(), password_hash, name, time.time()))
                return db.cursor.lastrowid
        except Exception:
            return None


def get_user_by_email(email: str) -> Optional[dict]:
    with DBConnection() as db:
        return db.fetchone("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))


def get_user_by_id(user_id: int) -> Optional[dict]:
    with DBConnection() as db:
        return db.fetchone("SELECT id, email, name, created_at, last_login FROM users WHERE id = ?", (user_id,))


def update_last_login(user_id: int):
    with DBConnection() as db:
        db.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user_id))


# ── Guest Operations ─────────────────────────────────────────────────

def get_guest_usage(guest_id: str) -> dict:
    with DBConnection() as db:
        row = db.fetchone("SELECT * FROM guest_usage WHERE guest_id = ?", (guest_id,))
        if row:
            return row

        now = time.time()
        db.execute(
            "INSERT INTO guest_usage (guest_id, message_count, first_seen, last_seen) VALUES (?, 0, ?, ?)",
            (guest_id, now, now),
        )
        return {"guest_id": guest_id, "message_count": 0, "first_seen": now, "last_seen": now}


def increment_guest_usage(guest_id: str) -> int:
    with DBConnection() as db:
        now = time.time()
        # Custom substitution handled in DBConnection.execute
        db.execute(
            """INSERT INTO guest_usage (guest_id, message_count, first_seen, last_seen)
               VALUES (?, 1, ?, ?)
               ON CONFLICT(guest_id) DO UPDATE SET
                   message_count = message_count + 1,
                   last_seen = ?""",
            (guest_id, now, now, now),
        )
        row = db.fetchone("SELECT message_count FROM guest_usage WHERE guest_id = ?", (guest_id,))
        return row["message_count"] if row else 1


# ── Conversation Operations ──────────────────────────────────────────

def create_conversation(
    user_id: Optional[int] = None,
    guest_id: Optional[str] = None,
    title: str = "محادثة جديدة",
    mode: str = "research",
) -> str:
    conv_id = str(uuid.uuid4())
    now = time.time()
    with DBConnection() as db:
        db.execute(
            """INSERT INTO conversations (id, user_id, guest_id, title, mode, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (conv_id, user_id, guest_id, title, mode, now, now),
        )
        return conv_id


def get_conversations(
    user_id: Optional[int] = None,
    guest_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    with DBConnection() as db:
        if user_id:
            return db.fetchall(
                """SELECT id, title, mode, created_at, updated_at
                   FROM conversations WHERE user_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (user_id, limit),
            )
        elif guest_id:
            return db.fetchall(
                """SELECT id, title, mode, created_at, updated_at
                   FROM conversations WHERE guest_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (guest_id, limit),
            )
        return []


def get_conversation(conv_id: str) -> Optional[dict]:
    with DBConnection() as db:
        return db.fetchone("SELECT * FROM conversations WHERE id = ?", (conv_id,))


def update_conversation_title(conv_id: str, title: str):
    with DBConnection() as db:
        db.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), conv_id),
        )


def touch_conversation(conv_id: str):
    with DBConnection() as db:
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (time.time(), conv_id))


def delete_conversation(conv_id: str) -> bool:
    with DBConnection() as db:
        db.execute("DELETE FROM conversations WHERE id = ?", (conv_id,))
        return db.cursor.rowcount > 0


def migrate_guest_to_user(guest_id: str, user_id: int) -> int:
    with DBConnection() as db:
        db.execute(
            "UPDATE conversations SET user_id = ?, guest_id = NULL WHERE guest_id = ?",
            (user_id, guest_id),
        )
        return db.cursor.rowcount


def search_conversations(
    query: str,
    user_id: Optional[int] = None,
    guest_id: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    term = f"%{query}%"
    with DBConnection() as db:
        if user_id:
            return db.fetchall(
                """SELECT DISTINCT c.id, c.title, c.mode, c.created_at, c.updated_at
                   FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.id
                   WHERE c.user_id = ? AND (c.title LIKE ? OR m.content LIKE ?)
                   ORDER BY c.updated_at DESC LIMIT ?""",
                (user_id, term, term, limit),
            )
        elif guest_id:
            return db.fetchall(
                """SELECT DISTINCT c.id, c.title, c.mode, c.created_at, c.updated_at
                   FROM conversations c
                   LEFT JOIN messages m ON m.conversation_id = c.id
                   WHERE c.guest_id = ? AND (c.title LIKE ? OR m.content LIKE ?)
                   ORDER BY c.updated_at DESC LIMIT ?""",
                (guest_id, term, term, limit),
            )
        return []


# ── Message Operations ───────────────────────────────────────────────

def add_message(
    conversation_id: str,
    role: str,
    content: str,
    sources: list[dict] | None = None,
    prosecution: dict | None = None,
    defense: dict | None = None,
    judge_decision: dict | None = None,
) -> int:
    with DBConnection() as db:
        sql = """INSERT INTO messages (conversation_id, role, content, sources, prosecution, defense, judge_decision, created_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?)"""
        db.execute(
            sql,
            (
                conversation_id,
                role,
                content,
                json.dumps(sources or [], ensure_ascii=False),
                json.dumps(prosecution, ensure_ascii=False) if prosecution else None,
                json.dumps(defense, ensure_ascii=False) if defense else None,
                json.dumps(judge_decision, ensure_ascii=False) if judge_decision else None,
                time.time(),
            ),
        )
        db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (time.time(), conversation_id),
        )
        return db.cursor.lastrowid if not IS_POSTGRES else 0


def get_messages(conversation_id: str, limit: int = 200) -> list[dict]:
    with DBConnection() as db:
        rows = db.fetchall(
            """SELECT id, role, content, sources, prosecution, defense, judge_decision, created_at
               FROM messages WHERE conversation_id = ?
               ORDER BY created_at ASC LIMIT ?""",
            (conversation_id, limit),
        )
        for msg in rows:
            try:
                msg["sources"] = json.loads(msg["sources"]) if msg.get("sources") else []
            except (json.JSONDecodeError, TypeError):
                msg["sources"] = []
            
            for key in ["prosecution", "defense", "judge_decision"]:
                if msg.get(key):
                    try:
                        msg[key] = json.loads(msg[key])
                    except (json.JSONDecodeError, TypeError):
                        msg[key] = {}
                else:
                    msg[key] = {}
        return rows


def get_conversation_message_count(conversation_id: str) -> int:
    with DBConnection() as db:
        row = db.fetchone(
            "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        )
        return row["cnt"] if row else 0


# ── Case Management System (CMS) Operations ─────────────────────────

def create_case(
    user_id: int,
    title: str,
    description: str,
    client_name: str,
    case_number: str,
    court: str,
    category: str,
    success_probability: float = 0.0
) -> str:
    case_id = str(uuid.uuid4())
    now = time.time()
    with DBConnection() as db:
        db.execute(
            """INSERT INTO cases (id, user_id, title, description, status, client_name, case_number, court, category, success_probability, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)""",
            (case_id, user_id, title, description, client_name, case_number, court, category, success_probability, now, now)
        )
        return case_id


def get_cases(
    user_id: int,
    status: Optional[str] = None,
    category: Optional[str] = None,
    search_query: Optional[str] = None
) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM cases WHERE user_id = ?"
    params = [user_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    if category:
        sql += " AND category = ?"
        params.append(category)
    if search_query:
        sql += " AND (title LIKE ? OR description LIKE ? OR client_name LIKE ? OR case_number LIKE ?)"
        term = f"%{search_query}%"
        params.extend([term, term, term, term])
    sql += " ORDER BY updated_at DESC"
    
    with DBConnection() as db:
        return db.fetchall(sql, tuple(params))


def get_case_details(case_id: str) -> Optional[Dict[str, Any]]:
    with DBConnection() as db:
        case = db.fetchone("SELECT * FROM cases WHERE id = ?", (case_id,))
        if not case:
            return None
        
        # Load attachments
        attachments = db.fetchall(
            "SELECT * FROM case_attachments WHERE case_id = ? ORDER BY uploaded_at DESC",
            (case_id,)
        )
        
        # Load notes
        notes = db.fetchall(
            """SELECT n.*, u.name as author_name, u.email as author_email 
               FROM case_notes n 
               LEFT JOIN users u ON n.author_id = u.id 
               WHERE n.case_id = ? 
               ORDER BY n.created_at DESC""",
            (case_id,)
        )
        
        # Load timeline
        timeline = db.fetchall(
            "SELECT * FROM case_timeline WHERE case_id = ? ORDER BY event_date ASC",
            (case_id,)
        )
        
        # Load judgment
        judgment = db.fetchone(
            "SELECT * FROM case_judgments WHERE case_id = ? ORDER BY created_at DESC LIMIT 1",
            (case_id,)
        )
        
        case["attachments"] = attachments
        case["notes"] = notes
        case["timeline"] = timeline
        case["judgment"] = judgment or {}
        
        return case


def update_case_status(case_id: str, status: str):
    with DBConnection() as db:
        db.execute(
            "UPDATE cases SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), case_id)
        )


def update_case_success_probability(case_id: str, prob: float):
    with DBConnection() as db:
        db.execute(
            "UPDATE cases SET success_probability = ?, updated_at = ? WHERE id = ?",
            (prob, time.time(), case_id)
        )


def add_case_note(case_id: str, author_id: int, content: str) -> str:
    note_id = str(uuid.uuid4())
    now = time.time()
    with DBConnection() as db:
        db.execute(
            "INSERT INTO case_notes (id, case_id, author_id, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (note_id, case_id, author_id, content, now)
        )
        db.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        return note_id


def add_case_attachment(case_id: str, file_name: str, file_path: str, file_type: str) -> str:
    att_id = str(uuid.uuid4())
    now = time.time()
    with DBConnection() as db:
        db.execute(
            "INSERT INTO case_attachments (id, case_id, file_name, file_path, file_type, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)",
            (att_id, case_id, file_name, file_path, file_type, now)
        )
        db.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        return att_id


def add_case_timeline_event(case_id: str, event_date: str, title: str, description: str, is_deadline: int = 0) -> str:
    evt_id = str(uuid.uuid4())
    now = time.time()
    with DBConnection() as db:
        db.execute(
            "INSERT INTO case_timeline (id, case_id, event_date, title, description, is_deadline, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (evt_id, case_id, event_date, title, description, is_deadline, now)
        )
        db.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        return evt_id


def save_case_judgment(
    case_id: str,
    verdict: str,
    reasoning: str,
    expected_verdict: str = "",
    prosecution_summary: str = "",
    defense_summary: str = ""
) -> str:
    jd_id = str(uuid.uuid4())
    now = time.time()
    with DBConnection() as db:
        # Check if a judgment already exists
        exists = db.fetchone("SELECT id FROM case_judgments WHERE case_id = ?", (case_id,))
        if exists:
            db.execute(
                """UPDATE case_judgments 
                   SET verdict = ?, reasoning = ?, expected_verdict = ?, prosecution_summary = ?, defense_summary = ?, created_at = ? 
                   WHERE case_id = ?""",
                (verdict, reasoning, expected_verdict, prosecution_summary, defense_summary, now, case_id)
            )
            db.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
            return exists["id"]
        else:
            db.execute(
                """INSERT INTO case_judgments (id, case_id, verdict, reasoning, expected_verdict, prosecution_summary, defense_summary, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (jd_id, case_id, verdict, reasoning, expected_verdict, prosecution_summary, defense_summary, now)
            )
            db.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
            return jd_id


def get_cms_analytics(user_id: int) -> Dict[str, Any]:
    with DBConnection() as db:
        # Counts per status
        status_counts = db.fetchall(
            "SELECT status, COUNT(*) as cnt FROM cases WHERE user_id = ? GROUP BY status",
            (user_id,)
        )
        
        # Counts per category
        category_counts = db.fetchall(
            "SELECT category, COUNT(*) as cnt FROM cases WHERE user_id = ? GROUP BY category",
            (user_id,)
        )
        
        # Monthly averages
        # SQLite vs Postgres extraction of month
        if IS_POSTGRES:
            monthly = db.fetchall(
                """SELECT TO_CHAR(TO_TIMESTAMP(created_at), 'YYYY-MM') as month, COUNT(*) as cnt, AVG(success_probability) as avg_prob
                   FROM cases WHERE user_id = ? GROUP BY month ORDER BY month ASC""",
                (user_id,)
            )
        else:
            monthly = db.fetchall(
                """SELECT strftime('%Y-%m', datetime(created_at, 'unixepoch')) as month, COUNT(*) as cnt, AVG(success_probability) as avg_prob
                   FROM cases WHERE user_id = ? GROUP BY month ORDER BY month ASC""",
                (user_id,)
            )
            
        return {
            "status": {r["status"]: r["cnt"] for r in status_counts},
            "categories": {r["category"]: r["cnt"] for r in category_counts if r["category"]},
            "monthly": monthly
        }


def delete_case(case_id: str) -> bool:
    with DBConnection() as db:
        db.execute("DELETE FROM cases WHERE id = ?", (case_id,))
        return db.cursor.rowcount > 0
