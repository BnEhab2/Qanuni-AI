"""
Qanuni.AI — Deep Legal Reasoning Engine
========================================
8-step structured chain-of-thought reasoning pipeline.
Replaces shallow keyword matching with multi-signal analysis.

Steps:
    1. Semantic Fact Extraction (enhanced keyword + pattern matching)
    2. Multi-signal Classification (keyword + score distribution)
    3. Article Relevance Scoring (per-article applicability)
    4. Article-to-Article Link Graph (cross-reference expansion)
    5. Contradiction Detection (delegated to contradiction module)
    6. Dual-Perspective Analysis (prosecution + defense with evidence quality)
    7. Legal Risk Assessment (severity × probability matrix)
    8. Evidence-Based Conclusion (structured reasoning trace)
"""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from config import (
    LEGAL_CATEGORIES,
    REASONING_MAX_RISK_ITEMS,
)


def _safe_parse_json(response_text: str, default=None):
    """Safely extract and parse the first JSON object from an LLM response string."""
    if not response_text:
        return default
    try:
        start = response_text.find("{")
        end = response_text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(response_text[start:end+1])
    except (json.JSONDecodeError, ValueError):
        pass
    return default



# ── Data Structures ──────────────────────────────────────────────────

@dataclass
class ReasoningStep:
    """Single step in the reasoning chain."""
    step_number: int
    step_name: str
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    confidence: float = 1.0
    evidence_refs: list = field(default_factory=list)
    duration_ms: float = 0.0


@dataclass
class RiskItem:
    """Single legal risk entry."""
    issue: str
    severity: str          # "high", "medium", "low"
    probability: str       # "high", "medium", "low"
    risk_level: str        # computed composite
    related_articles: list = field(default_factory=list)
    description: str = ""


@dataclass
class ReasoningTrace:
    """Complete reasoning output from the 8-step chain."""
    steps: list = field(default_factory=list)
    primary_category: str = "other"
    secondary_categories: list = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    article_links: dict = field(default_factory=dict)
    contradictions: list = field(default_factory=list)
    risk_assessment: list = field(default_factory=list)
    overall_confidence: float = 0.0
    reasoning_context: str = ""
    prosecution: dict = field(default_factory=dict)
    defense: dict = field(default_factory=dict)
    judge_decision: dict = field(default_factory=dict)
    entities: dict = field(default_factory=dict)
    timeline: dict = field(default_factory=dict)
    success_probability_data: dict = field(default_factory=dict)


# ── Cross-Reference Patterns ────────────────────────────────────────

# Patterns for detecting article cross-references in Arabic legal texts
_CROSS_REF_PATTERNS = [
    # "وفقاً للمادة 52" / "طبقاً للمادة 52"
    r'(?:وفقاً|طبقاً|إعمالاً|تطبيقاً)\s+(?:ل|لنص\s+)?(?:ال)?مادة\s+(?:رقم\s*)?(\d+)',
    # "بمراعاة أحكام المادة 318"
    r'(?:بمراعاة|مع\s+مراعاة)\s+(?:أحكام\s+)?(?:ال)?مادة\s+(?:رقم\s*)?(\d+)',
    # "مع عدم الإخلال بنص المادة 40"
    r'(?:مع\s+)?عدم\s+(?:ال)?إخلال\s+(?:ب|بنص\s+)?(?:ال)?مادة\s+(?:رقم\s*)?(\d+)',
    # "المشار إليها في المادة 77"
    r'(?:المشار|المنصوص)\s+(?:إليها|عليها?)\s+(?:في|ب)\s*(?:ال)?مادة\s+(?:رقم\s*)?(\d+)',
    # "المادة 318 من قانون العقوبات"
    r'(?:ال)?مادة\s+(?:رقم\s*)?(\d+(?:\s*مكرر(?:اً)?)?)\s+من\s+قانون',
    # Simple "المادة 52" within referencing context
    r'(?:أحكام|نصوص|حكم)\s+(?:ال)?مادة?\s+(?:رقم\s*)?(\d+)',
]

_REFERENCE_TYPE_PATTERNS = {
    "modifies": [r'يعدل', r'يستبدل', r'تعديل', r'يلغى', r'ملغ[اةى]'],
    "references": [r'وفقاً', r'طبقاً', r'المشار', r'المنصوص', r'إعمالاً'],
    "supplements": [r'بمراعاة', r'مع\s+مراعاة', r'بالإضافة', r'فضلاً'],
    "restricts": [r'عدم\s+(?:ال)?إخلال', r'استثناء', r'مع\s+عدم'],
}


# ── Enhanced Keyword Maps ────────────────────────────────────────────

_ACTION_KEYWORDS = {
    "سرقة": "سرقة", "قتل": "قتل", "نصب": "نصب واحتيال",
    "احتيال": "نصب واحتيال", "طلاق": "طلاق", "زواج": "زواج",
    "نفقة": "نفقة", "حضانة": "حضانة", "ميراث": "ميراث",
    "إيجار": "إيجار", "عقد": "عقد", "تعويض": "تعويض",
    "فصل": "فصل تعسفي", "اعتداء": "اعتداء", "تزوير": "تزوير",
    "رشوة": "رشوة", "مخدرات": "مخدرات", "شيك": "شيك بدون رصيد",
    "إيصال": "إيصال أمانة", "ضرب": "ضرب", "سب": "سب وقذف",
    "قذف": "سب وقذف", "بلاغ كاذب": "بلاغ كاذب",
    "حبس": "حبس", "كفالة": "كفالة", "استئناف": "استئناف",
    "نقض": "نقض", "دعوى": "دعوى",
    # Extended legal actions
    "خيانة أمانة": "خيانة أمانة", "تبديد": "تبديد",
    "غصب": "غصب", "انتحال": "انتحال صفة", "هتك عرض": "هتك عرض",
    "تحرش": "تحرش", "ابتزاز": "ابتزاز", "تهديد": "تهديد",
    "شهادة زور": "شهادة زور", "إتلاف": "إتلاف",
    "امتناع": "امتناع عن تنفيذ حكم",
}

