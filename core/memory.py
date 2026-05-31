"""
EgyLegalAi — Core Case Memory Module (SQLite)
==========================================
Persistent storage for completed legal case analyses.

Features:
    - save_case_memory()       — store a completed analysis
    - retrieve_similar_cases() — find past cases by embedding similarity
    - summarize_case()         — get a structured summary of a stored case

Schema:
    id, case_summary, retrieved_laws (JSON), reasoning, category,
    user_query, query_embedding (BLOB), created_at
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


def _serialize_embedding(embedding: list[float]) -> bytes:
    """Pack a float list into compact binary format."""
    return struct.pack(f"{len(embedding)}f", *embedding)


def _deserialize_embedding(data: bytes) -> list[float]:
    """Unpack binary embedding back to float list."""
    count = len(data) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{count}f", data))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    dot = np.dot(a_arr, b_arr)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(dot / (norm_a * norm_b))


class CaseMemoryStore:
    """
    Thread-safe SQLite case memory with embedding-based similarity search.

    Uses a connection-per-call pattern for thread safety (SQLite connections
    are not safe to share across threads).
    """

    def __init__(self, db_path: str = CASE_MEMORY_DB_PATH):
        self._db_path = db_path
        self._embed_model = None
        self._embed_lock = threading.Lock()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """Create a new connection for the current thread."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create the case_memory table if it doesn't exist."""
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
                CREATE INDEX IF NOT EXISTS idx_case_memory_category
                ON case_memory(category)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_case_memory_created_at
                ON case_memory(created_at)
            """)
            conn.commit()
        finally:
            conn.close()

    def _get_embed_model(self):
        """Lazy-load the embedding model for query encoding."""
        if self._embed_model is not None:
            return self._embed_model

        with self._embed_lock:
            if self._embed_model is not None:
                return self._embed_model

            import torch
            torch.set_num_threads(TORCH_NUM_THREADS)

            from sentence_transformers import SentenceTransformer
            print(f"[CaseMemory] Loading embedding model: {EMBEDDING_MODEL}")
            self._embed_model = SentenceTransformer(EMBEDDING_MODEL)
            return self._embed_model

    def _encode_query(self, text: str) -> list[float]:
        """Encode a text string into an embedding vector."""
        model = self._get_embed_model()
        embedding = model.encode(
            text,
            normalize_embeddings=True,
        )
        return embedding.tolist()

    def save_case_memory(
        self,
        user_query: str,
        case_summary: str,
        retrieved_laws: list[dict],
        reasoning: str,
        category: str = "other",
    ) -> int:
        """
        Store a completed case analysis in the database.

        Args:
            user_query:     The original user question.
            case_summary:   LLM-generated summary of the case.
            retrieved_laws: List of law dicts used in the analysis.
            reasoning:      The full reasoning/analysis text.
            category:       Legal domain category.

        Returns:
            The ID of the inserted row.
        """
        query_embedding = self._encode_query(user_query)
        embedding_blob = _serialize_embedding(query_embedding)

        # Sanitize laws for JSON storage (keep only serializable fields)
        laws_clean = []
        for law in retrieved_laws:
            laws_clean.append({
                "law_name":   law.get("law_name", ""),
                "categories": law.get("categories", ""),
                "source":     law.get("source", ""),
                "type":       law.get("type", ""),
                "snippet":    law.get("text", "")[:500],
            })

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
                    json.dumps(laws_clean, ensure_ascii=False),
                    reasoning,
                    category,
                    embedding_blob,
                    time.time(),
                ),
            )
            conn.commit()
            case_id = cursor.lastrowid
            print(f"[CaseMemory] Saved case #{case_id} — category: {category}")
            return case_id
        finally:
            conn.close()

    def retrieve_similar_cases(
        self,
        query: str,
        top_k: int = CASE_MEMORY_SIMILAR_TOP_K,
        category_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Find the most similar past cases using embedding cosine similarity.

        Args:
            query:           The search query text.
            top_k:           Maximum number of similar cases to return.
            category_filter: Optional category to filter by.

        Returns:
            List of case dicts sorted by similarity (highest first).
        """
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

        # Score each case by cosine similarity
        scored = []
        for row in rows:
            if row["query_embedding"]:
                stored_emb = _deserialize_embedding(row["query_embedding"])
                sim = _cosine_similarity(query_embedding, stored_emb)
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

        # Sort by similarity descending
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_k]

    def summarize_case(self, case_id: int) -> Optional[dict]:
        """
        Return a structured summary of a stored case.

        Args:
            case_id: The database ID of the case.

        Returns:
            Dict with case details, or None if not found.
        """
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
        """Return the N most recent cases."""
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


# ── Module-level singleton ─────────────────────────────────────────────
_store_instance: Optional[CaseMemoryStore] = None
_store_lock = threading.Lock()


def get_case_memory() -> CaseMemoryStore:
    """Get or create the module-level case memory singleton."""
    global _store_instance
    if _store_instance is None:
        with _store_lock:
            if _store_instance is None:
                _store_instance = CaseMemoryStore()
    return _store_instance
