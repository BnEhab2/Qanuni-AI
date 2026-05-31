"""
Qanuni.AI — Main Application Launcher
======================================
Sets up NLP & ML models, loads RAG pipeline components,
and launches the Flask application factory.
"""

import os
import sys
import gc
import inspect
import asyncio
import threading
import uuid
from pathlib import Path
from dotenv import load_dotenv
from flask import jsonify, request

# Fix Windows terminal encoding for Arabic / emoji output
if sys.platform.startswith("win"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

API_KEY = os.getenv("OPENROUTER_API_KEY", "")
print(f"[*] API Key loaded: {'YES (length={})'.format(len(API_KEY)) if API_KEY else 'NO'}")
PORT = int(os.getenv("PORT", 5000))

from config import (
    EMBEDDING_MODEL,
    CHROMA_DB_PATH,
    CHROMA_COLLECTION,
    TOP_K,
    RERANKER_TOP_K,
    CONVERSATION_MEMORY_MAX_TURNS,
    TORCH_NUM_THREADS,
    MERGED_CORPUS_PATH,
)

# ── Dynamic Database Downloader (For HuggingFace Spaces Deployment) ────
import urllib.request
import zipfile

def _download_file(url, dest_path):
    req = urllib.request.Request(url)
    hf_token = os.getenv("HF_TOKEN")
    if hf_token:
        req.add_header("Authorization", f"Bearer {hf_token}")
    
    with urllib.request.urlopen(req) as response, open(dest_path, 'wb') as out_file:
        chunk_size = 1024 * 1024  # 1MB
        while True:
            chunk = response.read(chunk_size)
            if not chunk:
                break
            out_file.write(chunk)

def _check_and_download_db():
    chroma_path = Path(CHROMA_DB_PATH)
    data_path = Path(MERGED_CORPUS_PATH)
    
    chroma_url = os.getenv("HF_CHROMA_DB_URL")
    corpus_url = os.getenv("HF_CORPUS_URL")
    
    db_file = chroma_path / "chroma.sqlite3"
    if chroma_url and (not chroma_path.exists() or not db_file.exists()):
        print(f"[*] HuggingFace Space: ChromaDB missing or incomplete. Downloading: {chroma_url}...")
        try:
            zip_tmp = chroma_path.parent / "chroma_db_temp.zip"
            zip_tmp.parent.mkdir(parents=True, exist_ok=True)
            
            _download_file(chroma_url, zip_tmp)
            print("    Extracting database...")
            with zipfile.ZipFile(zip_tmp, 'r') as zip_ref:
                zip_ref.extractall(chroma_path.parent)
                
            os.remove(zip_tmp)
            print("[+] ChromaDB downloaded and extracted successfully!")
        except Exception as e:
            print(f"[!] Error downloading ChromaDB: {e}")
            
    if corpus_url and not data_path.exists():
        print(f"[*] HuggingFace Space: Legal corpus missing. Downloading: {corpus_url}...")
        try:
            data_path.parent.mkdir(parents=True, exist_ok=True)
            _download_file(corpus_url, data_path)
            print("[+] Legal corpus downloaded successfully!")
        except Exception as e:
            print(f"[!] Error downloading corpus: {e}")

_check_and_download_db()
import chromadb
from sentence_transformers import SentenceTransformer

import torch
torch.set_num_threads(TORCH_NUM_THREADS)

print(f"[*] Loading embedding model: {EMBEDDING_MODEL}")
_embed_model = SentenceTransformer(EMBEDDING_MODEL)

print(f"[*] Connecting to ChromaDB at: {CHROMA_DB_PATH}")
_chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
_collection = _chroma_client.get_collection(name=CHROMA_COLLECTION)
print(f"[+] ChromaDB collection '{CHROMA_COLLECTION}' loaded — {_collection.count()} documents")

from core.retrieval import HybridRetriever, get_hybrid_retriever

print("[*] Initializing Hybrid Retriever (semantic + BM25)...")
_retriever = get_hybrid_retriever(
    embed_model=_embed_model,
    chroma_client=_chroma_client,
    collection=_collection,
)

from core.reranker import get_reranker

print("[*] Reranker registered (lazy-load on first query)")
_reranker = get_reranker()

# Background thread to warm up models and build indexes asynchronously
def _warm_up_services():
    try:
        print("[Warmup] Background warmup started...")
        # 1. Warm up embedding model
        _embed_model.encode("تحميل")
        
        # 2. Warm up BM25 index (this will load cache from disk in 1-2s!)
        _retriever._build_bm25_index()
        
        # 3. Warm up Reranker (this will lazy load BAAI/bge-reranker-v2-m3 in the background)
        _reranker._ensure_model()
        print("[Warmup] Background warmup completed! All services ready in-memory.")
    except Exception as e:
        print(f"[Warmup] Error during background warmup: {e}")

# Only run warmup in the main Werkzeug worker process to avoid running twice in debug mode
if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    threading.Thread(target=_warm_up_services, daemon=True).start()


# Share embedding model instance with CaseMemory to save ~400MB RAM
from core.memory import get_case_memory

print("[*] Initializing Case Memory (SQLite)...")
_case_memory = get_case_memory()
# Inject the pre-loaded embedding model to prevent duplicate loading
_case_memory.embed_model = _embed_model

from core.reasoning import LegalReasoningChain

from core.tools import (
    search_laws as tool_search_laws,
    retrieve_articles as tool_retrieve_articles,
    classify_case as tool_classify_case,
    retrieve_precedents as tool_retrieve_precedents,
    compare_cases as tool_compare_cases,
    generate_legal_strategy as tool_generate_legal_strategy,
    format_citation,
    build_citation_block,
)

from core.web_search import (
    search_web as tool_search_web,
    search_court_rulings as tool_search_court_rulings,
    search_legal_news as tool_search_legal_news,
)

class ConversationMemory:
    """
    Per-session conversational context memory.
    Maintains a sliding window of the last N user/assistant turns.
    Thread-safe with a lock per session.
    """

    def __init__(self, max_turns: int = CONVERSATION_MEMORY_MAX_TURNS):
        self._max_turns = max_turns
        self._sessions: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def add_turn(self, session_id: str, role: str, content: str):
        """Add a user or assistant message to the session history."""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = []
            self._sessions[session_id].append({
                "role": role,
                "content": content[:2000],  # Limit per-turn size
            })
            # Keep only last N turns (each turn = user + assistant pair)
            max_messages = self._max_turns * 2
            if len(self._sessions[session_id]) > max_messages:
                self._sessions[session_id] = self._sessions[session_id][-max_messages:]

    def get_context(self, session_id: str) -> str:
        """Build a context string from the session history."""
        with self._lock:
            history = self._sessions.get(session_id, [])

        if not history:
            return ""

        parts = ["### سياق المحادثة السابقة:"]
        for msg in history:
            role_label = "المستخدم" if msg["role"] == "user" else "المستشار"
            parts.append(f"**{role_label}**: {msg['content'][:500]}")

        return "\n".join(parts)

    def clear(self, session_id: str):
        """Clear a session's history."""
        with self._lock:
            self._sessions.pop(session_id, None)


_conversation_memory = ConversationMemory()

def search_laws(query: str, top_k: int = RERANKER_TOP_K,
                source_filter: str | None = None,
                type_filter: str | None = None,
                category_filter: str | None = None) -> list[dict]:
    """
    Full retrieval pipeline:
        Query → Hybrid Retrieval → Reranker → Citations → Final Results

    Returns the top-k most relevant chunks with metadata and citations.
    """
    results = tool_search_laws(
        query=query,
        retriever=_retriever,
        reranker=_reranker,
        top_k=top_k,
        source_filter=source_filter,
        type_filter=type_filter,
        category_filter=category_filter,
    )
    # Trigger garbage collection after heavy NLP retrieval / reranking to keep memory usage low
    gc.collect()
    return results


SYSTEM_PROMPT = """أنت Qanuni.AI — مساعد قانوني ذكي متخصص في القانون المصري.
أنت بتتكلم مع محامين ومستشارين قانونيين، فالكلام بينك وبينهم لازم يكون مهني ومحترم لكن طبيعي — زي زميل شاطر بيساعدك.

قواعدك الأساسية:
- اتكلم عربي فصحى واضحة ومهنية، لكن من غير تكلّف أو تكرار نفس القالب كل مرة.
- استند للنصوص القانونية المرفقة (Excerpts) واذكر رقم المادة واسم القانون لما تستشهد.
- لو مفيش نص مباشر يغطي السؤال، قول كده بصراحة واقترح أقرب المواد.
- لا تخترع مواد أو بنود مش موجودة في النصوص المرفقة.
- نسّق الرد بشكل مرتب (عناوين، نقاط، جداول لو لزم) بس من غير ما تلتزم بنفس الهيكل كل مرة — خلي الرد يناسب طبيعة السؤال.
- لما تذكر مادة قانونية، استخدم صيغة: المادة [رقم] — [اسم القانون]
- في آخر كل رد حط سطر: " تنبيه: المعلومات دي استرشادية بناءً على قاعدة البيانات المتاحة ومش بديل عن استشارة قانونية متخصصة."

الأدوات المتاحة لك:
- search_web: للبحث على الإنترنت عن معلومات قانونية مصرية. استخدمها لما النصوص المرفقة مش كافية أو المستخدم يسأل عن حاجة حديثة.
- search_court_rulings: للبحث عن أحكام محاكم مصرية وأحكام نقض.
- search_legal_news: للبحث عن أخبار قانونية وتعديلات تشريعية حديثة.

لما تستخدم أداة بحث الإنترنت:
- وضّح في الرد إن المعلومات دي من الإنترنت مش من قاعدة البيانات المحلية.
- اذكر مصدر المعلومة (اسم الموقع أو الرابط) لو متاح.
"""

DEBATE_PROMPT_PROSECUTION = """أنت Qanuni.AI في وضع المناظرة القانونية.
أنت بتلعب دور النيابة / الادعاء والمستخدم بيلعب دور الدفاع.

قواعد المناظرة:
- أنت لازم تبني حجتك على النصوص القانونية المرفقة (Excerpts) وتستشهد بالمواد بالظبط.
- رد على حجج الدفاع (المستخدم) بشكل منطقي ومنظم واستند للقانون.
- اتكلم بأسلوب نيابة محترم ومهني — حازم لكن من غير إهانة.
- لو المستخدم جاب نقطة قوية وعندها سند قانوني، اعترف بيها وحاول ترد عليها من زاوية تانية.
- لو المستخدم قال "كفاية" أو "خلاص" أو طلب وقف المناظرة، لخّص النقاط الرئيسية للطرفين بشكل محايد.
- في آخر التلخيص حط: "تنبيه: المناظرة دي تمرين قانوني وليست استشارة رسمية."
"""

DEBATE_PROMPT_DEFENSE = """أنت Qanuni.AI في وضع المناظرة القانونية.
أنت بتلعب دور الدفاع والمستخدم بيلعب دور النيابة / الادعاء.

قواعد المناظرة:
- أنت لازم تدافع عن المتهم وتبني حجتك على النصوص القانونية المرفقة (Excerpts) وتستشهد بالمواد بالظبط.
- رد على حجج النيابة (المستخدم) بشكل منطقي ومنظم واستند للقانون.
- اتكلم بأسلوب دفاع محترم ومهني — دافع بقوة لكن باحترام.
- لو المستخدم جاب نقطة قوية لصالح الادعاء، اعترف بيها وحاول تلاقيها بدفع مضاد.
- لو المستخدم قال "كفاية" أو "خلاص" أو طلب وقف المناظرة، لخّص النقاط الرئيسية للطرفين بشكل محايد.
- في آخر التلخيص حط: "تنبيه: المناظرة دي تمرين قانوني وليست استشارة رسمية."
"""


from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

# ── Language Model Setup ──────────────────────────────────────────────
openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

if openrouter_key:
    print("[*] Configuring google-adk LiteLlm agent with OpenRouter (gpt-4o-mini)...")
    llm = LiteLlm(
        model="openrouter/openai/gpt-4o-mini",
        api_key=openrouter_key,
        api_base="https://openrouter.ai/api/v1",
        temperature=0.5,
        max_output_tokens=2048,
    )
elif gemini_key:
    print("[*] Configuring google-adk LiteLlm agent with native Gemini (fallback)...")
    llm = LiteLlm(
        model="gemini/gemini-1.5-flash",
        api_key=gemini_key,
        temperature=0.5,
        max_output_tokens=2048,
    )
else:
    print("[!] WARNING: No API key configured. Agent fallback to offline/empty.")
    llm = LiteLlm(
        model="openrouter/openai/gpt-4o-mini",
        api_key="",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.5,
        max_output_tokens=2048,
    )

# ── Main Agent Definition ─────────────────────────────────────────────
qanuni_agent = LlmAgent(
    name="qanuni_agent",
    model=llm,
    description="Qanuni.AI — Egyptian Legal AI Agent",
    instruction=SYSTEM_PROMPT,
    tools=[
        tool_search_web,
        tool_search_court_rulings,
        tool_search_legal_news,
    ],
)

# ── Debate Agent Definition ──────────────────────────────────────────
debate_agent_prosecution = LlmAgent(
    name="qanuni_debate_prosecution",
    model=llm,
    description="Qanuni.AI Debate - AI plays Prosecution",
    instruction=DEBATE_PROMPT_PROSECUTION,
)

debate_agent_defense = LlmAgent(
    name="qanuni_debate_defense",
    model=llm,
    description="Qanuni.AI Debate - AI plays Defense",
    instruction=DEBATE_PROMPT_DEFENSE,
)

# ── ADK Session & Runner Setup ────────────────────────────────────────
session_service = InMemorySessionService()
runner = Runner(
    agent=qanuni_agent,
    app_name="qanuni",
    session_service=session_service,
)
debate_runner_prosecution = Runner(
    agent=debate_agent_prosecution,
    app_name="qanuni_debate_prosecution",
    session_service=session_service,
)
debate_runner_defense = Runner(
    agent=debate_agent_defense,
    app_name="qanuni_debate_defense",
    session_service=session_service,
)

# ── Persistent Event Loop for running async coroutines ────────────────
_loop = asyncio.new_event_loop()
_loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
_loop_thread.start()

def run_async(coro):
    """Run an async coroutine on the persistent event loop."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=120)

async def _run_agent_async(
    user_message: str,
    law_excerpts: list[dict],
    reasoning_context: str = "",
    conversation_context: str = "",
    citation_block: str = "",
    web_search_hint: str = "",
) -> str:
    """Send a message to the root agent and collect the response asynchronously."""
    context_parts: list[str] = []

    if conversation_context:
        context_parts.append(conversation_context)
        context_parts.append("")

    if reasoning_context:
        context_parts.append("### تحليل منهجي متعدد الخطوات:")
        context_parts.append(reasoning_context)
        context_parts.append("")

    context_parts.append("### النصوص القانونية المسترجعة:")
    for i, ex in enumerate(law_excerpts, 1):
        excerpt_header = (
            f"--- Excerpt {i} ---\n"
            f"Law: {ex['law_name']}\n"
            f"Source: {ex.get('source', '')}\n"
            f"Type: {ex.get('type', '')}\n"
            f"Categories: {ex['categories']}\n"
        )
        citation = ex.get("citation", format_citation(ex))
        excerpt_header += f"Citation: {citation}\n"

        if ex.get('summary'):
            excerpt_header += f"Summary: {ex['summary'][:300]}\n"

        if ex.get("retrieval_method"):
            excerpt_header += f"Retrieval: {ex['retrieval_method']}\n"
        if ex.get("reranker_score") is not None:
            excerpt_header += f"Reranker Score: {ex['reranker_score']:.4f}\n"

        excerpt_header += f"Text: {ex['text'][:1500]}\n"
        context_parts.append(excerpt_header)

    if not law_excerpts:
        context_parts.append("(لا توجد نصوص قانونية مطابقة في قاعدة البيانات.)")

    if citation_block:
        context_parts.append(citation_block)

    context_block = "\n".join(context_parts)

    if web_search_hint:
        prompt = f"{context_block}\n\n{web_search_hint}\n\n### User Question:\n{user_message}"
    else:
        prompt = f"{context_block}\n\n### User Question:\n{user_message}"

    user_id = "default_user"
    session_id = str(uuid.uuid4())

    session_result = session_service.create_session(
        app_name="qanuni",
        user_id=user_id,
        session_id=session_id,
    )
    if inspect.iscoroutine(session_result):
        session = await session_result
    else:
        session = session_result

    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt)],
    )

    response_parts = []
    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=content,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    response_parts.append(part.text)

    return "\n".join(response_parts) if response_parts else "عذراً، مفيش رد متاح دلوقتي."


def ask_llm(
    user_message: str,
    law_excerpts: list[dict],
    reasoning_context: str = "",
    conversation_context: str = "",
    citation_block: str = "",
    web_search_hint: str = "",
) -> str:
    return run_async(
        _run_agent_async(
            user_message,
            law_excerpts,
            reasoning_context=reasoning_context,
            conversation_context=conversation_context,
            citation_block=citation_block,
            web_search_hint=web_search_hint,
        )
    )

# ── Flask Application Creation ────────────────────────────────────────
from api import create_app
from api.chat import init_chat_services

app = create_app()

# Inject the model-related singletons & functions into the chat blueprint
init_chat_services(
    search_laws_fn=search_laws,
    ask_llm_fn=ask_llm,
    conversation_memory=_conversation_memory,
    case_memory=_case_memory,
    reasoning_chain_class=LegalReasoningChain,
    format_citation_fn=format_citation,
    build_citation_block_fn=build_citation_block,
    run_async_fn=run_async,
    session_service=session_service,
    runner=runner,
    debate_runner_prosecution=debate_runner_prosecution,
    debate_runner_defense=debate_runner_defense,
    types_module=types,
)

# ── Backwards Compatible Direct Case Memory & Tool Endpoints ───────────

@app.route("/api/case-memory/<int:case_id>", methods=["GET"])
def get_case(case_id: int):
    """Retrieve a stored case from memory."""
    case = _case_memory.summarize_case(case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    return jsonify(case)


@app.route("/api/case-memory/search", methods=["POST"])
def search_cases():
    """Search for similar past cases."""
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400

    top_k = data.get("top_k", 3)
    category = data.get("category")

    similar = _case_memory.retrieve_similar_cases(
        query=query, top_k=top_k, category_filter=category
    )
    return jsonify({"cases": similar})


@app.route("/api/case-memory/recent", methods=["GET"])
def recent_cases():
    """Get recent case analyses."""
    limit = request.args.get("limit", 10, type=int)
    cases = _case_memory.get_recent_cases(limit=limit)
    return jsonify({"cases": cases})


@app.route("/api/classify", methods=["POST"])
def classify():
    """Classify a legal case by domain."""
    data = request.get_json(force=True)
    description = data.get("description", "").strip()
    if not description:
        return jsonify({"error": "Empty description"}), 400

    chain = LegalReasoningChain()
    result = tool_classify_case(description, chain)
    return jsonify(result)


@app.route("/api/web-search", methods=["POST"])
def web_search():
    """Direct web search endpoint."""
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    search_type = data.get("type", "general")  # general, rulings, news

    if not query:
        return jsonify({"error": "Empty query"}), 400

    if search_type == "rulings":
        result = tool_search_court_rulings(query)
    elif search_type == "news":
        result = tool_search_legal_news(query)
    else:
        result = tool_search_web(query)

    return jsonify({
        "results": result,
        "type": search_type,
        "query": query,
    })


if __name__ == "__main__":
    print(f"\n>>> Qanuni.AI v3.1 server starting on http://localhost:{PORT}")
    print("    Model: GPT-4o-mini via OpenRouter")
    print("    Architecture: Refactored (Blueprints + SQLite auth & history persistence)")
    app.run(host="0.0.0.0", port=PORT, debug=True)