_PARTY_KEYWORDS = [
    "المتهم", "المجني عليه", "المدعي", "المدعى عليه",
    "الزوج", "الزوجة", "المالك", "المستأجر", "صاحب العمل",
    "العامل", "الموظف", "الشركة", "البائع", "المشتري",
    # Extended parties
    "الوارث", "الموصي", "الكفيل", "الضامن", "المؤجر",
    "الشريك", "المدير", "الوكيل", "الموكل", "القاصر",
    "الولي", "الوصي", "النيابة العامة", "المحكمة",
]

_TEMPORAL_KEYWORDS = [
    "قبل", "بعد", "أثناء", "خلال", "منذ", "حتى",
    "يوم", "شهر", "سنة", "مدة", "أجل", "ميعاد",
    "سقوط", "تقادم", "انقضاء",
]

_PENALTY_TERMS = [
    "يعاقب", "عقوبة", "حبس", "سجن", "غرامة",
    "يلتزم", "يجب", "يحظر", "لا يجوز", "ممنوع",
    "مسؤول", "تعويض", "جزاء", "إعدام", "سجن مؤبد",
    "الأشغال الشاقة", "المصادرة", "الحرمان",
]

_DEFENSE_TERMS = [
    "حق", "حقوق", "حرية", "استثناء", "إعفاء",
    "تخفيف", "ظروف مخففة", "حسن نية", "ضرورة",
    "دفاع شرعي", "رضا", "تنازل", "صلح", "عفو",
    "براءة", "عدم مسؤولية", "قوة قاهرة", "إكراه",
    "جنون", "عاهة عقلية", "سن", "قاصر",
]

_CATEGORY_RULES = {
    "criminal_law": [
        "سرقة", "قتل", "نصب", "احتيال", "اعتداء", "تزوير",
        "رشوة", "مخدرات", "ضرب", "سب", "قذف", "بلاغ كاذب",
        "جناية", "جنحة", "عقوبة", "حبس", "سجن", "غرامة",
        "المتهم", "المجني عليه", "النيابة", "تحرش", "هتك عرض",
        "خيانة أمانة", "تبديد", "ابتزاز", "تهديد", "شهادة زور",
    ],
    "civil_law": [
        "عقد", "تعويض", "إيجار", "بيع", "شراء", "ملكية",
        "ضرر", "مسؤولية", "التزام", "دين", "كفالة", "رهن",
        "فسخ", "بطلان", "إبطال", "تنفيذ عيني",
    ],
    "family_law": [
        "طلاق", "زواج", "نفقة", "حضانة", "ميراث", "مهر",
        "عدة", "خلع", "ولاية", "وصاية", "أحوال شخصية",
        "الزوج", "الزوجة", "قائمة منقولات",
    ],
    "commercial_law": [
        "شيك", "شركة", "تجاري", "إفلاس", "سجل تجاري",
        "توكيل", "وكالة تجارية", "علامة تجارية", "كمبيالة",
    ],
    "labor_law": [
        "فصل", "عمل", "أجر", "راتب", "إجازة", "تأمينات",
        "صاحب العمل", "العامل", "الموظف", "عقد عمل",
        "فصل تعسفي", "مكافأة نهاية الخدمة",
    ],
    "administrative_law": [
        "إداري", "حكومة", "موظف حكومي", "قرار إداري",
        "مجلس الدولة", "طعن",
    ],
    "constitutional_law": [
        "دستور", "حقوق", "حرية", "مساواة", "انتخاب",
        "محكمة دستورية",
    ],
    "procedural_law": [
        "دعوى", "استئناف", "نقض", "طعن", "إجراءات",
        "محكمة", "جلسة", "حكم", "تنفيذ", "اختصاص",
    ],
    "real_estate_law": [
        "عقار", "أرض", "بناء", "ترخيص", "شهر عقاري",
        "تسجيل", "حيازة", "صحة ونفاذ", "صحة توقيع",
    ],
    "tax_law": [
        "ضريبة", "ضرائب", "إعفاء ضريبي", "تهرب ضريبي",
    ],
}


# ── The Engine ───────────────────────────────────────────────────────

