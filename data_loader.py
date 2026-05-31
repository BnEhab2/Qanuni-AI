"""
Qanuni.AI — Data Loader & Ingestion Pipeline
===============================================
Loads both parquet datasets, normalizes schemas, chunks documents,
generates embeddings (BAAI/bge-m3), and stores everything in ChromaDB.

Usage:
    python data_loader.py          # Full ingest (merge → chunk → embed → store)
    python data_loader.py --force  # Wipe existing ChromaDB and rebuild
"""

import os
import sys
import hashlib
import argparse
import pandas as pd
from pathlib import Path
from tqdm import tqdm

# Fix Windows terminal encoding for Arabic output
if sys.platform.startswith("win"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from config import (
    MERGED_CORPUS_PATH,
    SOURCE_CORPUS,
    SOURCE_RAG,
    SOURCE_MANSHURAT,
    EMBEDDING_MODEL,
    CHROMA_DB_PATH,
    CHROMA_COLLECTION,
    CHUNK_SIZE,
    CHUNK_OVERLAP_WORDS,
)


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap_words: int = CHUNK_OVERLAP_WORDS) -> list[str]:
    """Split a long text into overlapping chunks by character count."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    words = text.split()
    current: list[str] = []
    current_len = 0

    for w in words:
        current.append(w)
        current_len += len(w) + 1
        if current_len >= chunk_size:
            chunks.append(" ".join(current))
            # Keep last N words for overlap
            current = current[-overlap_words:]
            current_len = sum(len(x) + 1 for x in current)

    if current:
        chunks.append(" ".join(current))

    return chunks


def build_chunks(merged_df: pd.DataFrame) -> list[dict]:
    """
    Chunk every row of the merged corpus.
    Returns a list of dicts ready for ChromaDB insertion.
    """
    print("\n[*] Chunking documents...")
    all_chunks = []

    for _, row in tqdm(merged_df.iterrows(), total=len(merged_df), desc="Chunking"):
        text = row["text"]
        if not text or len(text.strip()) < 10:
            continue

        text_chunks = chunk_text(text)

        for i, chunk_text_piece in enumerate(text_chunks):
            # Deterministic ID for deduplication / idempotent re-runs
            chunk_id = hashlib.sha256(
                f"{row['source']}:{row['title']}:chunk_{i}:{chunk_text_piece[:100]}".encode("utf-8")
            ).hexdigest()[:24]

            all_chunks.append({
                "id":        chunk_id,
                "text":      chunk_text_piece,
                "source":    row["source"],
                "type":      row["type"],
                "category":  row["category"],
                "title":     row["title"],
                "summary":   row["summary"],
                "keywords":  row["keywords"],
                "chunk_idx": i,
            })

    # Deduplicate chunks by ID to prevent ChromaDB errors
    seen_ids = set()
    deduped_chunks = []
    for c in all_chunks:
        if c["id"] not in seen_ids:
            seen_ids.add(c["id"])
            deduped_chunks.append(c)

    print(f"[+] Created {len(deduped_chunks)} chunks from {len(merged_df)} documents (Removed {len(all_chunks) - len(deduped_chunks)} duplicate IDs)")
    return deduped_chunks


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════

def build_chromadb(chunks: list[dict], force: bool = False):
    """
    Generate embeddings with bge-m3 and store in persistent ChromaDB.
    """
    import torch
    # Set PyTorch thread count to optimal number of CPU cores to avoid contention
    torch.set_num_threads(8)
    
    import chromadb
    from sentence_transformers import SentenceTransformer

    # --- Load embedding model ---
    print(f"\n[*] Loading embedding model: {EMBEDDING_MODEL} (Forced to CUDA)")
    print("    (First run downloads ~2GB model — subsequent runs use cache)")
    model = SentenceTransformer(EMBEDDING_MODEL, device="cuda")

    # --- Initialize ChromaDB ---
    print(f"[*] Opening ChromaDB at: {CHROMA_DB_PATH}")
    client = chromadb.PersistentClient(path=CHROMA_DB_PATH)

    if force:
        print("[!] --force: Deleting existing collection...")
        try:
            client.delete_collection(CHROMA_COLLECTION)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},  # cosine similarity for bge-m3
    )

    existing_count = collection.count()
    if existing_count > 0 and not force:
        print(f"[!] Collection already has {existing_count} documents.")
        print("    Use --force to rebuild. Skipping embedding step.")
        return

    # --- Prepare data ---
    texts      = [c["text"] for c in chunks]
    ids        = [c["id"] for c in chunks]
    metadatas  = [
        {
            "source":    c["source"],
            "type":      c["type"],
            "category":  c["category"],
            "title":     c["title"],
            "summary":   c["summary"][:500] if c["summary"] else "",
            "keywords":  c["keywords"][:300] if c["keywords"] else "",
            "chunk_idx": c["chunk_idx"],
        }
        for c in chunks
    ]

    # --- Deduplicate texts for embedding ---
    unique_texts = list(set(texts))
    print(f"\n[+] Total chunks: {len(texts)}")
    print(f"[+] Unique chunk texts: {len(unique_texts)} (Saved {len(texts) - len(unique_texts)} redundant embeddings)")

    # --- Embed unique texts ---
    print(f"[*] Embedding unique texts with batch_size=32...")
    import time
    t0 = time.time()
    
    unique_embeddings = model.encode(
        unique_texts,
        batch_size=32,
        normalize_embeddings=True,
        show_progress_bar=True,
    ).tolist()
    
    t1 = time.time()
    print(f"[+] Finished embedding in {t1 - t0:.2f}s ({len(unique_texts)/(t1 - t0):.2f} texts/sec)")

    # Create mapping from text to embedding
    text_to_emb = dict(zip(unique_texts, unique_embeddings))

    # Map all texts to their embeddings
    embeddings = [text_to_emb[t] for t in texts]

    # --- Insert into ChromaDB in batches ---
    BATCH_SIZE = 1000
    total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"\n[*] Inserting {len(texts)} chunks into ChromaDB in {total_batches} batches...")

    for i in tqdm(range(0, len(texts), BATCH_SIZE), total=total_batches, desc="Inserting"):
        batch_end = min(i + BATCH_SIZE, len(texts))
        collection.add(
            ids=ids[i:batch_end],
            embeddings=embeddings[i:batch_end],
            documents=texts[i:batch_end],
            metadatas=metadatas[i:batch_end],
        )

    final_count = collection.count()
    print(f"\n[+] ChromaDB collection '{CHROMA_COLLECTION}' now has {final_count} documents")
    print(f"[+] Stored at: {CHROMA_DB_PATH}")


# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Qanuni.AI Data Loader — Merge, Chunk, Embed, Store")
    parser.add_argument("--force", action="store_true",
                        help="Wipe existing ChromaDB and rebuild from scratch")
    parser.add_argument("--limit-corpus", type=int, default=None,
                        help="Limit the number of rows from egypt_legal_corpus to speed up ingestion")
    args = parser.parse_args()

    print("=" * 60)
    print("  Qanuni.AI — Data Ingestion Pipeline")
    print("=" * 60)

    # Step 1: Load pre-merged V3 corpus
    if not MERGED_CORPUS_PATH.exists():
        raise FileNotFoundError(
            f"Merged legal corpus v3 not found at: {MERGED_CORPUS_PATH}\n"
            "Please ensure the file is generated and placed in the data/ directory."
        )

    print(f"\n[1/3] Loading pre-merged V3 corpus from {MERGED_CORPUS_PATH.name}...")
    merged = pd.read_parquet(MERGED_CORPUS_PATH)
    merged = merged.fillna("")
    print(f"      → {len(merged):,} rows loaded")
    print(f"      Sources:")
    for src, count in merged["source"].value_counts().items():
        print(f"        • {src}: {count:,}")

    # Step 2: Chunk
    chunks = build_chunks(merged)

    # Step 3: Embed and store in ChromaDB
    build_chromadb(chunks, force=args.force)

    print("\n" + "=" * 60)
    print("   Ingestion complete!")
    print("  → Run `python app.py` to start the server")
    print("=" * 60)


if __name__ == "__main__":
    main()
