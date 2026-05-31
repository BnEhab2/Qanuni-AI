"""
EgyLegalAi — Core Hybrid Retrieval Module
======================================
Combines semantic embeddings (ChromaDB) with BM25 keyword matching
using Reciprocal Rank Fusion (RRF) for robust legal document retrieval.

Features:
    - Semantic search via ChromaDB embeddings
    - BM25 keyword search via rank_bm25
    - Reciprocal Rank Fusion (RRF) for score combination
    - Embedding LRU cache for repeated queries
    - Async-ready architecture

CPU Optimization:
    - LRU cache on embedding calls
    - Single model load at startup
    - Batched operations where possible
"""

import re
import hashlib
import threading
import time
from functools import lru_cache
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


# ── Arabic-aware tokenizer ────────────────────────────────────────────
def _tokenize_arabic(text: str) -> list[str]:
    """
    Simple Arabic-aware tokenizer:
    - Removes diacritics (tashkeel)
    - Splits on whitespace and punctuation
    - Lowercases for consistency
    - Filters very short tokens
    """
    # Remove Arabic diacritics
    diacritics = re.compile(r'[\u0617-\u061A\u064B-\u0652\u0670]')
    text = diacritics.sub('', text)

    # Split on non-alphanumeric characters (keeps Arabic letters)
    tokens = re.split(r'[^\w\u0600-\u06FF]+', text.lower())

    # Filter empty and very short tokens
    return [t for t in tokens if len(t) > 1]


