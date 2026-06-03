"""
Qanuni.AI — Main App
Loads models, sets up RAG pipeline, launches Flask server.
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

# Fix Windows terminal encoding
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

# ── Download DB for HuggingFace Spaces ────────────────────────────────
import urllib.request
import zipfile

def _download_file(url, dest_path):
    req = urllib.request.Request(url)
    hf_token = os.getenv("HF_TOKEN")
    if hf_token:
        req.add_header("Authorization", f"Bearer {hf_token}")

    with urllib.request.urlopen(req) as response, open(dest_path, 'wb') as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)

def _check_and_download_db():
    chroma_path = Path(CHROMA_DB_PATH)
    data_path = Path(MERGED_CORPUS_PATH)

    chroma_url = os.getenv("HF_CHROMA_DB_URL")
    corpus_url = os.getenv("HF_CORPUS_URL")

    db_file = chroma_path / "chroma.sqlite3"
    if chroma_url and (not chroma_path.exists() or not db_file.exists()):
        print(f"[*] Downloading ChromaDB: {chroma_url}...")
        try:
            zip_tmp = chroma_path.parent / "chroma_db_temp.zip"
            zip_tmp.parent.mkdir(parents=True, exist_ok=True)
            _download_file(chroma_url, zip_tmp)
            print("    Extracting...")
            with zipfile.ZipFile(zip_tmp, 'r') as zf:
                zf.extractall(chroma_path.parent)
            os.remove(zip_tmp)
            print("[+] ChromaDB ready!")
        except Exception as e:
            print(f"[!] ChromaDB download error: {e}")

    if corpus_url and not data_path.exists():
        print(f"[*] Downloading corpus: {corpus_url}...")
        try:
            data_path.parent.mkdir(parents=True, exist_ok=True)
            _download_file(corpus_url, data_path)
            print("[+] Corpus ready!")
        except Exception as e:
            print(f"[!] Corpus download error: {e}")

_check_and_download_db()

# ── Load Models ───────────────────────────────────────────────────────
import chromadb
from sentence_transformers import SentenceTransformer

import torch
torch.set_num_threads(TORCH_NUM_THREADS)

print(f"[*] Loading embedding model: {EMBEDDING_MODEL}")
_embed_model = SentenceTransformer(EMBEDDING_MODEL)

print(f"[*] Connecting to ChromaDB at: {CHROMA_DB_PATH}")
_chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
_collection = _chroma_client.get_collection(name=CHROMA_COLLECTION)
print(f"[+] Collection '{CHROMA_COLLECTION}' loaded — {_collection.count()} documents")

from core.retrieval import HybridRetriever, get_hybrid_retriever

print("[*] Initializing Hybrid Retriever...")
_retriever = get_hybrid_retriever(
    embed_model=_embed_model,
    chroma_client=_chroma_client,
    collection=_collection,
)

from core.reranker import get_reranker

print("[*] Reranker registered (lazy-load)")
_reranker = get_reranker()

# Background warmup
def _warm_up():
    try:
        print("[Warmup] Starting...")
        _embed_model.encode("تحميل")
        _retriever._build_bm25_index()
        _reranker._ensure_model()
        print("[Warmup] Done!")
    except Exception as e:
        print(f"[Warmup] Error: {e}")

if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    threading.Thread(target=_warm_up, daemon=True).start()

# Share embedding model with CaseMemory
from core.memory import get_case_memory

print("[*] Initializing Case Memory...")
_case_memory = get_case_memory()
_case_memory.embed_model = _embed_model

from core.reasoning import LegalReasoningChain
from core.deep_reasoning import DeepLegalReasoner
from core.contradiction import ContradictionDetector
from core.confidence import ConfidenceScorer

_deep_reasoner = DeepLegalReasoner()
_contradiction_detector = ContradictionDetector()
_confidence_scorer = ConfidenceScorer(memory_store=_case_memory)


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
    """Per-session sliding window of recent user/assistant turns."""

    def __init__(self, max_turns: int = CONVERSATION_MEMORY_MAX_TURNS):
        self._max_turns = max_turns
        self._sessions: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def add_turn(self, session_id: str, role: str, content: str):
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = []
            self._sessions[session_id].append({
                "role": role,
                "content": content[:2000],
            })
            max_msgs = self._max_turns * 2
            if len(self._sessions[session_id]) > max_msgs:
                self._sessions[session_id] = self._sessions[session_id][-max_msgs:]

    def get_context(self, session_id: str) -> str:
        with self._lock:
            history = self._sessions.get(session_id, [])

        if not history:
            return ""

        parts = ["### سياق المحادثة السابقة:"]
        for msg in history:
            label = "المستخدم" if msg["role"] == "user" else "المستشار"
            parts.append(f"**{label}**: {msg['content'][:500]}")

        return "\n".join(parts)

    def clear(self, session_id: str):
        with self._lock:
            self._sessions.pop(session_id, None)


_conversation_memory = ConversationMemory()


def search_laws(query: str, top_k: int = RERANKER_TOP_K,
                source_filter: str | None = None,
                type_filter: str | None = None,
                category_filter: str | None = None) -> list[dict]:
    """Full retrieval pipeline: query → hybrid → rerank → results."""
    results = tool_search_laws(
        query=query,
        retriever=_retriever,
        reranker=_reranker,
        top_k=top_k,
        source_filter=source_filter,
        type_filter=type_filter,
        category_filter=category_filter,
    )
    gc.collect()
    return results


SYSTEM_PROMPT = """أنت Qanuni.AI — مساعد قانوني ذكي متخصص في القانون المصري.
أنت بتتكلم مع محامين ومستشارين قانونيين، فالكلام بينك وبينهم لازم يكون مهني ومحترم لكن طبيعي — زي زميل شاطر بيساعدك.

