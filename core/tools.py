"""
Qanuni.AI — Legal Tools
Tool functions for the agent: search, classify, compare, cite.
"""

import re
from typing import Optional

from config import (
    TOP_K,
    RERANKER_TOP_K,
    HYBRID_CANDIDATE_K,
)


def search_laws(
    query: str,
    retriever,
    reranker,
    top_k: int = RERANKER_TOP_K,
    source_filter: Optional[str] = None,
    type_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
) -> list[dict]:
    """Search legal corpus: hybrid retrieval → reranking → top-K results."""
    candidates = retriever.hybrid_search(
        query=query,
        top_k=HYBRID_CANDIDATE_K,
        source_filter=source_filter,
        type_filter=type_filter,
        category_filter=category_filter,
    )

    if not candidates:
        return []

    reranked = reranker.rerank(query=query, documents=candidates, top_k=top_k)

    for doc in reranked:
        doc["citation"] = format_citation(doc)

    return reranked


def retrieve_articles(
    law_name: str,
    article_number: Optional[str],
    retriever,
    reranker,
    top_k: int = 5,
) -> list[dict]:
    """Retrieve specific articles from a named law."""
    query_parts = [law_name]
    if article_number:
        query_parts.append(f"المادة {article_number}")
    query = " ".join(query_parts)

    candidates = retriever.hybrid_search(query=query, top_k=top_k * 2)
    if not candidates:
        return []

    reranked = reranker.rerank(query=query, documents=candidates, top_k=top_k)

    for doc in reranked:
        doc["citation"] = format_citation(doc)
        doc["article_number"] = _extract_article_number(doc.get("text", ""))

    return reranked


def classify_case(description: str, reasoning_chain) -> dict:
    """Classify a legal case by domain using the reasoning chain."""
    reasoning_chain.reset()
    facts = reasoning_chain.identify_facts(description)
    classification = reasoning_chain.classify_case(facts)

    return {
        "primary_category": classification["primary_category"],
        "secondary_categories": classification.get("secondary_categories", []),
        "confidence_scores": classification.get("confidence_scores", {}),
        "detected_facts": facts.get("facts", {}),
    }


def retrieve_precedents(
    case_type: str,
    facts_summary: str,
    case_memory,
    top_k: int = 3,
) -> list[dict]:
    """Find similar past cases from case memory."""
    similar = case_memory.retrieve_similar_cases(
        query=facts_summary,
        top_k=top_k,
        category_filter=case_type if case_type != "other" else None,
    )

    return [
        {
            "case_id":      c["id"],
            "user_query":   c["user_query"],
            "case_summary": c["case_summary"][:500],
            "category":     c["category"],
            "similarity":   round(c["similarity"], 4),
            "created_at":   c["created_at"],
        }
        for c in similar
    ]


def compare_cases(case_a: dict, case_b: dict) -> dict:
    """Compare two legal cases: shared laws, unique laws, same category."""
    comparison = {
        "case_a_query":    case_a.get("user_query", ""),
        "case_b_query":    case_b.get("user_query", ""),
        "case_a_category": case_a.get("category", "other"),
        "case_b_category": case_b.get("category", "other"),
        "same_category":   case_a.get("category") == case_b.get("category"),
        "case_a_laws":     case_a.get("retrieved_laws", []),
        "case_b_laws":     case_b.get("retrieved_laws", []),
    }

    a_names = {l.get("law_name", "") for l in comparison["case_a_laws"]}
    b_names = {l.get("law_name", "") for l in comparison["case_b_laws"]}
    comparison["shared_laws"] = list(a_names & b_names)
    comparison["unique_to_a"] = list(a_names - b_names)
    comparison["unique_to_b"] = list(b_names - a_names)

    return comparison


def generate_legal_strategy(facts: dict, laws: list[dict], analysis: dict) -> dict:
    """Build a legal strategy from facts, laws, and reasoning analysis."""
    prosecution = analysis.get("prosecution", {})
    defense = analysis.get("defense", {})

    return {
        "category": analysis.get("primary_category", "other"),
        "facts_summary": facts.get("facts", {}).get("subject", ""),
        "applicable_laws_count": len(laws),
        "prosecution_perspective": {
            "supporting_laws": prosecution.get("supporting_laws_count", 0),
            "key_articles": [
                format_citation(law)
                for law in prosecution.get("supporting_laws", [])[:3]
            ],
        },
        "defense_perspective": {
            "supporting_laws": defense.get("supporting_laws_count", 0),
            "key_articles": [
                format_citation(law)
                for law in defense.get("supporting_laws", [])[:3]
            ],
        },
        "all_citations": [format_citation(law) for law in laws],
    }


def format_citation(result: dict) -> str:
    """Format a legal citation like: المادة 318 — قانون العقوبات (source)"""
    law_name = result.get("law_name", "غير محدد")
    source = result.get("source", "")
    text = result.get("text", "")

    article_num = _extract_article_number(text)

    parts = []
    if article_num:
        parts.append(f"المادة {article_num}")
    if law_name and law_name != "غير محدد":
        parts.append(f"— {law_name}")
    if source:
        parts.append(f"({source})")

    return " ".join(parts) if parts else f"{law_name} ({source})"


def _extract_article_number(text: str) -> Optional[str]:
    """Extract article number from legal text (e.g. مادة 318)."""
    patterns = [
        r'(?:ال)?مادة\s*(?:رقم\s*)?\(?\s*(\d+)\s*\)?',
        r'(?:ال)?مادة\s*(?:رقم\s*)?\(?\s*(\d+\s*(?:مكرر(?:اً)?)?(?:\s*\([أ-ي]\))?)\s*\)?',
        r'Article\s+(\d+)',
    ]

    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()

    return None


def build_citation_block(results: list[dict]) -> str:
    """Build a formatted citation block for all results."""
    if not results:
        return ""

    citations = []
    seen = set()
    for r in results:
        citation = format_citation(r)
        if citation not in seen:
            seen.add(citation)
            citations.append(citation)

    if not citations:
        return ""

    header = "\n\n### المراجع القانونية المستخدمة:\n"
    body = "\n".join(f"- {c}" for c in citations)
    return header + body
