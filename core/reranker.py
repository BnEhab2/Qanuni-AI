"""
Qanuni.AI — Core Reranker
Cross-encoder reranker for better search precision.
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
    """Re-scores retrieved documents using a cross-encoder model."""

    def __init__(self, model_name: str = RERANKER_MODEL):
        self._model_name = model_name
        self._model = None
        self._lock = threading.Lock()

    def _ensure_model(self):
        """Load the model on first use."""
        if self._model is not None:
            return

        with self._lock:
            if self._model is not None:
                return

            import torch
            torch.set_num_threads(TORCH_NUM_THREADS)

            from sentence_transformers import CrossEncoder

            print(f"[Reranker] Loading model: {self._model_name}")
            t0 = time.time()

            self._model = CrossEncoder(
                self._model_name,
                max_length=512,
                device="cpu",
            )

            print(f"[Reranker] Model loaded in {time.time() - t0:.2f}s")

    def rerank(
        self,
        query: str,
        documents: list[dict],
        top_k: int = RERANKER_TOP_K,
        score_key: str = "reranker_score",
    ) -> list[dict]:
        """Re-rank documents by relevance to the query. Returns top_k best."""
        if not documents:
            return []

        if len(documents) <= 1:
            for doc in documents:
                doc[score_key] = 1.0
            return documents

        self._ensure_model()

        pairs = [(query, doc["text"][:1500]) for doc in documents]

        scores = self._model.predict(
            pairs,
            batch_size=RERANKER_BATCH_SIZE,
            show_progress_bar=False,
        )

        for doc, score in zip(documents, scores):
            doc[score_key] = float(score)

        ranked = sorted(documents, key=lambda d: d[score_key], reverse=True)
        return ranked[:top_k]


# Singleton
_reranker: Optional[LegalReranker] = None
_lock = threading.Lock()


def get_reranker() -> LegalReranker:
    """Get or create the reranker singleton."""
    global _reranker
    if _reranker is None:
        with _lock:
            if _reranker is None:
                _reranker = LegalReranker()
    return _reranker
