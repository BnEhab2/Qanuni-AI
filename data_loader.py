"""
Qanuni.AI — Data Loader
Loads parquet corpus, chunks documents, generates embeddings, stores in ChromaDB.

Usage:
    python data_loader.py          # Full ingest
    python data_loader.py --force  # Wipe and rebuild
"""

import os
import sys
import re
import hashlib
import argparse
import pandas as pd
from pathlib import Path
from tqdm import tqdm

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


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP_WORDS) -> list[str]:
    """Split text into overlapping chunks respecting sentence boundaries."""
    if len(text) <= chunk_size:
        return [text]

    # Split by Arabic/English sentence delimiters: period, semicolon, question mark, exclamation, or newline
    sentences = re.split(r'(?<=[.؛!؟\n])\s+', text)
    chunks = []
    current_chunk = []
    current_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        
        # If a single sentence is larger than chunk_size, fallback to word splitting for it
        if len(sentence) > chunk_size:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
                current_chunk = []
                current_len = 0
            
            words = sentence.split()
            sub_chunk = []
            sub_len = 0
            for w in words:
                sub_chunk.append(w)
                sub_len += len(w) + 1
                if sub_len >= chunk_size:
                    chunks.append(" ".join(sub_chunk))
                    sub_chunk = sub_chunk[-overlap:] if len(sub_chunk) > overlap else []
                    sub_len = sum(len(x) + 1 for x in sub_chunk)
            if sub_chunk:
                chunks.append(" ".join(sub_chunk))
            continue

        if current_len + len(sentence) + 1 > chunk_size:
            if current_chunk:
                chunks.append(" ".join(current_chunk))
            
            # Keep up to 2 sentences as overlap for context continuity
            overlap_sentences = current_chunk[-2:] if len(current_chunk) >= 2 else current_chunk[-1:] if current_chunk else []
            current_chunk = list(overlap_sentences)
            current_chunk.append(sentence)
            current_len = sum(len(s) + 1 for s in current_chunk)
        else:
            current_chunk.append(sentence)
            current_len += len(sentence) + 1

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks


def build_chunks(df: pd.DataFrame) -> list[dict]:
    """Chunk every row and return dicts ready for ChromaDB."""
    print("\n[*] Chunking documents...")
    all_chunks = []

    for _, row in tqdm(df.iterrows(), total=len(df), desc="Chunking"):
        text = row["text"]
        if not text or len(text.strip()) < 10:
            continue

        for i, piece in enumerate(chunk_text(text)):
            chunk_id = hashlib.sha256(
                f"{row['source']}:{row['title']}:chunk_{i}:{piece[:100]}".encode("utf-8")
            ).hexdigest()[:24]

            all_chunks.append({
                "id":        chunk_id,
                "text":      piece,
                "source":    row["source"],
                "type":      row["type"],
                "category":  row["category"],
                "title":     row["title"],
                "summary":   row["summary"],
                "keywords":  row["keywords"],
                "chunk_idx": i,
            })

    # Deduplicate by ID
    seen = set()
    deduped = []
    for c in all_chunks:
        if c["id"] not in seen:
            seen.add(c["id"])
            deduped.append(c)

    print(f"[+] {len(deduped)} chunks from {len(df)} docs ({len(all_chunks) - len(deduped)} duplicates removed)")
    return deduped


def build_chromadb(chunks: list[dict], force: bool = False):
    """Generate embeddings and store in ChromaDB."""
    import torch
    torch.set_num_threads(8)

    import chromadb
    from sentence_transformers import SentenceTransformer

    print(f"\n[*] Loading embedding model: {EMBEDDING_MODEL}")
    model = SentenceTransformer(EMBEDDING_MODEL, device="cuda")

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
        metadata={"hnsw:space": "cosine"},
    )

    if collection.count() > 0 and not force:
        print(f"[!] Collection already has {collection.count()} docs. Use --force to rebuild.")
        return

    texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metadatas = [
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

    # Deduplicate texts for embedding efficiency
    unique_texts = list(set(texts))
    print(f"\n[+] {len(texts)} chunks, {len(unique_texts)} unique texts")

    import time
    t0 = time.time()
    unique_embeddings = model.encode(
        unique_texts, batch_size=32,
        normalize_embeddings=True, show_progress_bar=True,
    ).tolist()
    print(f"[+] Embedded in {time.time() - t0:.2f}s")

    text_to_emb = dict(zip(unique_texts, unique_embeddings))
    embeddings = [text_to_emb[t] for t in texts]

    # Insert in batches
    batch_size = 1000
    total_batches = (len(texts) + batch_size - 1) // batch_size
    print(f"\n[*] Inserting {len(texts)} chunks in {total_batches} batches...")

    for i in tqdm(range(0, len(texts), batch_size), total=total_batches, desc="Inserting"):
        end = min(i + batch_size, len(texts))
        collection.add(
            ids=ids[i:end],
            embeddings=embeddings[i:end],
            documents=texts[i:end],
            metadatas=metadatas[i:end],
        )

    print(f"\n[+] Collection '{CHROMA_COLLECTION}' now has {collection.count()} documents")


def main():
    parser = argparse.ArgumentParser(description="Qanuni.AI Data Loader")
    parser.add_argument("--force", action="store_true", help="Wipe and rebuild ChromaDB")
    parser.add_argument("--limit-corpus", type=int, default=None, help="Limit corpus rows")
    args = parser.parse_args()

    print("=" * 50)
    print("  Qanuni.AI — Data Ingestion")
    print("=" * 50)

    if not MERGED_CORPUS_PATH.exists():
        raise FileNotFoundError(f"Corpus not found: {MERGED_CORPUS_PATH}")

    print(f"\n[1/3] Loading corpus from {MERGED_CORPUS_PATH.name}...")
    merged = pd.read_parquet(MERGED_CORPUS_PATH).fillna("")
    print(f"      → {len(merged):,} rows")
    for src, count in merged["source"].value_counts().items():
        print(f"        • {src}: {count:,}")

    chunks = build_chunks(merged)
    build_chromadb(chunks, force=args.force)

    print("\n" + "=" * 50)
    print("   Done! Run `python app.py` to start.")
    print("=" * 50)


if __name__ == "__main__":
    main()