قواعدك الأساسية:
- اتكلم عربي فصحى واضحة ومهنية، لكن من غير تكلّف أو تكرار نفس القالب كل مرة.
- استند للنصوص القانونية المرفقة (Excerpts) واذكر رقم المادة واسم القانون لما تستشهد.
- إذا وجدت تعارضات أو اختلافات واضحة بين القوانين أو المواد المسترجعة، وضّح هذا التعارض صراحة للمستخدم واشرح كيف يمكن حله (مثلاً: اللاحق يلغي السابق، أو الخاص يقيد العام).
- تجنب تماماً الهلوسة أو اختلاق نصوص مواد قانونية غير موجودة في السياق المرفق.
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

# ── LLM Setup ─────────────────────────────────────────────────────────
openrouter_key = os.getenv("OPENROUTER_API_KEY", "").strip()
gemini_key = os.getenv("GEMINI_API_KEY", "").strip()

if openrouter_key:
    print("[*] Using OpenRouter (gpt-4o-mini)")
    llm = LiteLlm(
        model="openrouter/openai/gpt-4o-mini",
        api_key=openrouter_key,
        api_base="https://openrouter.ai/api/v1",
        temperature=0.5,
        max_output_tokens=2048,
    )
elif gemini_key:
    print("[*] Using Gemini (fallback)")
    llm = LiteLlm(
        model="gemini/gemini-1.5-flash",
        api_key=gemini_key,
        temperature=0.5,
        max_output_tokens=2048,
    )
else:
    print("[!] WARNING: No API key configured!")
    llm = LiteLlm(
        model="openrouter/openai/gpt-4o-mini",
        api_key="",
        api_base="https://openrouter.ai/api/v1",
        temperature=0.5,
        max_output_tokens=2048,
    )

# ── Agents ────────────────────────────────────────────────────────────
qanuni_agent = LlmAgent(
    name="qanuni_agent",
    model=llm,
    description="Qanuni.AI — Egyptian Legal AI Agent",
    instruction=SYSTEM_PROMPT,
    tools=[tool_search_web, tool_search_court_rulings, tool_search_legal_news],
)

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

# ── ADK Session & Runners ────────────────────────────────────────────
session_service = InMemorySessionService()
runner = Runner(agent=qanuni_agent, app_name="qanuni", session_service=session_service)
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

# ── Async Event Loop ─────────────────────────────────────────────────
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()

def run_async(coro):
    """Run an async coroutine on the persistent event loop."""
    return asyncio.run_coroutine_threadsafe(coro, _loop).result(timeout=120)


