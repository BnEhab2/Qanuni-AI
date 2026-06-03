"""
Qanuni.AI — Web Search (DuckDuckGo)
Lets the agent search the internet for Egyptian legal info.
"""

import time
import threading
import re
import string
from typing import Optional
from functools import lru_cache

from config import (
    WEB_SEARCH_MAX_RESULTS,
    WEB_SEARCH_CACHE_TTL,
    WEB_SEARCH_LEGAL_DOMAINS,
)

# Simple TTL cache
_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _get_cached(key: str) -> Optional[str]:
    """Return cached result if still valid."""
    with _cache_lock:
        if key in _cache:
            ts, result = _cache[key]
            if time.time() - ts < WEB_SEARCH_CACHE_TTL:
                return result
            del _cache[key]
    return None


def _set_cache(key: str, result: str):
    """Store result in cache."""
    with _cache_lock:
        _cache[key] = (time.time(), result)
        if len(_cache) > 100:
            oldest = min(_cache, key=lambda k: _cache[k][0])
            del _cache[oldest]


def _extract_search_keywords(query: str) -> str:
    """Extract key search terms from a long conversational query to optimize search performance."""
    words = query.strip().split()
    if len(words) <= 7:
        return query

    # Common Arabic stop words, particles, and search noise
    stop_words = {
        "هو", "هي", "هم", "هن", "أنا", "نحن", "أنت", "أنتم", "من", "في", "على", "إلى", "عن", 
        "مع", "حتى", "أو", "أم", "ثم", "بل", "لكن", "لقد", "قد", "كان", "كانت", "كانوا", "يكن",
        "تم", "قام", "أقام", "وجاب", "فيها", "فيه", "منها", "إليها", "عليها", "بينما", "حيث", 
        "عندما", "التي", "الذي", "الذين", "اللذين", "اللذان", "هذا", "هذه", "هؤلاء", "ذلك", 
        "تلك", "هناك", "ليس", "ليست", "لم", "لن", "لا", "ما", "إذ", "إذا", "بما", "كما", "أنه",
        "أنها", "أنهم", "إن", "أن", "لكي", "كي", "كل", "بعض", "جميع", "أما", "أثناء", "خلال",
        "بعد", "قبل", "تحت", "فوق", "بين", "أمام", "خلف", "وراء", "سنة", "سنوات", "عام", "أعوام",
        "يوم", "أيام", "شهر", "شهور", "أسبوع", "أسابيع", "ساعة", "ساعات", "دقيقة", "دقائق",
        "مغني", "مغنية", "راب", "الراب", "حفلة", "حفله", "حفلات", "الشيخ", "الشيخين", "للشيخ",
        "بسبب", "ضد", "لأن", "لأنه", "عبر", "بشكل", "طريقة", "طبيعة", "حالة", "حالات"
    }

    # Clean punctuation
    cleaned = query.translate(str.maketrans("", "", string.punctuation + "«»""''"""))
    raw_words = cleaned.split()

    filtered = [w for w in raw_words if w.lower() not in stop_words and len(w) >= 3]
    if len(filtered) < 3:
        filtered = [w for w in raw_words if w.lower() not in stop_words]

    if len(filtered) <= 6:
        return " ".join(filtered)

    # Smart heuristic: keep first 3 words (usually main subjects)
    # plus the 4 longest words from the rest (usually specific topic nouns)
    first_part = filtered[:3]
    remaining_part = filtered[3:]
    
    longest_remaining = sorted(remaining_part, key=len, reverse=True)[:4]
    
    # Reassemble in original order of appearance
    selected_words = []
    for w in raw_words:
        if w in first_part or w in longest_remaining:
            if w not in selected_words:
                selected_words.append(w)
                
    return " ".join(selected_words) if selected_words else query


def _ddg_search(query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> list[dict]:
    """Run a DuckDuckGo text search using lite backend with auto fallback."""
    # Pre-process search query to extract keywords if it is a long conversational text
    optimized_query = _extract_search_keywords(query)
    print(f"[WebSearch] Original query: '{query[:60]}...' -> Optimized: '{optimized_query}'")
    
    try:
        from duckduckgo_search import DDGS
        # Use lite backend (extremely resilient to Cloudflare blocks/bot detection)
        with DDGS() as ddgs:
            results = list(ddgs.text(optimized_query, region="xa-ar", backend="lite", max_results=max_results))
            if results:
                return results
            
            # Fallback to auto backend
            results = list(ddgs.text(optimized_query, region="xa-ar", backend="auto", max_results=max_results))
            if results:
                return results
    except Exception as e:
        print(f"[WebSearch] Search failed for '{optimized_query}': {e}")

    # Final fallback: search without region restriction
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(optimized_query, backend="lite", max_results=max_results))
    except Exception as e:
        print(f"[WebSearch] Fallback search failed: {e}")
        return []