class HybridRetriever:
    """
    Hybrid retrieval engine combining ChromaDB semantic search with BM25.

    Uses Reciprocal Rank Fusion (RRF) to combine results from both methods.
    """

    def __init__(
        self,
        embed_model: Optional[SentenceTransformer] = None,
        chroma_client: Optional[chromadb.ClientAPI] = None,
        collection: Optional[chromadb.Collection] = None,
    ):
        """
        Initialize the hybrid retriever.

        Args:
            embed_model:   Pre-loaded SentenceTransformer (shared with app.py).
            chroma_client: Pre-loaded ChromaDB client (shared with app.py).
            collection:    Pre-loaded ChromaDB collection (shared with app.py).
        """
        self._embed_model = embed_model
        self._chroma_client = chroma_client
        self._collection = collection

        # BM25 index state
        self._bm25 = None
        self._bm25_docs: list[dict] = []
        self._bm25_lock = threading.Lock()
        self._bm25_built = False

    def _ensure_embed_model(self):
        """Lazy-load embedding model if not provided."""
        if self._embed_model is not None:
            return

        import torch
        torch.set_num_threads(TORCH_NUM_THREADS)

        print(f"[HybridRetrieval] Loading embedding model: {EMBEDDING_MODEL}")
        self._embed_model = SentenceTransformer(EMBEDDING_MODEL)

    def _ensure_chroma(self):
        """Lazy-load ChromaDB if not provided."""
        if self._collection is not None:
            return

        print(f"[HybridRetrieval] Connecting to ChromaDB at: {CHROMA_DB_PATH}")
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

            # Disk Cache Setup
            from pathlib import Path
            import pickle
            cache_path = Path(CHROMA_DB_PATH) / "bm25_cache.pkl"

            if cache_path.exists():
                print(f"[HybridRetrieval] Found BM25 disk cache at {cache_path}. Loading...")
                try:
                    t0 = time.time()
                    with open(cache_path, "rb") as f:
                        cache_data = pickle.load(f)

                    if cache_data.get("total_docs") == total:
                        self._bm25 = cache_data["bm25"]
                        self._bm25_docs = cache_data["bm25_docs"]
                        self._bm25_built = True
                        t1 = time.time()
                        print(f"[HybridRetrieval] BM25 index loaded successfully from disk in {t1 - t0:.2f}s (verified {total} docs)")
                        return
                    else:
                        print(f"[HybridRetrieval] BM25 cache document count mismatch ({cache_data.get('total_docs')} vs {total}). Rebuilding...")
                except Exception as e:
                    print(f"[HybridRetrieval] Error loading BM25 cache: {e}. Rebuilding...")

            from rank_bm25 import BM25Okapi

            print("[HybridRetrieval] Building BM25 index from ChromaDB documents...")
            t0 = time.time()

            # Fetch all documents from ChromaDB for BM25 indexing
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

            # Store document info for BM25 result lookup
            self._bm25_docs = []
            tokenized_corpus = []

            for doc_id, doc_text, meta in zip(all_ids, all_docs, all_metas):
                tokens = _tokenize_arabic(doc_text)
                tokenized_corpus.append(tokens)
                self._bm25_docs.append({
                    "id":         doc_id,
                    "text":       doc_text,
                    "law_name":   meta.get("title", ""),
                    "categories": meta.get("category", ""),
                    "source":     meta.get("source", ""),
                    "type":       meta.get("type", ""),
                    "summary":    meta.get("summary", ""),
                    "keywords":   meta.get("keywords", ""),
                })

            self._bm25 = BM25Okapi(tokenized_corpus)
            self._bm25_built = True

            t1 = time.time()
            print(f"[HybridRetrieval] BM25 index built: {len(all_docs)} documents in {t1 - t0:.2f}s")

            # Save built index to disk cache
            try:
                print("[HybridRetrieval] Caching BM25 index to disk...")
                t_save_0 = time.time()
                cache_data = {
                    "total_docs": total,
                    "bm25": self._bm25,
                    "bm25_docs": self._bm25_docs
                }
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cache_path, "wb") as f:
                    pickle.dump(cache_data, f, protocol=pickle.HIGHEST_PROTOCOL)
                t_save_1 = time.time()
                print(f"[HybridRetrieval] BM25 disk cache saved in {t_save_1 - t_save_0:.2f}s")
            except Exception as e:
                print(f"[HybridRetrieval] Error saving BM25 cache: {e}")

    def _encode_query(self, query: str) -> list[float]:
        """Encode query with embedding model (uses cache for repeated queries)."""
        self._ensure_embed_model()
        embedding = self._embed_model.encode(
            query,
            normalize_embeddings=True,
        )
        return embedding.tolist()

    def semantic_search(
        self,
        query: str,
        top_k: int = TOP_K,
        source_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Semantic search over ChromaDB using embedding similarity.
        Same as the original search_laws() but integrated into the retriever.
        """
        self._ensure_chroma()
        query_embedding = self._encode_query(query)

        # Build optional where clause
        where_clause = None
        conditions = []
        if source_filter:
            conditions.append({"source": {"$eq": source_filter}})
        if type_filter:
            conditions.append({"type": {"$eq": type_filter}})
        if category_filter:
            conditions.append({"category": {"$eq": category_filter}})

        if len(conditions) == 1:
            where_clause = conditions[0]
        elif len(conditions) > 1:
            where_clause = {"$and": conditions}

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_clause if where_clause else None,
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

    def bm25_search(self, query: str, top_k: int = BM25_TOP_K) -> list[dict]:
        """
        BM25 keyword search over the document corpus.

        Args:
            query:  The search query.
            top_k:  Number of top results to return.

        Returns:
            List of document dicts with BM25 scores.
        """
        self._build_bm25_index()

        query_tokens = _tokenize_arabic(query)
        if not query_tokens:
            return []

        scores = self._bm25.get_scores(query_tokens)

        # Get top_k indices by score
        top_indices = sorted(
            range(len(scores)),
            key=lambda i: scores[i],
            reverse=True,
        )[:top_k]

        results = []
        for idx in top_indices:
            if scores[idx] > 0:
                doc = self._bm25_docs[idx].copy()
                doc["bm25_score"] = float(scores[idx])
                doc["retrieval_method"] = "bm25"
                results.append(doc)

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
        """
        Hybrid search combining semantic and BM25 using Reciprocal Rank Fusion.

        RRF score = Σ (weight / (k + rank))  where k=60 is a standard constant.

        Args:
            query:           The search query.
            top_k:           Total number of candidates to return.
            semantic_weight: Weight for semantic search scores.
            bm25_weight:     Weight for BM25 search scores.
            source_filter:   Optional metadata filter.
            type_filter:     Optional metadata filter.
            category_filter: Optional metadata filter.

        Returns:
            Fused and de-duplicated results sorted by combined RRF score.
        """
        # Run both searches
        semantic_results = self.semantic_search(
            query,
            top_k=top_k,
            source_filter=source_filter,
            type_filter=type_filter,
            category_filter=category_filter,
        )
        bm25_results = self.bm25_search(query, top_k=top_k)

        # Reciprocal Rank Fusion
        RRF_K = 60  # Standard RRF constant

        # Build score map keyed by text hash (for deduplication)
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

        # Sort by combined RRF score
        fused = sorted(
            score_map.values(),
            key=lambda d: d.get("rrf_score", 0),
            reverse=True,
        )

        return fused[:top_k]


# ── Module-level singleton ─────────────────────────────────────────────
_retriever_instance: Optional[HybridRetriever] = None
_retriever_lock = threading.Lock()


def get_hybrid_retriever(
    embed_model: Optional[SentenceTransformer] = None,
    chroma_client: Optional[chromadb.ClientAPI] = None,
    collection: Optional[chromadb.Collection] = None,
) -> HybridRetriever:
    """Get or create the module-level hybrid retriever singleton."""
    global _retriever_instance
    if _retriever_instance is None:
        with _retriever_lock:
            if _retriever_instance is None:
                _retriever_instance = HybridRetriever(
                    embed_model=embed_model,
                    chroma_client=chroma_client,
                    collection=collection,
                )
    return _retriever_instance
