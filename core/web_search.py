"""
Qanuni.AI — Web Search (DuckDuckGo)
Lets the agent search the internet for Egyptian legal info.
"""

import time
import threading
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


def _ddg_search(query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> list[dict]:
    """Run a DuckDuckGo text search."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.text(query, region="xa-ar", max_results=max_results))
    except Exception as e:
        print(f"[WebSearch] Error: {e}")
        return []


def _ddg_news(query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> list[dict]:
    """Run a DuckDuckGo news search."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            return list(ddgs.news(query, region="xa-ar", max_results=max_results))
    except Exception as e:
        print(f"[WebSearch] News error: {e}")
        return []


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

    results = _ddg_search(f"{query} قانون مصري")
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

    results = _ddg_search(f"{query} حكم محكمة النقض المصرية")
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

    results = _ddg_news(f"{query} قانون مصر")

    if not results:
        results = _ddg_search(f"{query} تعديل قانون مصر جديد")

    formatted = _format_results(results, "أخبار قانونية")

    _set_cache(f"news:{query}", formatted)
    print(f"[WebSearch] News: '{query}' → {len(results)} results")
    return formatted
