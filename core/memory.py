"""
Qanuni.AI — Case Memory (SQLite)
Stores completed legal analyses for future reference and similarity search.
"""

import json
import sqlite3
import struct
import time
import threading
import numpy as np
from typing import Optional

from config import (
    CASE_MEMORY_DB_PATH,
    CASE_MEMORY_SIMILAR_TOP_K,
    EMBEDDING_MODEL,
    EMBEDDING_CACHE_SIZE,
    TORCH_NUM_THREADS,
)


def _pack_embedding(embedding: list[float]) -> bytes:
    """Pack float list into binary."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _unpack_embedding(data: bytes) -> list[float]:
    """Unpack binary back to float list."""
    count = len(data) // 4
    return list(struct.unpack(f"{count}f", data))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors."""
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    dot = np.dot(a_arr, b_arr)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


class CaseMemoryStore:
    """SQLite-based case memory with embedding similarity search."""

    def __init__(self, db_path: str = CASE_MEMORY_DB_PATH):
        self._db_path = db_path
        self._embed_model = None
        self._embed_lock = threading.Lock()
        self._init_db()

    # Use property so app.py can inject the shared model
    @property
    def embed_model(self):
        return self._embed_model

    @embed_model.setter
    def embed_model(self, model):
        self._embed_model = model

    def _get_conn(self) -> sqlite3.Connection:
        """New connection per call (thread-safe)."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create table if needed."""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS case_memory (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_query      TEXT NOT NULL,
                    case_summary    TEXT NOT NULL,
                    retrieved_laws  TEXT NOT NULL DEFAULT '[]',
                    reasoning       TEXT NOT NULL DEFAULT '',
                    category        TEXT NOT NULL DEFAULT 'other',
                    query_embedding BLOB,
                    created_at      REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_case_category
                ON case_memory(category)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_case_created
                ON case_memory(created_at)
            """)
            conn.commit()
        finally:
            conn.close()

    def _get_embed_model(self):
        """Load embedding model if not already set."""
        if self._embed_model is not None:
            return self._embed_model

        with self._embed_lock:
            if self._embed_model is not None:
                return self._embed_model

            import torch
            torch.set_num_threads(TORCH_NUM_THREADS)

            from sentence_transformers import SentenceTransformer
            print(f"[CaseMemory] Loading model: {EMBEDDING_MODEL}")
            self._embed_model = SentenceTransformer(EMBEDDING_MODEL)
            return self._embed_model

    def _encode_query(self, text: str) -> list[float]:
        """Encode text into embedding vector."""
        model = self._get_embed_model()
        return model.encode(text, normalize_embeddings=True).tolist()

    def save_case_memory(
        self,
        user_query: str,
        case_summary: str,
        retrieved_laws: list[dict],
        reasoning: str,
        category: str = "other",
    ) -> int:
        """Save a completed case analysis. Returns the row ID."""
        embedding = self._encode_query(user_query)
        blob = _pack_embedding(embedding)

        # Keep only serializable fields
        clean_laws = [
            {
                "law_name":   law.get("law_name", ""),
                "categories": law.get("categories", ""),
                "source":     law.get("source", ""),
                "type":       law.get("type", ""),
                "snippet":    law.get("text", "")[:500],
            }
            for law in retrieved_laws
        ]

        conn = self._get_conn()
        try:
            cursor = conn.execute(
                """
                INSERT INTO case_memory
                    (user_query, case_summary, retrieved_laws, reasoning,
                     category, query_embedding, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_query,
                    case_summary,
                    json.dumps(clean_laws, ensure_ascii=False),
                    reasoning,
                    category,
                    blob,
                    time.time(),
                ),
            )
            conn.commit()
            case_id = cursor.lastrowid
            print(f"[CaseMemory] Saved case #{case_id} — {category}")
            return case_id
        finally:
            conn.close()

    def retrieve_similar_cases(
        self,
        query: str,
        top_k: int = CASE_MEMORY_SIMILAR_TOP_K,
        category_filter: Optional[str] = None,
    ) -> list[dict]:
        """Find similar past cases by embedding cosine similarity."""
        query_embedding = self._encode_query(query)

        conn = self._get_conn()
        try:
            if category_filter:
                rows = conn.execute(
                    "SELECT * FROM case_memory WHERE category = ? ORDER BY created_at DESC",
                    (category_filter,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM case_memory ORDER BY created_at DESC"
                ).fetchall()
        finally:
            conn.close()

        if not rows:
            return []

        scored = []
        for row in rows:
            if row["query_embedding"]:
                stored = _unpack_embedding(row["query_embedding"])
                sim = _cosine_similarity(query_embedding, stored)
            else:
                sim = 0.0

            scored.append({
                "id":             row["id"],
                "user_query":     row["user_query"],
                "case_summary":   row["case_summary"],
                "retrieved_laws": json.loads(row["retrieved_laws"]),
                "reasoning":      row["reasoning"],
                "category":       row["category"],
                "created_at":     row["created_at"],
                "similarity":     sim,
            })

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]

    def summarize_case(self, case_id: int) -> Optional[dict]:
        """Get a stored case by ID."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM case_memory WHERE id = ?", (case_id,)
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return None

        return {
            "id":             row["id"],
            "user_query":     row["user_query"],
            "case_summary":   row["case_summary"],
            "retrieved_laws": json.loads(row["retrieved_laws"]),
            "reasoning":      row["reasoning"],
            "category":       row["category"],
            "created_at":     row["created_at"],
        }

    def get_recent_cases(self, limit: int = 10) -> list[dict]:
        """Get the N most recent cases."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM case_memory ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        finally:
            conn.close()

        return [
            {
                "id":           row["id"],
                "user_query":   row["user_query"],
                "case_summary": row["case_summary"],
                "category":     row["category"],
                "created_at":   row["created_at"],
            }
            for row in rows
        ]


# Singleton
_store: Optional[CaseMemoryStore] = None
_lock = threading.Lock()


def get_case_memory() -> CaseMemoryStore:
    """Get or create the case memory singleton."""
    global _store
    if _store is None:
        with _lock:
            if _store is None:
                _store = CaseMemoryStore()
    return _store
