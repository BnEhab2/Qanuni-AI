"""
EgyLegalAi — Web Search Tool (DuckDuckGo)
==========================================
ADK-compatible tool functions that let the agent search the internet
for Egyptian legal information: laws, court rulings, legal news, etc.

Uses DuckDuckGo Search (no API key required).

The agent decides when to call these tools based on the user's question.
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


# ── In-memory TTL cache ──────────────────────────────────────────────
_search_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()


def _get_cached(key: str) -> Optional[str]:
    """Return cached result if still valid, else None."""
    with _cache_lock:
        if key in _search_cache:
            ts, result = _search_cache[key]
            if time.time() - ts < WEB_SEARCH_CACHE_TTL:
                return result
            del _search_cache[key]
    return None


def _set_cache(key: str, result: str):
    """Store a result in the TTL cache."""
    with _cache_lock:
        _search_cache[key] = (time.time(), result)
        # Evict old entries (keep cache small)
        if len(_search_cache) > 100:
            oldest_key = min(_search_cache, key=lambda k: _search_cache[k][0])
            del _search_cache[oldest_key]


# ── Core search using DuckDuckGo ─────────────────────────────────────

def _ddg_search(query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> list[dict]:
    """
    Raw DuckDuckGo search. Returns list of result dicts.
    Each dict has: title, href, body.
    """
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(
                query,
                region="xa-ar",   # Arabic region
                max_results=max_results,
            ))
        return results
    except Exception as e:
        print(f"[WebSearch] DuckDuckGo error: {e}")
        return []


def _ddg_news(query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> list[dict]:
    """
    DuckDuckGo news search. Returns list of news result dicts.
    Each dict has: title, url, body, date, source.
    """
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.news(
                query,
                region="xa-ar",
                max_results=max_results,
            ))
        return results
    except Exception as e:
        print(f"[WebSearch] DuckDuckGo news error: {e}")
        return []


def _format_results(results: list[dict], search_type: str = "web") -> str:
    """Format raw DDG results into a readable string for the agent."""
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


# ── ADK Tool Functions ───────────────────────────────────────────────
# These functions are registered as tools on the LlmAgent.
# The agent calls them when it needs to search the web.


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
    cache_key = f"web:{query}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    # Add legal context to the query for better results
    legal_query = f"{query} قانون مصري"
    results = _ddg_search(legal_query)
    formatted = _format_results(results, "بحث عام")

    _set_cache(cache_key, formatted)
    print(f"[WebSearch] General search: '{query}' → {len(results)} results")
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
    cache_key = f"rulings:{query}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    ruling_query = f"{query} حكم محكمة النقض المصرية"
    results = _ddg_search(ruling_query)
    formatted = _format_results(results, "أحكام محاكم")

    _set_cache(cache_key, formatted)
    print(f"[WebSearch] Court rulings search: '{query}' → {len(results)} results")
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
    cache_key = f"news:{query}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    news_query = f"{query} قانون مصر"
    results = _ddg_news(news_query)

    if not results:
        # Fallback to regular search with news-oriented query
        results = _ddg_search(f"{query} تعديل قانون مصر جديد")

    formatted = _format_results(results, "أخبار قانونية")

    _set_cache(cache_key, formatted)
    print(f"[WebSearch] Legal news search: '{query}' → {len(results)} results")
    return formatted
