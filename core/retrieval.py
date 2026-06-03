"""
Qanuni.AI — Hybrid Retrieval
Combines semantic search (ChromaDB) with BM25 keyword matching
using Reciprocal Rank Fusion (RRF).
"""

import re
import numpy as np
import hashlib
import threading
import time
from typing import Optional

import chromadb
from sentence_transformers import SentenceTransformer

from config import (
    EMBEDDING_MODEL,
    CHROMA_DB_PATH,
    CHROMA_COLLECTION,
    TOP_K,
    SEMANTIC_WEIGHT,
    BM25_WEIGHT,
    BM25_TOP_K,
    HYBRID_CANDIDATE_K,
    EMBEDDING_CACHE_SIZE,
    TORCH_NUM_THREADS,
)


def _tokenize_arabic(text: str) -> list[str]:
    """Simple Arabic tokenizer: removes diacritics, splits, lowercases."""
    diacritics = re.compile(r'[\u0617-\u061A\u064B-\u0652\u0670]')
    text = diacritics.sub('', text)
    tokens = re.split(r'[^\w\u0600-\u06FF]+', text.lower())
    return [t for t in tokens if len(t) > 1]


class HybridRetriever:
    """Hybrid retrieval: ChromaDB semantic + BM25, fused with RRF."""

    def __init__(
        self,
        embed_model: Optional[SentenceTransformer] = None,
        chroma_client: Optional[chromadb.ClientAPI] = None,
        collection: Optional[chromadb.Collection] = None,
    ):
        self._embed_model = embed_model
        self._chroma_client = chroma_client
        self._collection = collection

        # BM25 state
        self._bm25 = None
        self._bm25_docs: list[dict] = []
        self._bm25_lock = threading.Lock()
        self._bm25_built = False

    def _ensure_embed_model(self):
        """Load embedding model if not provided."""
        if self._embed_model is not None:
            return

        import torch
        torch.set_num_threads(TORCH_NUM_THREADS)

        print(f"[Retrieval] Loading embedding model: {EMBEDDING_MODEL}")
        self._embed_model = SentenceTransformer(EMBEDDING_MODEL)

    def _ensure_chroma(self):
        """Connect to ChromaDB if not provided."""
        if self._collection is not None:
            return

        print(f"[Retrieval] Connecting to ChromaDB at: {CHROMA_DB_PATH}")
        self._chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
        self._collection = self._chroma_client.get_collection(name=CHROMA_COLLECTION)

    def _build_bm25_index(self):
        """Build or load the BM25 index from ChromaDB documents."""
        if self._bm25_built:
            return

        with self._bm25_lock:
            if self._bm25_built:
                return

            self._ensure_chroma()
            total = self._collection.count()

            # Try loading from disk cache first
            from pathlib import Path
            import pickle
            cache_path = Path(CHROMA_DB_PATH) / "bm25_cache.pkl"

            if cache_path.exists():
                print(f"[Retrieval] Loading BM25 cache from disk...")
                try:
                    t0 = time.time()
                    with open(cache_path, "rb") as f:
                        cache = pickle.load(f)

                    if cache.get("total_docs") == total:
                        self._bm25 = cache["bm25"]
                        self._bm25_docs = cache["bm25_docs"]
                        self._bm25_built = True
                        print(f"[Retrieval] BM25 loaded from cache in {time.time() - t0:.2f}s ({total} docs)")
                        return
                    else:
                        print(f"[Retrieval] BM25 cache stale, rebuilding...")
                except Exception as e:
                    print(f"[Retrieval] Cache load error: {e}, rebuilding...")

            from rank_bm25 import BM25Okapi

            print("[Retrieval] Building BM25 index...")
            t0 = time.time()

            # Fetch all documents from ChromaDB
            batch_size = 5000
            all_docs = []
            all_metas = []
            all_ids = []

            for offset in range(0, total, batch_size):
                result = self._collection.get(
                    limit=batch_size,
                    offset=offset,
                    include=["documents", "metadatas"],
                )
                if result["documents"]:
                    all_docs.extend(result["documents"])
                    all_metas.extend(result["metadatas"])
                    all_ids.extend(result["ids"])

            self._bm25_docs = []
            tokenized = []

            for doc_id, doc_text, meta in zip(all_ids, all_docs, all_metas):
                tokens = _tokenize_arabic(doc_text)
                tokenized.append(tokens)
                self._bm25_docs.append({
                    "id":       doc_id,
                    "text":     doc_text,
                    "law_name": meta.get("title", ""),
                    "categories": meta.get("category", ""),
                    "source":   meta.get("source", ""),
                    "type":     meta.get("type", ""),
                    "summary":  meta.get("summary", ""),
                    "keywords": meta.get("keywords", ""),
                })

            self._bm25 = BM25Okapi(tokenized)
            self._bm25_built = True

            print(f"[Retrieval] BM25 built: {len(all_docs)} docs in {time.time() - t0:.2f}s")

            # Save to disk cache
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "wb") as f:
                    pickle.dump({
                        "total_docs": total,
                        "bm25": self._bm25,
                        "bm25_docs": self._bm25_docs,
                    }, f, protocol=pickle.HIGHEST_PROTOCOL)
                print("[Retrieval] BM25 cache saved to disk")
            except Exception as e:
                print(f"[Retrieval] Failed to save cache: {e}")

    def _encode_query(self, query: str) -> list[float]:
        """Encode query text into embedding vector."""
        self._ensure_embed_model()
        embedding = self._embed_model.encode(query, normalize_embeddings=True)
        return embedding.tolist()

    def semantic_search(
        self,
        query: str,
        top_k: int = TOP_K,
        source_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
    ) -> list[dict]:
        """Search ChromaDB using embedding similarity."""
        self._ensure_chroma()
        query_embedding = self._encode_query(query)

        # Build where clause from filters
        conditions = []
        if source_filter:
            conditions.append({"source": {"$eq": source_filter}})
        if type_filter:
            conditions.append({"type": {"$eq": type_filter}})
        if category_filter:
            conditions.append({"category": {"$eq": category_filter}})

        where = None
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        formatted = []
        if results and results["documents"] and results["documents"][0]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                formatted.append({
                    "law_name":   meta.get("title", ""),
                    "categories": meta.get("category", ""),
                    "source":     meta.get("source", ""),
                    "type":       meta.get("type", ""),
                    "summary":    meta.get("summary", ""),
                    "keywords":   meta.get("keywords", ""),
                    "text":       doc,
                    "distance":   dist,
                    "retrieval_method": "semantic",
                })

        return formatted

    def bm25_search(
        self,
        query: str,
        top_k: int = BM25_TOP_K,
        source_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
    ) -> list[dict]:
        """BM25 keyword search over the corpus with metadata filtering."""
        self._build_bm25_index()

        query_tokens = _tokenize_arabic(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)
        scores_arr = np.array(scores, dtype=np.float32)

        # Only consider docs with positive scores
        positive_mask = scores_arr > 0
        if not positive_mask.any():
            return []

        # Fast partial sort: pick top candidates without sorting entire array
        # We ask for more candidates than top_k to accommodate filter rejections
        candidate_k = min(top_k * 5, int(positive_mask.sum()))
        if candidate_k == 0:
            return []

        # argpartition is O(N) vs O(N·logN) for full argsort
        candidate_indices = np.argpartition(scores_arr, -candidate_k)[-candidate_k:]
        # Now sort only the candidates
        candidate_indices = candidate_indices[np.argsort(scores_arr[candidate_indices])[::-1]]

        results = []
        for idx in candidate_indices:
            score_val = float(scores_arr[idx])
            if score_val <= 0:
                break

            doc = self._bm25_docs[idx]

            # Apply metadata filters
            if source_filter and doc.get("source") != source_filter:
                continue
            if type_filter and doc.get("type") != type_filter:
                continue
            if category_filter and doc.get("categories") != category_filter:
                continue

            doc_copy = doc.copy()
            doc_copy["bm25_score"] = score_val
            doc_copy["retrieval_method"] = "bm25"
            results.append(doc_copy)

            if len(results) >= top_k:
                break

        return results

    def hybrid_search(
        self,
        query: str,
        top_k: int = HYBRID_CANDIDATE_K,
        semantic_weight: float = SEMANTIC_WEIGHT,
        bm25_weight: float = BM25_WEIGHT,
        source_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
    ) -> list[dict]:
        """Combine semantic + BM25 results using Reciprocal Rank Fusion."""
        semantic_results = self.semantic_search(
            query, top_k=top_k,
            source_filter=source_filter,
            type_filter=type_filter,
            category_filter=category_filter,
        )
        bm25_results = self.bm25_search(
            query, top_k=top_k,
            source_filter=source_filter,
            type_filter=type_filter,
            category_filter=category_filter,
        )

        RRF_K = 60
        score_map: dict[str, dict] = {}

        for rank, doc in enumerate(semantic_results):
            text_hash = hashlib.md5(doc["text"][:200].encode("utf-8")).hexdigest()
            rrf_score = semantic_weight / (RRF_K + rank + 1)

            if text_hash not in score_map:
                score_map[text_hash] = doc.copy()
                score_map[text_hash]["rrf_score"] = rrf_score
                score_map[text_hash]["retrieval_method"] = "semantic"
            else:
                score_map[text_hash]["rrf_score"] += rrf_score
                score_map[text_hash]["retrieval_method"] = "hybrid"

        for rank, doc in enumerate(bm25_results):
            text_hash = hashlib.md5(doc["text"][:200].encode("utf-8")).hexdigest()
            rrf_score = bm25_weight / (RRF_K + rank + 1)

            if text_hash not in score_map:
                score_map[text_hash] = doc.copy()
                score_map[text_hash]["rrf_score"] = rrf_score
                score_map[text_hash]["retrieval_method"] = "bm25"
            else:
                score_map[text_hash]["rrf_score"] += rrf_score
                score_map[text_hash]["retrieval_method"] = "hybrid"

        fused = sorted(
            score_map.values(),
            key=lambda d: d.get("rrf_score", 0),
            reverse=True,
        )

        return fused[:top_k]


# Singleton
_retriever: Optional[HybridRetriever] = None
_lock = threading.Lock()


def get_hybrid_retriever(
    embed_model: Optional[SentenceTransformer] = None,
    chroma_client: Optional[chromadb.ClientAPI] = None,
    collection: Optional[chromadb.Collection] = None,
) -> HybridRetriever:
    """Get or create the hybrid retriever singleton."""
    global _retriever
    if _retriever is None:
        with _lock:
            if _retriever is None:
                _retriever = HybridRetriever(
                    embed_model=embed_model,
                    chroma_client=chroma_client,
                    collection=collection,
                )
    return _retriever
