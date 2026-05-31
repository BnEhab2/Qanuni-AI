"""
EgyLegalAi — Core Reranker Module
=============================
Cross-encoder reranker using BAAI/bge-reranker-v2-m3.
Re-scores retrieved documents against the user query for higher precision.

Flow:
    ChromaDB/BM25 candidates  →  Reranker  →  Top-K best results

CPU-optimized: lazy model loading, batched inference, configurable top_k.
"""

import time
import threading
from typing import Optional

from config import (
    RERANKER_MODEL,
    RERANKER_TOP_K,
    RERANKER_BATCH_SIZE,
    TORCH_NUM_THREADS,
)


class LegalReranker:
    """
    Cross-encoder reranker for legal document retrieval.

    Lazy-loads the model on first call to avoid slowing down server startup.
    Thread-safe with a lock around initialization.
    """

    def __init__(self, model_name: str = RERANKER_MODEL):
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        """Lazy-load the cross-encoder model on first use."""
        if self._model is not None:
            return

        with self._lock:
            # Double-check after acquiring lock
            if self._model is not None:
                return

            import torch
            torch.set_num_threads(TORCH_NUM_THREADS)

            from sentence_transformers import CrossEncoder

            print(f"[Reranker] Loading cross-encoder model: {self._model_name}")
            t0 = time.time()

            self._model = CrossEncoder(
                self._model_name,
                max_length=512,
                device="cpu",
            )

            t1 = time.time()
            print(f"[Reranker] Model loaded in {t1 - t0:.2f}s")

    def rerank(
        self,
        query: str,
        documents: list[dict],
        top_k: int = RERANKER_TOP_K,
        score_key: str = "reranker_score",
    ) -> list[dict]:
        """
        Re-rank a list of retrieved documents using cross-encoder scoring.

        Args:
            query:      The user's search query.
            documents:  List of dicts, each must have a 'text' key.
            top_k:      Number of top results to return after reranking.
            score_key:  Key name to store the reranker score in each dict.

        Returns:
            List of top_k documents, sorted by reranker score (highest first).
            Each dict gets an additional `score_key` field with the score.
        """
        if not documents:
            return []

        if len(documents) <= 1:
            for doc in documents:
                doc[score_key] = 1.0
            return documents

        self._ensure_model()

        # Build query-document pairs for cross-encoder
        pairs = [(query, doc["text"][:1500]) for doc in documents]

        # Batched prediction for CPU efficiency
        scores = self._model.predict(
            pairs,
            batch_size=RERANKER_BATCH_SIZE,
            show_progress_bar=False,
        )

        # Attach scores and sort
        for doc, score in zip(documents, scores):
            doc[score_key] = float(score)

        ranked = sorted(documents, key=lambda d: d[score_key], reverse=True)

        return ranked[:top_k]


# ── Module-level singleton ─────────────────────────────────────────────
_reranker_instance: Optional[LegalReranker] = None
_instance_lock = threading.Lock()


def get_reranker() -> LegalReranker:
    """Get or create the module-level reranker singleton."""
    global _reranker_instance
    if _reranker_instance is None:
        with _instance_lock:
            if _reranker_instance is None:
                _reranker_instance = LegalReranker()
    return _reranker_instance
