"""
Qanuni.AI — Centralized Configuration
All constants for the RAG pipeline: datasets, embeddings, ChromaDB, chunking,
reranking, hybrid retrieval, case memory, reasoning chain, auth, and security.
"""

import os
import secrets
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent

# Dataset files (local parquet copies)
MERGED_CORPUS_PATH      = BASE_DIR / "data" / "merged_legal_corpus_v3.parquet"


# Dataset source labels (used in metadata)
SOURCE_CORPUS = "egypt_legal_corpus"   # dataflare/egypt-legal-corpus
SOURCE_RAG    = "egypt_legal_rag"      # fr3on/eg-legal-rag
SOURCE_MANSHURAT = "manshurat_org"     # manshurat.org

# Manshurat.org scraping parameters
MANSHURAT_BASE_URL = "https://manshurat.org"
MANSHURAT_SCRAPE_DELAY = 1.5  # seconds between requests
MANSHURAT_CHECKPOINT_PATH = BASE_DIR / "scraper" / "manshurat_checkpoint.json"
MANSHURAT_URLS_PATH = BASE_DIR / "scraper" / "manshurat_urls.json"

# Embedding model
EMBEDDING_MODEL = "intfloat/multilingual-e5-base"
# Alternative (heavier, better quality): "BAAI/bge-m3"

# ChromaDB
CHROMA_DB_PATH   = str(BASE_DIR / "chroma_db")
CHROMA_COLLECTION = "egypt_legal_unified"

# Chunking parameters
CHUNK_SIZE          = 800    # max characters per chunk
CHUNK_OVERLAP_WORDS = 20     # word overlap between consecutive chunks

# Retrieval
TOP_K = 6                    # max chunks to return from vector search

# Reranker
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
# Alternative: "jinaai/jina-reranker-v2-base-multilingual"
RERANKER_TOP_K = 4           # keep top-4 after reranking (from TOP_K candidates)
RERANKER_BATCH_SIZE = 16     # batch size for cross-encoder inference

# Hybrid Retrieval (Semantic + BM25)
SEMANTIC_WEIGHT = 0.65       # weight for semantic (embedding) scores in RRF
BM25_WEIGHT     = 0.35       # weight for BM25 keyword scores in RRF
BM25_TOP_K      = 10         # how many results BM25 returns before fusion
HYBRID_CANDIDATE_K = 15      # total candidates before reranking

# Case Memory (SQLite)
CASE_MEMORY_DB_PATH = str(BASE_DIR / "data" / "case_memory.db")
CASE_MEMORY_SIMILAR_TOP_K = 3  # max similar cases to retrieve

# Conversational Context Memory
CONVERSATION_MEMORY_MAX_TURNS = 3   # max user/assistant turn pairs to keep (reduced for RAM)

# CPU Optimization (tuned for RTX 3050 / 16GB RAM)
EMBEDDING_CACHE_SIZE = 128    # LRU cache entries for embedding vectors
TORCH_NUM_THREADS   = 4      # PyTorch intra-op parallelism threads (reduced for 16GB RAM)

# Legal Reasoning Chain Categories
LEGAL_CATEGORIES = [
    "criminal_law",        # القانون الجنائي
    "civil_law",           # القانون المدني
    "commercial_law",      # القانون التجاري
    "family_law",          # قانون الأحوال الشخصية
    "labor_law",           # قانون العمل
    "administrative_law",  # القانون الإداري
    "constitutional_law",  # القانون الدستوري
    "procedural_law",      # قانون الإجراءات
    "real_estate_law",     # قانون العقارات
    "tax_law",             # القانون الضريبي
    "other",               # أخرى
]

# Web Search Tool (DuckDuckGo)
WEB_SEARCH_MAX_RESULTS   = 5       # max results per search call
WEB_SEARCH_CACHE_TTL     = 300     # cache TTL in seconds (5 minutes)
WEB_SEARCH_LEGAL_DOMAINS = [       # trusted Egyptian legal sources
    "manshurat.org",
    "eastlaws.com",
    "cc.gov.eg",                    # محكمة النقض
    "scc.gov.eg",                   # المحكمة الدستورية
    "laweg.net",
    "elwatannews.com",
    "youm7.com",
]

# ── Auth & Security ───────────────────────────────────────────────────
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_hex(32))
JWT_ACCESS_TOKEN_EXPIRES = 86400    # 24 hours (local system, long sessions)
BCRYPT_LOG_ROUNDS = 12
GUEST_MESSAGE_LIMIT = 5             # max messages before requiring signup
RATE_LIMIT_PER_MINUTE = 15          # max API requests per minute per IP

# Input sanitization
MAX_INPUT_LENGTH = 2000
PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(previous|above|all)",
    r"system\s*prompt",
    r"you\s+are\s+now",
    r"forget\s+(everything|instructions)",
    r"<script",
    r"javascript:",
    r"disregard",
    r"override\s+instructions",
]