class DeepLegalReasoner:
    """
    8-step chain-of-thought legal reasoning engine.
    All steps are deterministic — no LLM calls.
    """

    def __init__(self):
        self._steps: list[ReasoningStep] = []

    def reset(self):
        self._steps = []

    # ── Step 1: Semantic Fact Extraction ──────────────────────────────

    def extract_facts(self, user_query: str, generate_fn=None) -> dict:
        """Enhanced fact extraction with patterns, not just keywords."""
        import json
        t0 = time.time()

        facts = {
            "parties": [],
            "actions": [],
            "subject": user_query[:300],
            "keywords": [],
            "temporal_refs": [],
            "monetary_refs": [],
            "article_refs": [],
        }

        # Actions
        for keyword, action in _ACTION_KEYWORDS.items():
            if keyword in user_query:
                if action not in facts["actions"]:
                    facts["actions"].append(action)
                if keyword not in facts["keywords"]:
                    facts["keywords"].append(keyword)

        # Parties
        for party in _PARTY_KEYWORDS:
            if party in user_query:
                facts["parties"].append(party)

        # Temporal references
        for term in _TEMPORAL_KEYWORDS:
            if term in user_query:
                facts["temporal_refs"].append(term)

        # Monetary amounts (e.g., "50000 جنيه", "مبلغ 100 ألف")
        money_pattern = r'(\d[\d,\.]*)\s*(?:جنيه|ج\.م|ألف|مليون|دولار)'
        for match in re.finditer(money_pattern, user_query):
            facts["monetary_refs"].append(match.group(0))

        # If generate_fn is provided, perform LLM-based NER extraction
        if generate_fn:
            try:
                system_prompt = (
                    "أنت خبير تحليل نصوص قانونية واستخراج كيانات قانونية. "
                    "مهمتك استخراج الكيانات المحددة من النص المرفق بصيغة JSON مطابقة تماماً للمثال التالي دون أي نصوص إضافية أو تعليقات خارج الجيسون:\n"
                    "{\n"
                    '  "people": ["اسم الشخص 1"],\n'
                    '  "organizations": ["اسم الجهة/الشركة 1"],\n'
                    '  "dates": ["تاريخ 1"],\n'
                    '  "money": ["مبلغ مالي 1"],\n'
                    '  "courts": ["اسم المحكمة 1"],\n'
                    '  "laws": ["اسم القانون/المادة 1"]\n'
                    "}"
                )
                prompt = f"اقرأ النص واستخرج الكيانات المحددة:\n\n{user_query}"
                ner_response = generate_fn(prompt, system_prompt).strip()
                ner_data = _safe_parse_json(ner_response, {})
                if ner_data:
                    facts["people"] = ner_data.get("people", [])
                    facts["organizations"] = ner_data.get("organizations", [])
                    facts["dates"] = ner_data.get("dates", [])
                    facts["money"] = ner_data.get("money", [])
                    facts["courts"] = ner_data.get("courts", [])
                    facts["laws"] = ner_data.get("laws", [])
            except Exception as e:
                print(f"[DeepReasoner] LLM NER failed: {e}")

        step = ReasoningStep(
            step_number=1,
            step_name="semantic_fact_extraction",
            inputs={"query": user_query[:200]},
            outputs=facts,
            confidence=min(1.0, 0.3 + 0.1 * len(facts["actions"]) + 0.1 * len(facts["parties"])),
            duration_ms=(time.time() - t0) * 1000,
        )
        self._steps.append(step)
        return facts

    # ── Step 2: Multi-Signal Classification ──────────────────────────

    def classify(self, user_query: str, facts: dict) -> dict:
        """Classification using keyword scoring + score distribution analysis."""
        t0 = time.time()

        scores: dict[str, float] = {cat: 0.0 for cat in LEGAL_CATEGORIES}
        all_text = user_query + " " + " ".join(facts.get("actions", [])) + " " + " ".join(facts.get("keywords", []))

        for category, terms in _CATEGORY_RULES.items():
            for term in terms:
                if term in all_text:
                    scores[category] += 1.0

        # Normalize scores
        total = sum(scores.values())
        if total > 0:
            normalized = {k: v / total for k, v in scores.items()}
        else:
            normalized = scores

        # Sort by score
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_cat = ranked[0][0] if ranked[0][1] > 0 else "other"
        secondary = [cat for cat, score in ranked[1:] if score > 0]

        # Classification confidence = gap between top-1 and top-2
        if len(ranked) >= 2 and total > 0:
            gap = (ranked[0][1] - ranked[1][1]) / total
            class_confidence = min(1.0, 0.5 + gap)
        else:
            class_confidence = 0.5 if best_cat != "other" else 0.2

        result = {
            "primary_category": best_cat,
            "secondary_categories": secondary[:3],
            "scores": {k: round(v, 3) for k, v in scores.items() if v > 0},
            "normalized_scores": {k: round(v, 3) for k, v in normalized.items() if v > 0},
            "classification_confidence": round(class_confidence, 3),
        }

        step = ReasoningStep(
            step_number=2,
            step_name="multi_signal_classification",
            inputs={"detected_actions": facts.get("actions", [])},
            outputs=result,
            confidence=class_confidence,
            duration_ms=(time.time() - t0) * 1000,
        )
        self._steps.append(step)
        return result

    # ── Step 3: Article Relevance Scoring ────────────────────────────

    def score_articles(self, facts: dict, articles: list[dict]) -> list[dict]:
        """Score each retrieved article's applicability to the case."""
        t0 = time.time()

        scored = []
        fact_keywords = set(facts.get("keywords", []) + facts.get("actions", []))

        for article in articles:
            text = article.get("text", "")
            law_name = article.get("law_name", "")

            # Keyword overlap score
            keyword_hits = sum(1 for kw in fact_keywords if kw in text)
            keyword_score = min(1.0, keyword_hits / max(len(fact_keywords), 1))

            # Penalty/defense relevance
            penalty_hits = sum(1 for term in _PENALTY_TERMS if term in text)
            defense_hits = sum(1 for term in _DEFENSE_TERMS if term in text)

            # Reranker score (if available, already normalized)
            reranker_score = article.get("reranker_score", 0.0)

            # Composite applicability score
            applicability = (
                0.35 * keyword_score +
                0.35 * min(1.0, reranker_score) +
                0.15 * min(1.0, penalty_hits / 3) +
                0.15 * min(1.0, defense_hits / 3)
            )

            scored_article = {
                **article,
                "applicability_score": round(applicability, 4),
                "keyword_overlap": keyword_hits,
                "penalty_relevance": penalty_hits,
                "defense_relevance": defense_hits,
                "evidence_quality": "strong" if applicability > 0.6 else "moderate" if applicability > 0.3 else "weak",
            }
            scored.append(scored_article)

        # Sort by applicability
        scored.sort(key=lambda x: x["applicability_score"], reverse=True)

        step = ReasoningStep(
            step_number=3,
            step_name="article_relevance_scoring",
            inputs={"article_count": len(articles), "fact_keywords": list(fact_keywords)[:10]},
            outputs={
                "scored_count": len(scored),
                "strong_evidence": sum(1 for a in scored if a["evidence_quality"] == "strong"),
                "moderate_evidence": sum(1 for a in scored if a["evidence_quality"] == "moderate"),
                "weak_evidence": sum(1 for a in scored if a["evidence_quality"] == "weak"),
            },
            confidence=min(1.0, 0.3 + 0.1 * sum(1 for a in scored if a["applicability_score"] > 0.3)),
            duration_ms=(time.time() - t0) * 1000,
        )
        self._steps.append(step)
        return scored

    # ── Step 4: Article-to-Article Link Graph ────────────────────────

    def build_article_links(self, articles: list[dict]) -> dict:
        """Extract cross-references between articles to build a link graph."""
        t0 = time.time()

        links = {}  # {article_index: [(referenced_article_num, ref_type, context)]}

        for i, article in enumerate(articles):
            text = article.get("text", "")
            article_refs = []

            for pattern in _CROSS_REF_PATTERNS:
                for match in re.finditer(pattern, text):
                    ref_num = match.group(1).strip()
                    context = text[max(0, match.start()-30):match.end()+30]

                    # Determine reference type
                    ref_type = "references"
                    for rtype, rpatterns in _REFERENCE_TYPE_PATTERNS.items():
                        if any(re.search(rp, context) for rp in rpatterns):
                            ref_type = rtype
                            break

                    article_refs.append({
                        "referenced_article": ref_num,
                        "reference_type": ref_type,
                        "context": context.strip()[:100],
                    })

            if article_refs:
                key = f"article_{i}_{article.get('law_name', 'unknown')[:30]}"
                links[key] = article_refs

        # Find inter-article connections (articles that reference each other)
        connections = []
        for i, art_a in enumerate(articles):
            text_a = art_a.get("text", "")
            # Extract article number from this text
            art_num_match = re.search(r'(?:ال)?مادة\s+(?:رقم\s*)?(\d+)', text_a[:100])
            if not art_num_match:
                continue
            art_a_num = art_num_match.group(1)

            for j, art_b in enumerate(articles):
                if i == j:
                    continue
                text_b = art_b.get("text", "")
                # Check if article B references article A's number
                if re.search(rf'(?:ال)?مادة\s+(?:رقم\s*)?{art_a_num}\b', text_b):
                    connections.append({
                        "from": i,
                        "to": j,
                        "from_law": art_a.get("law_name", ""),
                        "to_law": art_b.get("law_name", ""),
                        "article_num": art_a_num,
                    })

        step = ReasoningStep(
            step_number=4,
            step_name="article_link_graph",
            inputs={"article_count": len(articles)},
            outputs={
                "total_cross_references": sum(len(v) for v in links.values()),
                "articles_with_references": len(links),
                "inter_article_connections": len(connections),
            },
            confidence=1.0,
            duration_ms=(time.time() - t0) * 1000,
        )
        self._steps.append(step)

        return {
            "links": links,
            "connections": connections,
        }

    # ── Step 5: Contradiction Detection (delegated) ──────────────────

    def detect_contradictions(self, articles: list[dict], contradiction_detector) -> list[dict]:
        """Delegate to the ContradictionDetector module."""
        t0 = time.time()

        if contradiction_detector is None:
            step = ReasoningStep(
                step_number=5,
                step_name="contradiction_detection",
                inputs={"article_count": len(articles)},
                outputs={"contradictions_found": 0, "status": "detector_unavailable"},
                confidence=0.5,
                duration_ms=(time.time() - t0) * 1000,
            )
            self._steps.append(step)
            return []

        contradictions = contradiction_detector.detect(articles)

        step = ReasoningStep(
            step_number=5,
            step_name="contradiction_detection",
            inputs={"article_count": len(articles), "pairs_checked": len(articles) * (len(articles)-1) // 2},
            outputs={
                "contradictions_found": len(contradictions),
                "types": list(set(c.get("type", "") for c in contradictions)),
            },
            confidence=1.0 if not contradictions else max(0.3, 1.0 - 0.15 * len(contradictions)),
            duration_ms=(time.time() - t0) * 1000,
        )
        self._steps.append(step)
        return contradictions

    # ── Step 6: Dual-Perspective Analysis ────────────────────────────

    def analyze_perspectives(self, facts: dict, scored_articles: list[dict]) -> dict:
        """Prosecution + Defense analysis with evidence quality tagging."""
        t0 = time.time()

        prosecution_evidence = []
        defense_evidence = []

        for article in scored_articles:
            text = article.get("text", "")
            quality = article.get("evidence_quality", "weak")

            p_score = article.get("penalty_relevance", 0)
            d_score = article.get("defense_relevance", 0)

            if p_score > 0:
                prosecution_evidence.append({
                    "law_name": article.get("law_name", ""),
                    "text_preview": text[:200],
                    "relevance_score": p_score,
                    "evidence_quality": quality,
                    "applicability": article.get("applicability_score", 0),
                    "key_terms": [t for t in _PENALTY_TERMS if t in text][:5],
                })

            if d_score > 0:
                defense_evidence.append({
                    "law_name": article.get("law_name", ""),
                    "text_preview": text[:200],
                    "relevance_score": d_score,
                    "evidence_quality": quality,
                    "applicability": article.get("applicability_score", 0),
                    "key_terms": [t for t in _DEFENSE_TERMS if t in text][:5],
                })

        # Sort by relevance
        prosecution_evidence.sort(key=lambda x: x["relevance_score"], reverse=True)
        defense_evidence.sort(key=lambda x: x["relevance_score"], reverse=True)

        # Balance assessment
        p_strength = sum(e["relevance_score"] for e in prosecution_evidence)
        d_strength = sum(e["relevance_score"] for e in defense_evidence)
        total_strength = p_strength + d_strength

        if total_strength > 0:
            balance = {
                "prosecution_weight": round(p_strength / total_strength, 3),
                "defense_weight": round(d_strength / total_strength, 3),
                "stronger_side": "prosecution" if p_strength > d_strength else "defense" if d_strength > p_strength else "balanced",
            }
        else:
            balance = {"prosecution_weight": 0, "defense_weight": 0, "stronger_side": "unknown"}

        result = {
            "prosecution": {
                "evidence_count": len(prosecution_evidence),
                "total_strength": p_strength,
                "evidence": prosecution_evidence[:5],
            },
            "defense": {
                "evidence_count": len(defense_evidence),
                "total_strength": d_strength,
                "evidence": defense_evidence[:5],
            },
            "balance": balance,
        }

        step = ReasoningStep(
            step_number=6,
            step_name="dual_perspective_analysis",
            inputs={"scored_articles": len(scored_articles)},
            outputs={
                "prosecution_evidence": len(prosecution_evidence),
                "defense_evidence": len(defense_evidence),
                "balance": balance,
            },
            confidence=min(1.0, 0.3 + 0.05 * (len(prosecution_evidence) + len(defense_evidence))),
            duration_ms=(time.time() - t0) * 1000,
        )
        self._steps.append(step)
        return result

    # ── Step 7: Legal Risk Assessment ────────────────────────────────

    def assess_risk(self, facts: dict, scored_articles: list[dict], perspectives: dict) -> list[dict]:
        """Build legal risk matrix: severity × probability."""
        t0 = time.time()

        risks = []

        # Analyze prosecution evidence for risk items
        for evidence in perspectives.get("prosecution", {}).get("evidence", []):
            text = evidence.get("text_preview", "")
            key_terms = evidence.get("key_terms", [])
            applicability = evidence.get("applicability", 0)

            # Determine severity from penalty terms
            if any(t in text for t in ["إعدام", "سجن مؤبد", "الأشغال الشاقة"]):
                severity = "high"
            elif any(t in text for t in ["سجن", "حبس"]):
                severity = "medium"
            elif any(t in text for t in ["غرامة", "تعويض"]):
                severity = "low"
            else:
                severity = "low"

            # Determine probability from evidence quality
            quality = evidence.get("evidence_quality", "weak")
            if quality == "strong" and applicability > 0.6:
                probability = "high"
            elif quality == "moderate" or applicability > 0.3:
                probability = "medium"
            else:
                probability = "low"

            # Composite risk level
            risk_matrix = {
                ("high", "high"): "critical",
                ("high", "medium"): "high",
                ("high", "low"): "medium",
                ("medium", "high"): "high",
                ("medium", "medium"): "medium",
                ("medium", "low"): "low",
                ("low", "high"): "medium",
                ("low", "medium"): "low",
                ("low", "low"): "low",
            }
            risk_level = risk_matrix.get((severity, probability), "low")

            issue = f"خطر قانوني: {', '.join(key_terms[:3])}" if key_terms else "خطر قانوني محتمل"

            risks.append({
                "issue": issue,
                "severity": severity,
                "probability": probability,
                "risk_level": risk_level,
                "related_law": evidence.get("law_name", ""),
                "description": text[:150],
            })

        # Sort by risk level priority
        risk_priority = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        risks.sort(key=lambda x: risk_priority.get(x["risk_level"], 0), reverse=True)
        risks = risks[:REASONING_MAX_RISK_ITEMS]

        step = ReasoningStep(
            step_number=7,
            step_name="legal_risk_assessment",
            inputs={"prosecution_evidence": len(perspectives.get("prosecution", {}).get("evidence", []))},
            outputs={
                "total_risks": len(risks),
                "critical": sum(1 for r in risks if r["risk_level"] == "critical"),
                "high": sum(1 for r in risks if r["risk_level"] == "high"),
                "medium": sum(1 for r in risks if r["risk_level"] == "medium"),
                "low": sum(1 for r in risks if r["risk_level"] == "low"),
            },
            confidence=1.0,
            duration_ms=(time.time() - t0) * 1000,
        )
        self._steps.append(step)
        return risks

    # ── Step 8: Build Reasoning Context ──────────────────────────────

    def build_reasoning_context(
        self,
        facts: dict,
        classification: dict,
        scored_articles: list[dict],
        article_links: dict,
        contradictions: list[dict],
        perspectives: dict,
        risks: list[dict],
    ) -> str:
        """Build the complete structured reasoning context for the LLM."""
        t0 = time.time()

        sections = []

        # Section 1: Facts
        sections.append("## ١. الوقائع المستخلصة")
        sections.append(f"- الموضوع: {facts.get('subject', '')[:200]}")
        if facts.get("actions"):
            sections.append(f"- الأفعال: {', '.join(facts['actions'])}")
        if facts.get("parties"):
            sections.append(f"- الأطراف: {', '.join(facts['parties'])}")
        if facts.get("temporal_refs"):
            sections.append(f"- مرجعيات زمنية: {', '.join(facts['temporal_refs'])}")
        if facts.get("monetary_refs"):
            sections.append(f"- مبالغ مالية: {', '.join(facts['monetary_refs'])}")
        if facts.get("article_refs"):
            sections.append(f"- مواد مشار إليها: {', '.join(facts['article_refs'])}")
        sections.append("")

        # Section 2: Classification
        sections.append("## ٢. التصنيف القانوني")
        sections.append(f"- التصنيف الأساسي: {classification.get('primary_category', 'other')}")
        if classification.get("secondary_categories"):
            sections.append(f"- تصنيفات ثانوية: {', '.join(classification['secondary_categories'])}")
        sections.append(f"- ثقة التصنيف: {classification.get('classification_confidence', 0):.0%}")
        sections.append("")

        # Section 3: Evidence quality summary
        strong = sum(1 for a in scored_articles if a.get("evidence_quality") == "strong")
        moderate = sum(1 for a in scored_articles if a.get("evidence_quality") == "moderate")
        weak = sum(1 for a in scored_articles if a.get("evidence_quality") == "weak")
        sections.append("## ٣. تقييم جودة الأدلة")
        sections.append(f"- إجمالي النصوص المسترجعة: {len(scored_articles)}")
        sections.append(f"- أدلة قوية: {strong} | متوسطة: {moderate} | ضعيفة: {weak}")
        sections.append("")

        # Section 4: Cross-references
        links = article_links.get("links", {})
        connections = article_links.get("connections", [])
        if links or connections:
            sections.append("## ٤. الروابط بين النصوص القانونية")
            total_refs = sum(len(v) for v in links.values())
            sections.append(f"- إحالات مرجعية: {total_refs}")
            sections.append(f"- روابط متبادلة بين النصوص المسترجعة: {len(connections)}")
            for conn in connections[:3]:
                sections.append(f"  - المادة {conn['article_num']} مُشار إليها في نص آخر")
            sections.append("")

        # Section 5: Contradictions
        if contradictions:
            sections.append("## ٥. ⚠️ تعارضات مكتشفة")
            for c in contradictions:
                sections.append(
                    f"- تعارض ({c.get('type', 'عام')}): "
                    f"{c.get('article_a_name', '')} ↔ {c.get('article_b_name', '')} "
                    f"— {c.get('description', '')}"
                )
                if c.get("resolution_hint"):
                    sections.append(f"  → حل مقترح: {c['resolution_hint']}")
            sections.append("")

        # Section 6: Dual perspective
        balance = perspectives.get("balance", {})
        sections.append("## ٦. تحليل الموقف من المنظورين")
        p_count = perspectives.get("prosecution", {}).get("evidence_count", 0)
        d_count = perspectives.get("defense", {}).get("evidence_count", 0)
        sections.append(f"- أدلة الادعاء: {p_count} نصوص")
        sections.append(f"- أدلة الدفاع: {d_count} نصوص")
        sections.append(f"- الطرف الأقوى: {balance.get('stronger_side', 'unknown')}")

        for evidence in perspectives.get("prosecution", {}).get("evidence", [])[:2]:
            sections.append(f"  - ادعاء: {evidence.get('law_name', '')}: {', '.join(evidence.get('key_terms', []))}")
        for evidence in perspectives.get("defense", {}).get("evidence", [])[:2]:
            sections.append(f"  - دفاع: {evidence.get('law_name', '')}: {', '.join(evidence.get('key_terms', []))}")
        sections.append("")

        # Section 7: Risk assessment
        if risks:
            sections.append("## ٧. تقييم المخاطر القانونية")
            for risk in risks[:3]:
                level_ar = {"critical": "حرج", "high": "عالي", "medium": "متوسط", "low": "منخفض"}
                sections.append(
                    f"- [{level_ar.get(risk['risk_level'], 'غير محدد')}] "
                    f"{risk['issue']} — {risk.get('related_law', '')}"
                )
            sections.append("")

        context = "\n".join(sections)

        step = ReasoningStep(
            step_number=8,
            step_name="evidence_based_conclusion",
            inputs={"sections_built": 7},
            outputs={"context_length": len(context)},
            confidence=1.0,
            duration_ms=(time.time() - t0) * 1000,
        )
        self._steps.append(step)

        return context

    # ── Main Entry Point ─────────────────────────────────────────────

    def run(
        self,
        user_query: str,
        retrieved_articles: list[dict],
        contradiction_detector=None,
        generate_fn=None,
    ) -> ReasoningTrace:
        """Execute the full 8-step reasoning chain."""
        self.reset()

        # Step 1: Facts
        facts = self.extract_facts(user_query, generate_fn)

        # Step 2: Classification
        classification = self.classify(user_query, facts)

        # Step 3: Score articles
        scored_articles = self.score_articles(facts, retrieved_articles)

        # Step 4: Cross-reference links
        article_links = self.build_article_links(scored_articles)

        # Step 5: Contradiction detection
        contradictions = self.detect_contradictions(scored_articles, contradiction_detector)

        # Step 6: Dual perspective
        perspectives = self.analyze_perspectives(facts, scored_articles)

        # Step 7: Risk assessment
        risks = self.assess_risk(facts, scored_articles, perspectives)

        # Multi-Agent Execution if generate_fn is provided
        prosecution_data = {}
        defense_data = {}
        judge_data = {}

        # Heuristic check for legal signals in the user query to bypass LLM reasoning for simple greetings
        has_legal_signals = (
            len(facts.get("parties", [])) > 0 or
            len(facts.get("actions", [])) > 0 or
            len(facts.get("keywords", [])) > 0 or
            len(facts.get("monetary_refs", [])) > 0 or
            len(facts.get("article_refs", [])) > 0 or
            classification.get("primary_category", "other") != "other"
        )

        if generate_fn:
            if not has_legal_signals:
                print("[DeepReasoner] Non-legal or general query detected. Bypassing multi-agent analysis.")
                prosecution_data = {
                    "argument": "لا توجد أبعاد قانونية واضحة في الاستفسار المعروض تقتضي مرافعة الادعاء.",
                    "supporting_articles": [],
                    "confidence": 0.0,
                    "risk_view": "low",
                    "reasoning": "الاستفسار ذو طبيعة عامة أو غير قانونية."
                }
                defense_data = {
                    "argument": "لا يوجد اتهام أو مسؤولية قانونية تقتضي تقديم دفوع.",
                    "supporting_articles": [],
                    "confidence": 0.0,
                    "risk_view": "low",
                    "reasoning": "الاستفسار ذو طبيعة عامة أو غير قانونية."
                }
                judge_data = {
                    "verdict": "uncertain",
                    "verdict_ar": "غير حاسم",
                    "confidence": 0.0,
                    "reasoning": "الاستفسار المقدم عام ولا يطرح نزاعاً قانونياً أو وقائع للفصل فيها.",
                    "key_factors": ["طبيعة استرشادية عامة"]
                }
            else:
                try:
                    from core.agents.prosecutor_agent import ProsecutorAgent
                    from core.agents.defense_agent import DefenseAgent
                    from core.agents.judge_agent import JudgeAgent
                    from concurrent.futures import ThreadPoolExecutor

                    print("[DeepReasoner] Initiating multi-agent analysis...")
                    prosecutor = ProsecutorAgent(generate_fn)
                    defense = DefenseAgent(generate_fn)
                    judge = JudgeAgent(generate_fn)

                    # Execute prosecution and defense agents concurrently in parallel threads
                    with ThreadPoolExecutor(max_workers=2) as executor:
                        future_pros = executor.submit(prosecutor.analyze, facts, scored_articles, contradictions)
                        future_def = executor.submit(defense.analyze, facts, scored_articles, contradictions)
                        prosecution_data = future_pros.result()
                        defense_data = future_def.result()

                    # Judge decision must run after both prosecution and defense arguments are ready
                    judge_data = judge.decide(facts, prosecution_data, defense_data, scored_articles, contradictions)
                except Exception as e:
                    print(f"[DeepReasoner] Error in multi-agent execution: {e}")
        else:
            # Fallback mock data when running offline/tests without generation
            prosecution_data = {
                "argument": "الادعاء يرى ثبوت التهمة بناءً على المواد المسترجعة والوقائع المعروضة.",
                "supporting_articles": [],
                "confidence": 0.7,
                "risk_view": "medium",
                "reasoning": "انطباق شروط تجريم الفعل."
            }
            defense_data = {
                "argument": "الدفاع يتمسك ببراءة المتهم لوجود شك في الدليل وحسن النية.",
                "supporting_articles": [],
                "confidence": 0.6,
                "risk_view": "low",
                "reasoning": "أهمية تفسير الشك لمصلحة المتهم."
            }
            judge_data = {
                "verdict": "uncertain",
                "verdict_ar": "غير حاسم",
                "confidence": 0.5,
                "reasoning": "تعادل الحجج بين النيابة والدفاع يقتضي التوقف وطلب مزيد من الإيضاح.",
                "key_factors": ["تعارض الدفوع والأدلة"]
            }

        # Success probability and timeline extraction
        success_prob_data = {
            "probability": 50,
            "pros": ["توفر نصوص قانونية مطابقة"],
            "cons": ["تحتاج القضية لمزيد من التدقيق الواقعي للأدلة"],
            "precedents": []
        }
        timeline_data = {"events": [], "warnings": []}

        if generate_fn and has_legal_signals:
            try:
                system_prompt_prob = (
                    "أنت مستشار قانوني خبير في تقييم القضايا والنزاعات أمام القضاء المصري. "
                    "مهمتك تقييم احتمالية كسب القضية بناءً على الوقائع المرفقة وأدلة الادعاء والدفاع المتاحة. "
                    "يجب أن تقدم تحليلاً دقيقاً وحساب نسبة مئوية واقعية للاحتمالية (من 0 إلى 100) وتوفر نقاط القوة والضعف والأدلة الداعمة. "
                    "الرد يجب أن يكون بصيغة JSON مطابقة تماماً للهيكل التالي دون أي نصوص إضافية أو تعليقات خارج الجيسون:\n"
                    "{\n"
                    '  "probability": 75,\n'
                    '  "pros": ["نقطة قوة 1"],\n'
                    '  "cons": ["نقطة ضعف 1"],\n'
                    '  "precedents": ["سوابق قضائية أو أحكام نقض تدعم الموقف 1"]\n'
                    "}"
                )
                prompt_prob = (
                    f"الوقائع المرفقة بالنزاع:\n{user_query}\n\n"
                    f"حجة الادعاء:\n{json.dumps(prosecution_data, ensure_ascii=False)}\n\n"
                    f"دفوع الدفاع:\n{json.dumps(defense_data, ensure_ascii=False)}\n"
                )
                prob_response = generate_fn(prompt_prob, system_prompt_prob).strip()
                success_prob_data = _safe_parse_json(prob_response, success_prob_data)
            except Exception as e:
                print(f"[DeepReasoner] LLM success probability calculation failed: {e}")

            try:
                system_prompt_time = (
                    "أنت خبير قانوني مصري متخصص في استخراج التواريخ وحساب مواعيد مدد السقوط والتقادم القانوني طبقاً لأحكام القانون المصري. "
                    "مهمتك هي استخراج الوقائع المرتبطة بتواريخ محددة، وتقييم وجود أي مدد تقادم مسقط للحقوق أو مدد سقوط أو مواعيد إجرائية هامة. "
                    "يجب أن يكون ردك بصيغة JSON صالحة ومطابقة للهيكل التالي تماماً ودون أي نصوص إضافية أو تعليقات خارج الجيسون:\n"
                    "{\n"
                    '  "events": [\n'
                    '    { "date": "التاريخ (YYYY/MM/DD)", "event": "وصف الواقعة", "implication": "الأثر القانوني" }\n'
                    '  ],\n'
                    '  "warnings": [\n'
                    '    { "type": "نوع التنبيه", "days_remaining_or_passed": "المدة المتبقية أو المنقضية", "description": "شرح السند القانوني" }\n'
                    '  ]\n'
                    "}"
                )
                prompt_time = f"اقرأ النص واستخرج التواريخ والتقادم القانوني المرتبط به بدقة:\n\n{user_query}"
                timeline_response = generate_fn(prompt_time, system_prompt_time).strip()
                parsed_timeline = _safe_parse_json(timeline_response, None)
                if parsed_timeline:
                    timeline_data = parsed_timeline
            except Exception as e:
                print(f"[DeepReasoner] LLM timeline extraction failed: {e}")

        # Step 8: Build reasoning context
        reasoning_context = self.build_reasoning_context(
            facts, classification, scored_articles,
            article_links, contradictions, perspectives, risks,
        )

        # Append Multi-Agent arguments and decision to context
        agent_sections = []
        agent_sections.append("\n## ٨. مرافعة النيابة العامة (Prosecution)")
        agent_sections.append(f"- الحجة الأساسية: {prosecution_data.get('argument', '')}")
        agent_sections.append(f"- منطق الادعاء: {prosecution_data.get('reasoning', '')}")
        agent_sections.append(f"- المواد المؤيدة: {', '.join(prosecution_data.get('supporting_articles', []))}")
        
        agent_sections.append("\n## ٩. دفوع محامي الدفاع (Defense)")
        agent_sections.append(f"- الحجة الأساسية: {defense_data.get('argument', '')}")
        agent_sections.append(f"- منطق الدفاع: {defense_data.get('reasoning', '')}")
        agent_sections.append(f"- المواد المؤيدة: {', '.join(defense_data.get('supporting_articles', []))}")

        agent_sections.append("\n## ١٠. حكم منصة القضاء (Judge Verdict)")
        agent_sections.append(f"- منطوق الحكم: {judge_data.get('verdict_ar', '')} ({judge_data.get('verdict', '')})")
        agent_sections.append(f"- نسبة ثقة المحكمة: {judge_data.get('confidence', 0.0):.0%}")
        agent_sections.append(f"- أسباب وحيثيات الحكم: {judge_data.get('reasoning', '')}")
        if judge_data.get('key_factors'):
            agent_sections.append(f"- العوامل الحاسمة: {', '.join(judge_data.get('key_factors', []))}")

        reasoning_context += "\n" + "\n".join(agent_sections)

        # Assemble trace
        trace = ReasoningTrace(
            steps=list(self._steps),
            primary_category=classification.get("primary_category", "other"),
            secondary_categories=classification.get("secondary_categories", []),
            facts=facts,
            article_links=article_links,
            contradictions=contradictions,
            risk_assessment=risks,
            overall_confidence=judge_data.get("confidence", classification.get("classification_confidence", 0.5)),
            reasoning_context=reasoning_context,
            prosecution=prosecution_data,
            defense=defense_data,
            judge_decision=judge_data,
            entities={
                "people": facts.get("people", []),
                "organizations": facts.get("organizations", []),
                "dates": facts.get("dates", []),
                "money": facts.get("money", []),
                "courts": facts.get("courts", []),
                "laws": facts.get("laws", [])
            },
            timeline=timeline_data,
            success_probability_data=success_prob_data
        )

        total_ms = sum(s.duration_ms for s in self._steps)
        print(f"[DeepReasoner] Multi-agent reasoning trace completed.")

        return trace
