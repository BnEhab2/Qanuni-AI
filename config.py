"""
Qanuni.AI — Config
All settings for the app: paths, models, retrieval, auth, etc.
"""

import os
import secrets
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
MERGED_CORPUS_PATH = BASE_DIR / "data" / "merged_legal_corpus_v3.parquet"

# Dataset source labels
SOURCE_CORPUS = "egypt_legal_corpus"
SOURCE_RAG = "egypt_legal_rag"
SOURCE_MANSHURAT = "manshurat_org"

# Manshurat.org scraping
MANSHURAT_BASE_URL = "https://manshurat.org"
MANSHURAT_SCRAPE_DELAY = 1.5
MANSHURAT_CHECKPOINT_PATH = BASE_DIR / "scraper" / "manshurat_checkpoint.json"
MANSHURAT_URLS_PATH = BASE_DIR / "scraper" / "manshurat_urls.json"

# Embedding model
EMBEDDING_MODEL = "intfloat/multilingual-e5-base"

# ChromaDB
CHROMA_DB_PATH = str(BASE_DIR / "chroma_db")
CHROMA_COLLECTION = "egypt_legal_unified"

# Chunking
CHUNK_SIZE = 800
CHUNK_OVERLAP_WORDS = 20

# Retrieval
TOP_K = 6

try:
    import torch
    _cuda_available = torch.cuda.is_available()
except ImportError:
    _cuda_available = False

# Reranker configurations
USE_RERANKER = _cuda_available  # Skip reranker on CPU to reduce query latency from 8s to <1s
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_TOP_K = 4
RERANKER_BATCH_SIZE = 16

# Hybrid Retrieval (Semantic + BM25)
SEMANTIC_WEIGHT = 0.65
BM25_WEIGHT = 0.35
BM25_TOP_K = 10

# Dynamic candidate count to reduce CPU reranking latency
HYBRID_CANDIDATE_K = 15 if _cuda_available else 8

# Case Memory
CASE_MEMORY_DB_PATH = str(BASE_DIR / "data" / "case_memory.db")
CASE_MEMORY_SIMILAR_TOP_K = 3

# Conversation Memory
CONVERSATION_MEMORY_MAX_TURNS = 3

# CPU / Performance
EMBEDDING_CACHE_SIZE = 128
TORCH_NUM_THREADS = 4

# Legal Categories
LEGAL_CATEGORIES = [
    "criminal_law",
    "civil_law",
    "commercial_law",
    "family_law",
    "labor_law",
    "administrative_law",
    "constitutional_law",
    "procedural_law",
    "real_estate_law",
    "tax_law",
    "other",
]

# Web Search (DuckDuckGo)
WEB_SEARCH_MAX_RESULTS = 5
WEB_SEARCH_CACHE_TTL = 300
WEB_SEARCH_LEGAL_DOMAINS = [
    "manshurat.org",
    "eastlaws.com",
    "cc.gov.eg",
    "scc.gov.eg",
    "laweg.net",
    "elwatannews.com",
    "youm7.com",
]

# Auth & Security
def _get_persistent_jwt_secret() -> str:
    """Load or generate a persistent JWT secret key to preserve user sessions across restarts."""
    env_secret = os.getenv("JWT_SECRET_KEY")
    if env_secret:
        return env_secret

    secret_file = BASE_DIR / "data" / "jwt_secret.key"
    if secret_file.exists():
        try:
            return secret_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    # Generate a new persistent key and save it
    new_secret = secrets.token_hex(32)
    try:
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(new_secret, encoding="utf-8")
        print(f"[Config] Generated and saved persistent JWT secret key to: {secret_file.name}")
    except Exception as e:
        print(f"[Config] Failed to save persistent JWT secret key: {e}")
    return new_secret

JWT_SECRET_KEY = _get_persistent_jwt_secret()
JWT_ACCESS_TOKEN_EXPIRES = 86400
BCRYPT_LOG_ROUNDS = 12
GUEST_MESSAGE_LIMIT = 5
RATE_LIMIT_PER_MINUTE = 15
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://localhost:5000,http://127.0.0.1:3000,http://127.0.0.1:5000").split(",")
    if o.strip()
]

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

# ── Deep Reasoning Engine ────────────────────────────────────────────
REASONING_ARTICLE_LINK_HOPS = 1
REASONING_CONTRADICTION_THRESHOLD = 0.7
REASONING_MIN_CONFIDENCE = 0.3
REASONING_MAX_RISK_ITEMS = 5

# Confidence Scoring Weights
CONFIDENCE_WEIGHT_RETRIEVAL = 0.25
CONFIDENCE_WEIGHT_COVERAGE = 0.20
CONFIDENCE_WEIGHT_CLASSIFICATION = 0.15
CONFIDENCE_WEIGHT_CONTRADICTION = 0.15
CONFIDENCE_WEIGHT_PRECEDENT = 0.15
CONFIDENCE_WEIGHT_EVIDENCE = 0.10

# Hallucination Guard
HALLUCINATION_LOW_CONFIDENCE_THRESHOLD = 0.4
HALLUCINATION_MEDIUM_CONFIDENCE_THRESHOLD = 0.7
