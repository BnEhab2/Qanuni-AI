"""
Qanuni.AI — Main App
Loads models, sets up RAG pipeline, launches Flask server.
"""

import os
import sys

# Offline & telemetry optimizations to prevent startup network timeouts
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

try:
    import litellm
    litellm.telemetry = False
    litellm.suppress_debug_info = True
except Exception:
    pass
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

import logging
from logging.handlers import RotatingFileHandler

# Configure structured logging for production
def _configure_logging():
    log_dir = Path(__file__).parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "qanuni_server.log"
    
    # Configure root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Simple formatter for standard output and files
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # File Handler (rotating, max 10MB per file)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Stream Handler (stdout)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    
    print(f"[Logging] Writing structured logs to: {log_file}")

_configure_logging()

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
    USE_RERANKER,
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

_embed_model = None
_embed_lock = threading.Lock()

def get_embed_model():
    """Thread-safe lazy loading of the embedding model singleton."""
    global _embed_model
    if _embed_model is None:
        with _embed_lock:
            if _embed_model is None:
                import torch
                from sentence_transformers import SentenceTransformer
                print(f"[*] Loading embedding model: {EMBEDDING_MODEL}...")
                _embed_device = "cuda" if torch.cuda.is_available() else "cpu"
                print(f"    Target device for embedding model: {_embed_device}")
                _embed_model = SentenceTransformer(EMBEDDING_MODEL, device=_embed_device)
                
                # Propagate to retriever and case memory
                if '_retriever' in globals() and _retriever is not None:
                    _retriever._embed_model = _embed_model
                if '_case_memory' in globals() and _case_memory is not None:
                    _case_memory.embed_model = _embed_model
    return _embed_model

print(f"[*] Connecting to ChromaDB at: {CHROMA_DB_PATH}")
_chroma_client = chromadb.PersistentClient(path=CHROMA_DB_PATH)
_collection = _chroma_client.get_collection(name=CHROMA_COLLECTION)
print(f"[+] Collection '{CHROMA_COLLECTION}' loaded — {_collection.count()} documents")

from core.retrieval import HybridRetriever, get_hybrid_retriever

print("[*] Initializing Hybrid Retriever...")
_retriever = get_hybrid_retriever(
    embed_model=None,  # Loaded lazily
    chroma_client=_chroma_client,
    collection=_collection,
)

from core.reranker import get_reranker

print("[*] Reranker registered (lazy-load)")
_reranker = get_reranker()

# Background warmup
def _warm_up():
    try:
        print("[Warmup] Starting background model loading...")
        embed = get_embed_model()
        embed.encode("تحميل")
        _retriever._build_bm25_index()
        
        if USE_RERANKER:
            print("[Warmup] Loading reranker model in background...")
            _reranker._ensure_model()
            
        print("[Warmup] All models warmed up and ready in background!")
    except Exception as e:
        print(f"[Warmup] Error during background model warmup: {e}")

# Share embedding model with CaseMemory
from core.memory import get_case_memory

