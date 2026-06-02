"""
Qanuni.AI — Legal Reasoning Chain
6-step analysis pipeline for legal case analysis.
"""

from typing import Optional
from config import LEGAL_CATEGORIES


class LegalReasoningChain:
    """Multi-step legal reasoning: facts → classify → retrieve → analyze → conclude."""

    def __init__(self):
        self._steps: list[dict] = []

    def reset(self):
        """Clear all steps for a new analysis."""
        self._steps = []

    def identify_facts(self, user_query: str) -> dict:
        """Step 1: Extract key facts from the user's query."""
        facts = {
            "parties":  [],
            "actions":  [],
            "subject":  user_query[:200],
            "keywords": [],
        }

        # Arabic legal action keywords
        action_keywords = {
            "سرقة": "سرقة",
            "قتل": "قتل",
            "نصب": "نصب واحتيال",
            "احتيال": "نصب واحتيال",
            "طلاق": "طلاق",
            "زواج": "زواج",
            "نفقة": "نفقة",
            "حضانة": "حضانة",
            "ميراث": "ميراث",
            "إيجار": "إيجار",
            "عقد": "عقد",
            "تعويض": "تعويض",
            "فصل": "فصل تعسفي",
            "اعتداء": "اعتداء",
            "تزوير": "تزوير",
            "رشوة": "رشوة",
            "مخدرات": "مخدرات",
            "شيك": "شيك بدون رصيد",
            "إيصال": "إيصال أمانة",
            "ضرب": "ضرب",
            "سب": "سب وقذف",
            "قذف": "سب وقذف",
            "بلاغ كاذب": "بلاغ كاذب",
            "حبس": "حبس",
            "كفالة": "كفالة",
            "استئناف": "استئناف",
            "نقض": "نقض",
            "دعوى": "دعوى",
        }

        for keyword, action in action_keywords.items():
            if keyword in user_query:
                if action not in facts["actions"]:
                    facts["actions"].append(action)
                if keyword not in facts["keywords"]:
                    facts["keywords"].append(keyword)

        # Detect party references
        party_keywords = [
            "المتهم", "المجني عليه", "المدعي", "المدعى عليه",
            "الزوج", "الزوجة", "المالك", "المستأجر", "صاحب العمل",
            "العامل", "الموظف", "الشركة", "البائع", "المشتري",
        ]
        for party in party_keywords:
            if party in user_query:
                facts["parties"].append(party)

        result = {
            "step": "identify_facts",
            "step_number": 1,
            "facts": facts,
            "raw_query": user_query,
        }
        self._steps.append(result)
        return result

    def classify_case(self, facts: dict) -> dict:
        """Step 2: Classify the case into a legal domain."""
        raw_query = facts.get("raw_query", "")
        actions = facts.get("facts", {}).get("actions", [])
        keywords = facts.get("facts", {}).get("keywords", [])

        category_rules = {
            "criminal_law": [
                "سرقة", "قتل", "نصب", "احتيال", "اعتداء", "تزوير",
                "رشوة", "مخدرات", "ضرب", "سب", "قذف", "بلاغ كاذب",
                "جناية", "جنحة", "عقوبة", "حبس", "سجن", "غرامة",
                "المتهم", "المجني عليه", "النيابة",
            ],
            "civil_law": [
                "عقد", "تعويض", "إيجار", "بيع", "شراء", "ملكية",
                "ضرر", "مسؤولية", "التزام", "دين", "كفالة", "رهن",
            ],
            "family_law": [
                "طلاق", "زواج", "نفقة", "حضانة", "ميراث", "مهر",
                "عدة", "خلع", "ولاية", "وصاية", "أحوال شخصية",
                "الزوج", "الزوجة",
            ],
            "commercial_law": [
                "شيك", "شركة", "تجاري", "إفلاس", "سجل تجاري",
                "توكيل", "وكالة تجارية", "علامة تجارية",
            ],
            "labor_law": [
                "فصل", "عمل", "أجر", "راتب", "إجازة", "تأمينات",
                "صاحب العمل", "العامل", "الموظف", "عقد عمل",
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
                "محكمة", "جلسة", "حكم", "تنفيذ",
            ],
            "real_estate_law": [
                "عقار", "أرض", "بناء", "ترخيص", "شهر عقاري",
                "تسجيل", "حيازة",
            ],
            "tax_law": [
                "ضريبة", "ضرائب", "إعفاء ضريبي", "تهرب ضريبي",
            ],
        }

        scores: dict[str, int] = {cat: 0 for cat in LEGAL_CATEGORIES}
        all_text = raw_query + " " + " ".join(actions) + " " + " ".join(keywords)

        for category, terms in category_rules.items():
            for term in terms:
                if term in all_text:
                    scores[category] += 1

        best = max(scores, key=scores.get)
        if scores[best] == 0:
            best = "other"

        secondary = [cat for cat, score in scores.items() if score > 0 and cat != best]

        result = {
            "step": "classify_case",
            "step_number": 2,
            "primary_category": best,
            "secondary_categories": secondary,
            "confidence_scores": {k: v for k, v in scores.items() if v > 0},
        }
        self._steps.append(result)
        return result

    def build_retrieval_context(self, classification: dict, retrieved_results: list[dict]) -> dict:
        """Step 3: Organize retrieved laws by source."""
        primary_cat = classification.get("primary_category", "other")

        by_source: dict[str, list] = {}
        for result in retrieved_results:
            source = result.get("source", "unknown")
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(result)

        result = {
            "step": "retrieve_laws",
            "step_number": 3,
            "primary_category": primary_cat,
            "total_laws_found": len(retrieved_results),
            "laws_by_source": {src: len(laws) for src, laws in by_source.items()},
            "results": retrieved_results,
        }
        self._steps.append(result)
        return result

    def prosecution_analysis(self, facts: dict, laws: list[dict]) -> dict:
        """Step 4: Find laws supporting the prosecution/claimant."""
        prosecution_laws = []
        penalty_terms = [
            "يعاقب", "عقوبة", "حبس", "سجن", "غرامة",
            "يلتزم", "يجب", "يحظر", "لا يجوز", "ممنوع",
            "مسؤول", "تعويض", "جزاء",
        ]

        for law in laws:
            text = law.get("text", "")
            relevance = sum(1 for term in penalty_terms if term in text)
            if relevance > 0:
                prosecution_laws.append({**law, "prosecution_relevance": relevance})

        result = {
            "step": "prosecution_analysis",
            "step_number": 4,
            "supporting_laws_count": len(prosecution_laws),
            "supporting_laws": prosecution_laws,
            "facts_summary": facts.get("facts", {}).get("subject", ""),
        }
        self._steps.append(result)
        return result

    def defense_analysis(self, facts: dict, laws: list[dict]) -> dict:
        """Step 5: Find laws supporting the defense."""
        defense_laws = []
        defense_terms = [
            "حق", "حقوق", "حرية", "استثناء", "إعفاء",
            "تخفيف", "ظروف مخففة", "حسن نية", "ضرورة",
            "دفاع شرعي", "رضا", "تنازل", "صلح", "عفو",
            "براءة", "عدم مسؤولية",
        ]

        for law in laws:
            text = law.get("text", "")
            relevance = sum(1 for term in defense_terms if term in text)
            if relevance > 0:
                defense_laws.append({**law, "defense_relevance": relevance})

        result = {
            "step": "defense_analysis",
            "step_number": 5,
            "supporting_laws_count": len(defense_laws),
            "supporting_laws": defense_laws,
        }
        self._steps.append(result)
        return result

    def build_reasoning_context(self) -> str:
        """Step 6: Build the complete reasoning context string for the LLM."""
        sections = []

        for step in self._steps:
            name = step.get("step", "unknown")
            num = step.get("step_number", 0)

            if name == "identify_facts":
                facts = step.get("facts", {})
                section = f"### الخطوة {num}: تحديد الوقائع\n"
                section += f"- الموضوع: {facts.get('subject', '')}\n"
                if facts.get("actions"):
                    section += f"- الأفعال المحددة: {', '.join(facts['actions'])}\n"
                if facts.get("parties"):
                    section += f"- الأطراف: {', '.join(facts['parties'])}\n"
                if facts.get("keywords"):
                    section += f"- كلمات مفتاحية: {', '.join(facts['keywords'])}\n"
                sections.append(section)

            elif name == "classify_case":
                section = f"### الخطوة {num}: تصنيف القضية\n"
                section += f"- التصنيف الأساسي: {step.get('primary_category', 'other')}\n"
                if step.get("secondary_categories"):
                    section += f"- تصنيفات ثانوية: {', '.join(step['secondary_categories'])}\n"
                sections.append(section)

            elif name == "retrieve_laws":
                section = f"### الخطوة {num}: استرجاع النصوص القانونية\n"
                section += f"- إجمالي النصوص: {step.get('total_laws_found', 0)}\n"
                for src, count in step.get("laws_by_source", {}).items():
                    section += f"  - {src}: {count} نصوص\n"
                sections.append(section)

            elif name == "prosecution_analysis":
                section = f"### الخطوة {num}: تحليل موقف الادعاء/المدعي\n"
                section += f"- نصوص داعمة: {step.get('supporting_laws_count', 0)}\n"
                for law in step.get("supporting_laws", [])[:3]:
                    section += f"  - {law.get('law_name', 'غير محدد')}: {law.get('text', '')[:200]}\n"
                sections.append(section)

            elif name == "defense_analysis":
                section = f"### الخطوة {num}: تحليل موقف الدفاع\n"
                section += f"- نصوص داعمة: {step.get('supporting_laws_count', 0)}\n"
                for law in step.get("supporting_laws", [])[:3]:
                    section += f"  - {law.get('law_name', 'غير محدد')}: {law.get('text', '')[:200]}\n"
                sections.append(section)

        return "\n".join(sections)

    def run_full_chain(self, user_query: str, retrieved_results: list[dict]) -> dict:
        """Run the complete 6-step reasoning chain."""
        self.reset()

        facts = self.identify_facts(user_query)
        classification = self.classify_case(facts)
        retrieval = self.build_retrieval_context(classification, retrieved_results)
        prosecution = self.prosecution_analysis(facts, retrieved_results)
        defense = self.defense_analysis(facts, retrieved_results)
        reasoning_context = self.build_reasoning_context()

        return {
            "facts": facts,
            "classification": classification,
            "retrieval": retrieval,
            "prosecution": prosecution,
            "defense": defense,
            "reasoning_context": reasoning_context,
            "primary_category": classification.get("primary_category", "other"),
        }