def _ddg_news(query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> list[dict]:
    """Run a DuckDuckGo news search with fallback to general search."""
    optimized_query = _extract_search_keywords(query)
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            # News endpoint is often rate-limited/blocked (403), so we try it but prepare to catch
            return list(ddgs.news(optimized_query, region="xa-ar", max_results=max_results))
    except Exception as e:
        print(f"[WebSearch] News endpoint failed for '{optimized_query}': {e}. Falling back to lite text search.")
        
    # Fallback to general lite search with "أخبار" keyword
    return _ddg_search(f"{optimized_query} أخبار", max_results=max_results)


def _format_results(results: list[dict], search_type: str = "web") -> str:
    """Format search results into readable text."""
    if not results:
        return "لم يتم العثور على نتائج."

    parts = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "بدون عنوان")
        url = r.get("href", r.get("url", ""))
        body = r.get("body", "")[:500]
        source = r.get("source", "")
        date = r.get("date", "")

        entry = f"--- نتيجة {i} ---\n"
        entry += f"العنوان: {title}\n"
        if source:
            entry += f"المصدر: {source}\n"
        if date:
            entry += f"التاريخ: {date}\n"
        if url:
            entry += f"الرابط: {url}\n"
        entry += f"المحتوى: {body}\n"
        parts.append(entry)

    header = f"[🌐 نتائج البحث على الإنترنت ({search_type}) — {len(results)} نتيجة]\n\n"
    return header + "\n".join(parts)


# ADK Tool Functions (called by the agent)

def search_web(query: str) -> str:
    """
    ابحث على الإنترنت عن معلومات قانونية مصرية.
    استخدم هذه الأداة عندما:
    - السؤال يتعلق بأحكام محاكم حديثة أو تعديلات تشريعية جديدة
    - النصوص القانونية المسترجعة غير كافية للإجابة
    - المستخدم يسأل عن أخبار أو أحداث قانونية حديثة
    - تحتاج معلومات إضافية غير موجودة في قاعدة البيانات

    Args:
        query: استعلام البحث (بالعربية أو الإنجليزية)

    Returns:
        نتائج البحث من الإنترنت مع العناوين والروابط والمحتوى
    """
    cached = _get_cached(f"web:{query}")
    if cached:
        return cached

    # Try specific query with suffix
    results = _ddg_search(f"{query} قانون مصري")
    # Fallback to clean query without suffix
    if not results:
        results = _ddg_search(query)

    formatted = _format_results(results, "بحث عام")
    _set_cache(f"web:{query}", formatted)
    print(f"[WebSearch] General: '{query}' → {len(results)} results")
    return formatted


def search_court_rulings(query: str) -> str:
    """
    ابحث عن أحكام محاكم مصرية وأحكام نقض.
    استخدم هذه الأداة عندما:
    - المستخدم يسأل عن أحكام سابقة أو سوابق قضائية
    - تحتاج أحكام محكمة النقض أو المحكمة الدستورية
    - المستخدم يريد معرفة اتجاه المحاكم في مسألة معينة

    Args:
        query: وصف القضية أو الموضوع القانوني المراد البحث عن أحكام له

    Returns:
        أحكام المحاكم ذات الصلة مع تفاصيلها
    """
    cached = _get_cached(f"rulings:{query}")
    if cached:
        return cached

    # Try specific queries, falling back to broader terms
    results = _ddg_search(f"{query} حكم محكمة النقض المصرية")
    if not results:
        results = _ddg_search(f"{query} حكم نقض")
    if not results:
        results = _ddg_search(query)

    formatted = _format_results(results, "أحكام محاكم")
    _set_cache(f"rulings:{query}", formatted)
    print(f"[WebSearch] Rulings: '{query}' → {len(results)} results")
    return formatted


def search_legal_news(query: str) -> str:
    """
    ابحث عن أخبار قانونية وتعديلات تشريعية حديثة في مصر.
    استخدم هذه الأداة عندما:
    - المستخدم يسأل عن تعديلات قانونية جديدة
    - المستخدم يريد آخر الأخبار القانونية
    - الموضوع يتعلق بتشريعات حديثة أو مشاريع قوانين

    Args:
        query: الموضوع القانوني المراد البحث عن أخباره

    Returns:
        أحدث الأخبار والتعديلات التشريعية ذات الصلة
    """
    cached = _get_cached(f"news:{query}")
    if cached:
        return cached

    # Try news searches, falling back to general search
    results = _ddg_news(f"{query} قانون مصر")
    if not results:
        results = _ddg_news(query)
    if not results:
        results = _ddg_search(f"{query} أخبار")

    formatted = _format_results(results, "أخبار قانونية")
    _set_cache(f"news:{query}", formatted)
    print(f"[WebSearch] News: '{query}' → {len(results)} results")
    return formatted
