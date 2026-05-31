"""
Qanuni.AI — Flask Backend (v2.0)
==================================
Egyptian Legal AI Agent with:
    - Hybrid retrieval (ChromaDB semantic + BM25 keyword)
    - Cross-encoder reranking (BAAI/bge-reranker-v2-m3)
    - Multi-step legal reasoning chain
    - Tool-based agent architecture
    - Persistent case memory (SQLite)
    - Conversational context memory
    - Legal citation support
    - CPU optimization (batching, caching, async)

Supports unified legal corpus from multiple HuggingFace datasets.
"""

import os
import sys
import json
import uuid
import inspect
import asyncio
import threading
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

# Fix Windows terminal encoding for Arabic / emoji output
if sys.platform.startswith("win"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv(dotenv_path=Path(__file__).parent / ".env")

API_KEY = os.getenv("OPENROUTER_API_KEY", "")
print(f"[*] API Key loaded: {'YES (length={})'.format(len(API_KEY)) if API_KEY else 'NO'}")
PORT = 5000

from config import (
    EMBEDDING_MODEL,
    CHROMA_DB_PATH,
    CHROMA_COLLECTION,
    TOP_K,
    RERANKER_TOP_K,
    CONVERSATION_MEMORY_MAX_TURNS,
    TORCH_NUM_THREADS,
)
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


from core.memory import get_case_memory

print("[*] Initializing Case Memory (SQLite)...")
_case_memory = get_case_memory()

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
    # Step 1: Hybrid retrieval (wider candidate set)
    results = tool_search_laws(
        query=query,
        retriever=_retriever,
        reranker=_reranker,
        top_k=top_k,
        source_filter=source_filter,
        type_filter=type_filter,
        category_filter=category_filter,
    )

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
    # Build context block with rich metadata from retrieval pipeline
    context_parts: list[str] = []

    # Add conversation history context
    if conversation_context:
        context_parts.append(conversation_context)
        context_parts.append("")

    # Add reasoning chain context
    if reasoning_context:
        context_parts.append("### تحليل منهجي متعدد الخطوات:")
        context_parts.append(reasoning_context)
        context_parts.append("")

    # Add retrieved law excerpts
    context_parts.append("### النصوص القانونية المسترجعة:")
    for i, ex in enumerate(law_excerpts, 1):
        excerpt_header = (
            f"--- Excerpt {i} ---\n"
            f"Law: {ex['law_name']}\n"
            f"Source: {ex.get('source', '')}\n"
            f"Type: {ex.get('type', '')}\n"
            f"Categories: {ex['categories']}\n"
        )
        # Add citation
        citation = ex.get("citation", format_citation(ex))
        excerpt_header += f"Citation: {citation}\n"

        if ex.get('summary'):
            excerpt_header += f"Summary: {ex['summary'][:300]}\n"

        # Add retrieval method and scores
        if ex.get("retrieval_method"):
            excerpt_header += f"Retrieval: {ex['retrieval_method']}\n"
        if ex.get("reranker_score") is not None:
            excerpt_header += f"Reranker Score: {ex['reranker_score']:.4f}\n"

        excerpt_header += f"Text: {ex['text'][:1500]}\n"
        context_parts.append(excerpt_header)

    if not law_excerpts:
        context_parts.append("(لا توجد نصوص قانونية مطابقة في قاعدة البيانات.)")

    # Add citation instructions
    if citation_block:
        context_parts.append(citation_block)

    context_block = "\n".join(context_parts)

    # Add web search hint if user enabled it
    if web_search_hint:
        prompt = f"{context_block}\n\n{web_search_hint}\n\n### User Question:\n{user_message}"
    else:
        prompt = f"{context_block}\n\n### User Question:\n{user_message}"

    # Use a stateless random session ID for each API call to prevent conversation crosstalk
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
    """Send user question + retrieved law excerpts to the unified GeminiAgent and return the response."""
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


app = Flask(__name__)
CORS(app)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    # Optional metadata filters from the frontend
    source_filter   = data.get("source_filter")
    type_filter     = data.get("type_filter")
    category_filter = data.get("category_filter")

    # Session ID for conversational memory (from frontend or auto-generated)
    session_id = data.get("session_id", "default_session")

    # ── Step 1: Get conversational context ──────────────────────────
    conversation_context = _conversation_memory.get_context(session_id)

    # ── Step 2: Run reasoning chain (fact identification + classification) ──
    reasoning_chain = LegalReasoningChain()
    chain_result = None
    reasoning_context = ""

    try:
        # Quick classification to potentially refine retrieval
        facts = reasoning_chain.identify_facts(user_message)
        classification = reasoning_chain.classify_case(facts)
        detected_category = classification.get("primary_category", "other")

        # Use detected category as filter if confident
        if detected_category != "other" and not category_filter:
            # Don't force filter — let hybrid retrieval handle it
            pass
    except Exception as e:
        print(f"[!] Reasoning chain pre-analysis error: {e}")
        detected_category = "other"

    # ── Step 3: Hybrid retrieval + reranking ─────────────────────────
    results = search_laws(
        user_message,
        source_filter=source_filter,
        type_filter=type_filter,
        category_filter=category_filter,
    )

    # ── Step 4: Complete reasoning chain with retrieved results ──────
    try:
        chain_result = reasoning_chain.run_full_chain(user_message, results)
        reasoning_context = chain_result.get("reasoning_context", "")
        detected_category = chain_result.get("primary_category", detected_category)
    except Exception as e:
        print(f"[!] Reasoning chain error: {e}")

    # ── Step 5: Build citation block ────────────────────────────────
    citation_block = build_citation_block(results)

    # ── Step 5.5: Build web search hint if enabled ──────────────────
    web_search_enabled = data.get("web_search", False)
    web_search_hint = ""
    if web_search_enabled:
        web_search_hint = (
            "### تعليمات إضافية:\n"
            "المستخدم فعّل البحث على الإنترنت. "
            "استخدم أدوات البحث المتاحة لك (search_web أو search_court_rulings أو search_legal_news) "
            "للبحث عن معلومات إضافية على الإنترنت تكمل النصوص المسترجعة من قاعدة البيانات."
        )

    # ── Step 6: Ask the LLM with full context ───────────────────────
    answer = ask_llm(
        user_message,
        results,
        reasoning_context=reasoning_context,
        conversation_context=conversation_context,
        citation_block=citation_block,
        web_search_hint=web_search_hint,
    )

    # ── Step 7: Update conversational memory ────────────────────────
    _conversation_memory.add_turn(session_id, "user", user_message)
    _conversation_memory.add_turn(session_id, "assistant", answer)

    # ── Step 8: Save to case memory (async, non-blocking) ───────────
    try:
        _case_memory.save_case_memory(
            user_query=user_message,
            case_summary=answer[:1000],
            retrieved_laws=results,
            reasoning=reasoning_context,
            category=detected_category,
        )
    except Exception as e:
        print(f"[!] Case memory save error: {e}")

    # ── Step 9: Build source citations with unified metadata ────────
    sources = []
    seen = set()
    for r in results:
        key = f"{r.get('source', '')}:{r['law_name']}"
        if key not in seen:
            seen.add(key)
            sources.append({
                "law_name":       r["law_name"],
                "categories":     r["categories"],
                "source":         r.get("source", ""),
                "type":           r.get("type", ""),
                "citation":       r.get("citation", format_citation(r)),
                "article_number": r.get("article_number", ""),
                "snippet":        r["text"][:300] + ("…" if len(r["text"]) > 300 else ""),
                "reranker_score": round(r.get("reranker_score", 0), 4),
                "retrieval_method": r.get("retrieval_method", ""),
            })

    return jsonify({
        "answer": answer,
        "sources": sources,
        "classification": detected_category,
        "session_id": session_id,
    })


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


@app.route("/api/conversation/clear", methods=["POST"])
def clear_conversation():
    """Clear conversational memory for a session."""
    data = request.get_json(force=True)
    session_id = data.get("session_id", "default_session")
    _conversation_memory.clear(session_id)
    return jsonify({"status": "cleared", "session_id": session_id})


@app.route("/api/debate", methods=["POST"])
def debate():
    data = request.get_json(force=True)
    user_message = data.get("message", "").strip()
    case_description = data.get("case_description", "").strip()
    session_id = data.get("session_id", "debate_" + str(uuid.uuid4())[:8])
    user_role = data.get("user_role", "defense")

    if not user_message and not case_description:
        return jsonify({"error": "أرسل رسالة أو وصف القضية"}), 400

    if user_role == "prosecution":
        chosen_runner = debate_runner_defense
        app_name = "qanuni_debate_defense"
        user_label = "حجة النيابة (المستخدم)"
    else:
        chosen_runner = debate_runner_prosecution
        app_name = "qanuni_debate_prosecution"
        user_label = "حجة الدفاع (المستخدم)"

    # Build the debate prompt with case context
    conversation_context = _conversation_memory.get_context(session_id)

    # Retrieve relevant laws for the case
    search_query = case_description if case_description else user_message
    results = search_laws(search_query)

    # Build law excerpts for context
    law_context_parts = []
    if case_description:
        law_context_parts.append(f"### وصف القضية:\n{case_description}\n")

    if conversation_context:
        law_context_parts.append(conversation_context)

    law_context_parts.append("### النصوص القانونية المسترجعة:")
    for i, ex in enumerate(results, 1):
        law_context_parts.append(
            f"--- Excerpt {i} ---\n"
            f"Law: {ex['law_name']}\n"
            f"Citation: {ex.get('citation', format_citation(ex))}\n"
            f"Text: {ex['text'][:1500]}\n"
        )

    context_block = "\n".join(law_context_parts)
    prompt = f"{context_block}\n\n### {user_label}:\n{user_message}"

    # Run through the debate agent
    async def _run_debate():
        uid = "debate_user"
        sid = str(uuid.uuid4())
        sess_result = session_service.create_session(
            app_name=app_name, user_id=uid, session_id=sid,
        )
        if inspect.iscoroutine(sess_result):
            session = await sess_result
        else:
            session = sess_result

        content = types.Content(
            role="user", parts=[types.Part.from_text(text=prompt)],
        )
        parts = []
        async for event in chosen_runner.run_async(
            user_id=uid, session_id=session.id, new_message=content,
        ):
            if event.content and event.content.parts:
                for p in event.content.parts:
                    if p.text:
                        parts.append(p.text)
        return "\n".join(parts) if parts else "مفيش رد متاح."

    answer = run_async(_run_debate())

    # Save to conversation memory for continuity
    _conversation_memory.add_turn(session_id, "user", user_message)
    _conversation_memory.add_turn(session_id, "assistant", answer)

    sources = []
    seen = set()
    for r in results:
        key = f"{r.get('source', '')}:{r['law_name']}"
        if key not in seen:
            seen.add(key)
            sources.append({
                "law_name": r["law_name"],
                "citation": r.get("citation", format_citation(r)),
                "snippet": r["text"][:300],
            })

    return jsonify({
        "answer": answer,
        "sources": sources,
        "session_id": session_id,
        "mode": "debate",
    })


if __name__ == "__main__":
    print(f"\n>>> Qanuni.AI v3.1 server starting on http://localhost:{PORT}")
    print("    Model: GPT-4o-mini via OpenRouter")
    print("    Features: Hybrid Retrieval | Reranking | Reasoning Chain | Case Memory | Debate Mode | Web Search (DuckDuckGo)")
    app.run(host="0.0.0.0", port=PORT, debug=True)