async def _run_agent_async(
    user_message: str,
    law_excerpts: list[dict],
    reasoning_context: str = "",
    conversation_context: str = "",
    citation_block: str = "",
    web_search_hint: str = "",
) -> str:
    """Send a message to the agent and collect the response."""
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
        header = (
            f"--- Excerpt {i} ---\n"
            f"Law: {ex['law_name']}\n"
            f"Source: {ex.get('source', '')}\n"
            f"Type: {ex.get('type', '')}\n"
            f"Categories: {ex['categories']}\n"
        )
        citation = ex.get("citation", format_citation(ex))
        header += f"Citation: {citation}\n"

        if ex.get('summary'):
            header += f"Summary: {ex['summary'][:300]}\n"
        if ex.get("retrieval_method"):
            header += f"Retrieval: {ex['retrieval_method']}\n"
        if ex.get("reranker_score") is not None:
            header += f"Reranker Score: {ex['reranker_score']:.4f}\n"

        header += f"Text: {ex['text'][:1500]}\n"
        context_parts.append(header)

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
    sid = str(uuid.uuid4())

    sess_result = session_service.create_session(
        app_name="qanuni", user_id=user_id, session_id=sid,
    )
    if inspect.iscoroutine(sess_result):
        session = await sess_result
    else:
        session = sess_result

    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt)],
    )

    response_parts = []
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content,
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
            user_message, law_excerpts,
            reasoning_context=reasoning_context,
            conversation_context=conversation_context,
            citation_block=citation_block,
            web_search_hint=web_search_hint,
        )
    )


async def _generate_text_async(prompt: str, system_instruction: str = "") -> str:
    """Helper to run a raw text completion using a temporary agent runner."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    
    agent = LlmAgent(
        name="multi_agent_helper",
        model=llm,
        instruction=system_instruction or "You are a helpful Egyptian legal assistant.",
    )
    temp_runner = Runner(agent=agent, app_name="multi_agent_helper", session_service=session_service)
    
    user_id = "default_user"
    sid = str(uuid.uuid4())
    sess_result = session_service.create_session(
        app_name="multi_agent_helper", user_id=user_id, session_id=sid,
    )
    if inspect.iscoroutine(sess_result):
        session = await sess_result
    else:
        session = sess_result
        
    content = types.Content(
        role="user",
        parts=[types.Part.from_text(text=prompt)],
    )
    
    response_parts = []
    async for event in temp_runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    response_parts.append(part.text)
                    
    return "".join(response_parts) if response_parts else ""


def generate_text(prompt: str, system_instruction: str = "") -> str:
    """Synchronous wrapper for text generation."""
    return run_async(_generate_text_async(prompt, system_instruction))


# ── Flask App ─────────────────────────────────────────────────────────
from api import create_app
from api.chat import init_chat_services

app = create_app()

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
    deep_reasoner=_deep_reasoner,
    contradiction_detector=_contradiction_detector,
    confidence_scorer=_confidence_scorer,
    generate_text_fn=generate_text,
)


@app.route("/api/case-memory/<int:case_id>", methods=["GET"])
def get_case(case_id: int):
    case = _case_memory.summarize_case(case_id)
    if not case:
        return jsonify({"error": "Case not found"}), 404
    return jsonify(case)


@app.route("/api/case-memory/search", methods=["POST"])
def search_cases():
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "Empty query"}), 400

    similar = _case_memory.retrieve_similar_cases(
        query=query,
        top_k=data.get("top_k", 3),
        category_filter=data.get("category"),
    )
    return jsonify({"cases": similar})


@app.route("/api/case-memory/recent", methods=["GET"])
def recent_cases():
    limit = request.args.get("limit", 10, type=int)
    return jsonify({"cases": _case_memory.get_recent_cases(limit=limit)})


@app.route("/api/classify", methods=["POST"])
def classify():
    data = request.get_json(force=True)
    description = data.get("description", "").strip()
    if not description:
        return jsonify({"error": "Empty description"}), 400
    return jsonify(tool_classify_case(description, LegalReasoningChain()))


@app.route("/api/web-search", methods=["POST"])
def web_search():
    data = request.get_json(force=True)
    query = data.get("query", "").strip()
    search_type = data.get("type", "general")

    if not query:
        return jsonify({"error": "Empty query"}), 400

    if search_type == "rulings":
        result = tool_search_court_rulings(query)
    elif search_type == "news":
        result = tool_search_legal_news(query)
    else:
        result = tool_search_web(query)

    return jsonify({"results": result, "type": search_type, "query": query})


if __name__ == "__main__":
    print(f"\n>>> Qanuni.AI v3.1 — http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=True)