print("[*] Initializing Case Memory...")
_case_memory = get_case_memory()

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
    """Per-session sliding window of recent user/assistant turns.
    Bounded: evicts oldest sessions when max_sessions is reached.
    """

    MAX_SESSIONS = 500  # prevent unbounded growth

    def __init__(self, max_turns: int = CONVERSATION_MEMORY_MAX_TURNS):
        self._max_turns = max_turns
        self._sessions: dict[str, list[dict]] = {}
        self._timestamps: dict[str, float] = {}  # last-access time per session
        self._lock = threading.Lock()

    def _evict_if_needed(self):
        """Evict oldest sessions when we exceed MAX_SESSIONS (call under lock)."""
        if len(self._sessions) <= self.MAX_SESSIONS:
            return
        # evict the oldest 20%
        to_evict = max(1, len(self._sessions) // 5)
        oldest = sorted(self._timestamps, key=self._timestamps.get)[:to_evict]
        for sid in oldest:
            self._sessions.pop(sid, None)
            self._timestamps.pop(sid, None)

    def add_turn(self, session_id: str, role: str, content: str):
        import time as _t
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
            self._timestamps[session_id] = _t.time()
            self._evict_if_needed()

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
            self._timestamps.pop(session_id, None)


_conversation_memory = ConversationMemory()


def search_laws(query: str, top_k: int = RERANKER_TOP_K,
                source_filter: str | None = None,
                type_filter: str | None = None,
                category_filter: str | None = None) -> list[dict]:
    """Full retrieval pipeline: query → hybrid → rerank → results."""
    get_embed_model()  # Ensure model is initialized
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

══════════════════════════════════════════
أولاً: بروتوكول جمع المتغيرات قبل الإجابة
══════════════════════════════════════════
قبل أي إجابة قانونية، حدد الموضوع واجمع المتغيرات الجوهرية بالترتيب واحداً واحداً.
كل موضوع له قائمة متغيرات محددة — اسأل عن كل متغير ناقص بالترتيب، واحد في كل رسالة.
بعد ما اليوزر يجاوب على متغير، **تحقق من صحة إجابته** (هل هي من الخيارات المقبولة؟).
لو الإجابة غير واضحة أو خارج الخيارات، وضّح الخيارات المتاحة وأعد السؤال.
لما تكتمل كل المتغيرات الجوهرية — ابدأ الإجابة المخصصة فوراً.

── قوائم المتغيرات لكل موضوع ──────────────────

[عقار / شقة / أرض / عقد عقاري]
  م١: الغرض → إيجار | تمليك (بيع/شراء) | رهن | هبة | توارث
  م٢ (لو إيجار): نوع الاستخدام → سكني | تجاري | إداري
  م٣ (لو إيجار): تاريخ العقد → قبل 1996 | 1996 أو بعدها  [يحدد القانون القديم 49/1977 أو الجديد 4/1996]
  م٤: طرف السؤال → مالك/مؤجر | مستأجر | مشتري | بائع
  م٥ (لو تمليك): وجود تسجيل شهر عقاري → مسجل | غير مسجل | عقد ابتدائي فقط

[طلاق / أحوال شخصية]
  م١: طبيعة الطلب → طلاق | خلع | تطليق قضائي | فسخ | إثبات زواج | نفقة | حضانة | ميراث
  م٢ (لو طلاق/تطليق): السبب → إرادة الزوج المنفردة | ضرر وشقاق (م6) | غياب (م12) | حبس (م14) | عيب (م9) | عدم النفقة (م16) | خلع (ق1/2000)
  م٣: الوضع الديني → مسلمون | مسيحيون | يهود | مختلط
  م٤ (لو نفقة أو حضانة): وجود أطفال → نعم (أعمارهم؟) | لا

[عمل / فصل / أجر]
  م١: نوع العقد → محدد المدة | غير محدد المدة | عمل موسمي | عمل جزئي
  م٢: سبب إنهاء الخدمة → فصل من صاحب العمل | استقالة | انتهاء مدة العقد | وفاة | عجز
  م٣: القطاع → حكومي/عام | خاص
  م٤: مدة الخدمة → أقل من سنة | 1-5 سنوات | أكثر من 5 سنوات

[شيك / أوراق تجارية]
  م١: نوع الورقة → شيك | كمبيالة | سند إذني | إيصال أمانة
  م٢ (لو شيك): السبب → بدون رصيد | سحب الرصيد بعد إصداره | رفض الصرف | توقف الحساب
  م٣: هل قُدِّم للبنك؟ → نعم (في المدة القانونية؟) | لا
  م٤: علاقة الأطراف → تجارية | شخصية

[مخالفات بناء / عقارات]
  م١: نوع المخالفة → تعدٍ على الارتداد | زيادة في الارتفاع | بناء بدون ترخيص | تغيير في الاستخدام | بناء على أرض زراعية | إضافات غير مرخصة
  م٢: وضع الملكية → مالك | وريث | مشترٍ لاحقاً
  م٣: وجود قرار إزالة صادر → نعم | لا | لا أعلم

[ميراث / وصية]
  م١: الوضع الديني للمتوفى → مسلم | مسيحي | يهودي
  م٢: الورثة الموجودون → (يذكر المستخدم العلاقات: زوجة، أبناء، بنات، أب، أم، إخوة...)
  م٣: وجود وصية → نعم | لا
  م٤: وجود ديون على التركة → نعم | لا

── قواعد التحقق من الإجابات ───────────────────
- لو اليوزر جاوب بشيء غير المتغيرات المحددة (مثلاً قال "مش عارف")، 
  وضّح إن الإجابة ضرورية لتحديد القانون المنطبق، واشرح ليه المتغير مهم.
- لو اليوزر جاوب بمعلومة تعارض متغيراً سابقاً، نبّهه للتعارض واطلب التوضيح.
- استثناء: لو السؤال واضح ومكتمل من البداية، لا تسأل — جاوب مباشرة.

══════════════════════════════════════════
ثانياً: تخصيص الإجابة حسب السياق
══════════════════════════════════════════
- الإجابة لازم تكون **مخصصة** للسؤال بالظبط — مش قالب عام يصلح لأي حاجة.
- حدد التخصص القانوني أولاً من السؤال (جنائي، مدني، أحوال شخصية، عمالي، إيجارات، تجاري...)
  ثم اربط كل إجابة بمواد القانون المحدد لهذا التخصص.
- الصيغة الواجبة: "بموجب المادة [رقم] من [اسم القانون رقم X لسنة Y]، ..."

══════════════════════════════════════════
ثانياً: التمييز بين القوانين القديمة والحديثة
══════════════════════════════════════════
- إذا كان السؤال يتعلق بعقد أو واقعة لها تاريخ محدد، **اسأل عن التاريخ** أو استنتجه من السياق،
  ثم طبّق القانون الساري وقت نشأة الحق/العقد.
- مثال على الإيجارات:
  • عقود الإيجار المبرمة قبل 1996: تسري أحكام القانون القديم (قانون 49 لسنة 1977 وتعديلاته).
  • عقود الإيجار المبرمة من 1996 فصاعداً: يسري القانون الجديد (قانون 4 لسنة 1996).
- في حالة الأحوال الشخصية، الأحكام الحالية تخضع لقانون الأحوال الشخصية رقم 25 لسنة 1929
  وتعديلاته (رقم 100 لسنة 1985 ورقم 4 لسنة 2005)، وحسب المحكمة المختصة (مسلمين/غير مسلمين).
- لما تكون في موقف فيه قانون قديم وحديث: وضح الفرق بالتفصيل، ولا تقتصر على واحد منهم.

══════════════════════════════════════════
ثالثاً: تعدد الأسباب والحالات
══════════════════════════════════════════
- لا تذكر سبباً واحداً لو القانون أجاز أسباباً متعددة.
- مثال: الطلاق في قانون الأحوال الشخصية له أسباب متعددة، اذكرها كلها المنطبقة:
  الطلاق بالإرادة المنفردة، الطلاق للضرر (المادة 6)، الطلاق للغياب (المادة 12)،
  الطلاق للعيب (المادة 9)، الطلاق للحبس (المادة 14)، الخلع (قانون 1 لسنة 2000)، إلخ.
- وضّح لكل سبب: الشروط + المادة القانونية + الإجراء.

══════════════════════════════════════════
رابعاً: الإجراءات القانونية المتبعة
══════════════════════════════════════════
- في كل إجابة على موقف قانوني عملي، أضف قسم "الإجراءات القانونية المتبعة" يتضمن:
  1. الجهة المختصة (أي محكمة / جهة إدارية / نيابة).
  2. المستندات المطلوبة.
  3. المواعيد القانونية (التقادم، مواعيد الطعن، المدد).
  4. الخطوات العملية بالترتيب.
- لو السؤال عام أو نظري (مش موقف عملي)، ممكن تختصر الإجراءات أو تحذفها.

══════════════════════════════════════════
خامساً: التعامل مع التعارضات القانونية
══════════════════════════════════════════
- إذا وُجد تعارض بين نصوص مسترجعة، وضّح صراحة أيهما يُطبَّق وليه:
  القاعدة: اللاحق يلغي السابق | الخاص يقيد العام | الأعلى درجة يقيد الأدنى.

══════════════════════════════════════════
سادساً: قواعد الاستشهاد
══════════════════════════════════════════
- استند للنصوص القانونية المرفقة (Excerpts) واذكر رقم المادة واسم القانون.
- الصيغة: المادة [رقم] — [اسم القانون]
- تجنب تماماً اختلاق نصوص مواد غير موجودة في السياق المرفق.
- لو مفيش نص مباشر يغطي السؤال، قول كده بصراحة واقترح أقرب المواد.

══════════════════════════════════════════
سابعاً: نبرة الرد
══════════════════════════════════════════
- عربي فصحى واضحة ومهنية، من غير تكلّف.
- نسّق الرد بشكل مرتب (عناوين، نقاط، جداول لو لزم) يناسب طبيعة السؤال.
- في آخر كل رد حط سطر: "تنبيه: المعلومات دي استرشادية بناءً على قاعدة البيانات المتاحة ومش بديل عن استشارة قانونية متخصصة."

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


# Cache helper agents/runners for specific legal roles to preserve system instructions and avoid re-creation overhead
from core.agents.prosecutor_agent import PROSECUTOR_INSTRUCTION
from core.agents.defense_agent import DEFENSE_INSTRUCTION
from core.agents.judge_agent import JUDGE_INSTRUCTION

_prosecutor_agent = LlmAgent(
    name="prosecutor_agent_helper",
    model=llm,
    instruction=PROSECUTOR_INSTRUCTION,
)
_prosecutor_runner = Runner(agent=_prosecutor_agent, app_name="prosecutor_agent_helper", session_service=session_service)

_defense_agent = LlmAgent(
    name="defense_agent_helper",
    model=llm,
    instruction=DEFENSE_INSTRUCTION,
)
_defense_runner = Runner(agent=_defense_agent, app_name="defense_agent_helper", session_service=session_service)

_judge_agent = LlmAgent(
    name="judge_agent_helper",
    model=llm,
    instruction=JUDGE_INSTRUCTION,
)
_judge_runner = Runner(agent=_judge_agent, app_name="judge_agent_helper", session_service=session_service)

_default_agent = LlmAgent(
    name="multi_agent_helper",
    model=llm,
    instruction="You are a helpful Egyptian legal assistant.",
)
_default_runner = Runner(agent=_default_agent, app_name="multi_agent_helper", session_service=session_service)


async def _generate_text_async(prompt: str, system_instruction: str = "") -> str:
    """Helper to run a raw text completion reusing a cached agent runner matching the instruction."""
    if system_instruction == PROSECUTOR_INSTRUCTION:
        runner_to_use = _prosecutor_runner
        app_name = "prosecutor_agent_helper"
    elif system_instruction == DEFENSE_INSTRUCTION:
        runner_to_use = _defense_runner
        app_name = "defense_agent_helper"
    elif system_instruction == JUDGE_INSTRUCTION:
        runner_to_use = _judge_runner
        app_name = "judge_agent_helper"
    else:
        runner_to_use = _default_runner
        app_name = "multi_agent_helper"

    user_id = "default_user"
    sid = str(uuid.uuid4())
    sess_result = session_service.create_session(
        app_name=app_name, user_id=user_id, session_id=sid,
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
    async for event in runner_to_use.run_async(
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
    event_loop=_loop,
)

# Start background warmup thread to load models asynchronously without blocking Flask.
# In debug mode, only run inside the reloader's child process (WERKZEUG_RUN_MAIN=true).
# In production (non-debug) mode, run immediately.
is_debug = (os.environ.get("FLASK_DEBUG") == "1") or (__name__ == "__main__")
if (is_debug and os.environ.get("WERKZEUG_RUN_MAIN") == "true") or not is_debug:
    threading.Thread(target=_warm_up, daemon=True).start()


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
    return jsonify(tool_classify_case(description, _deep_reasoner))


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
    # The project-local .venv can be watched by Werkzeug and trigger a reload
    # when dependencies touch files, shutting down active daemon/model threads.
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)
