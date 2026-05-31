"""
EgyLegalAi — Core Multi-Step Legal Reasoning Chain
================================================
Implements a structured 6-step legal analysis pipeline that produces
comprehensive, well-reasoned legal opinions.

Steps:
    1. identify_facts()         — Extract key facts from user query
    2. classify_case()          — Determine legal domain
    3. retrieve_laws()          — Targeted law retrieval
    4. prosecution_analysis()   — Build prosecution perspective
    5. defense_analysis()       — Build defense perspective
    6. final_conclusion()       — Synthesize final legal opinion

Each step produces structured output that feeds into the next step,
building a comprehensive context block for the LLM.
"""

from typing import Optional
from config import LEGAL_CATEGORIES


class LegalReasoningChain:
    """
    Multi-step legal reasoning chain for comprehensive case analysis.

    The chain processes a user query through 6 structured steps,
    building a rich context that the LLM uses to produce a
    well-reasoned legal opinion.
    """

    def __init__(self):
        self._steps_completed: list[dict] = []

    def reset(self):
        """Clear all completed steps for a new analysis."""
        self._steps_completed = []

    def identify_facts(self, user_query: str) -> dict:
        """
        Step 1: Extract key facts from the user's query.

        Parses the user input to identify:
        - Subject matter / topic
        - Parties involved
        - Actions described
        - Key terms and legal concepts
        - Questions being asked

        Returns a structured fact summary.
        """
        # Keyword-based fact extraction for Arabic legal text
        fact_indicators = {
            "parties":  [],
            "actions":  [],
            "subject":  user_query[:200],
            "keywords": [],
        }

        # Detect legal action keywords (Arabic)
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
                if action not in fact_indicators["actions"]:
                    fact_indicators["actions"].append(action)
                if keyword not in fact_indicators["keywords"]:
                    fact_indicators["keywords"].append(keyword)

        # Detect party references
        party_keywords = [
            "المتهم", "المجني عليه", "المدعي", "المدعى عليه",
            "الزوج", "الزوجة", "المالك", "المستأجر", "صاحب العمل",
            "العامل", "الموظف", "الشركة", "البائع", "المشتري",
        ]
        for party in party_keywords:
            if party in user_query:
                fact_indicators["parties"].append(party)

        step_result = {
            "step": "identify_facts",
            "step_number": 1,
            "facts": fact_indicators,
            "raw_query": user_query,
        }
        self._steps_completed.append(step_result)
        return step_result

    def classify_case(self, facts: dict) -> dict:
        """
        Step 2: Classify the case into a legal domain.

        Uses the extracted facts and keywords to determine which
        area of Egyptian law applies.
        """
        raw_query = facts.get("raw_query", "")
        actions = facts.get("facts", {}).get("actions", [])
        keywords = facts.get("facts", {}).get("keywords", [])

        # Classification rules based on keywords
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

        # Score each category
        scores: dict[str, int] = {cat: 0 for cat in LEGAL_CATEGORIES}
        all_text = raw_query + " " + " ".join(actions) + " " + " ".join(keywords)

        for category, terms in category_rules.items():
            for term in terms:
                if term in all_text:
                    scores[category] += 1

        # Pick the highest-scoring category
        best_category = max(scores, key=scores.get)
        if scores[best_category] == 0:
            best_category = "other"

        # Get secondary categories (score > 0)
        secondary = [
            cat for cat, score in scores.items()
            if score > 0 and cat != best_category
        ]

        step_result = {
            "step": "classify_case",
            "step_number": 2,
            "primary_category": best_category,
            "secondary_categories": secondary,
            "confidence_scores": {k: v for k, v in scores.items() if v > 0},
        }
        self._steps_completed.append(step_result)
        return step_result

    def build_retrieval_context(
        self,
        classification: dict,
        retrieved_results: list[dict],
    ) -> dict:
        """
        Step 3: Organize retrieved laws by relevance to the classification.

        Groups laws by source and relevance, preparing them for analysis.
        """
        primary_cat = classification.get("primary_category", "other")

        # Group results by source
        by_source: dict[str, list] = {}
        for result in retrieved_results:
            source = result.get("source", "unknown")
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(result)

        step_result = {
            "step": "retrieve_laws",
            "step_number": 3,
            "primary_category": primary_cat,
            "total_laws_found": len(retrieved_results),
            "laws_by_source": {
                source: len(laws) for source, laws in by_source.items()
            },
            "results": retrieved_results,
        }
        self._steps_completed.append(step_result)
        return step_result

    def prosecution_analysis(
        self,
        facts: dict,
        laws: list[dict],
    ) -> dict:
        """
        Step 4: Build prosecution/claimant perspective.

        Identifies which laws support the prosecution/claimant position
        and what penalties or remedies apply.
        """
        prosecution_laws = []
        for law in laws:
            text = law.get("text", "")
            # Laws mentioning penalties, obligations, or prohibitions
            penalty_terms = [
                "يعاقب", "عقوبة", "حبس", "سجن", "غرامة",
                "يلتزم", "يجب", "يحظر", "لا يجوز", "ممنوع",
                "مسؤول", "تعويض", "جزاء",
            ]
            relevance = sum(1 for term in penalty_terms if term in text)
            if relevance > 0:
                prosecution_laws.append({
                    **law,
                    "prosecution_relevance": relevance,
                })

        step_result = {
            "step": "prosecution_analysis",
            "step_number": 4,
            "supporting_laws_count": len(prosecution_laws),
            "supporting_laws": prosecution_laws,
            "facts_summary": facts.get("facts", {}).get("subject", ""),
        }
        self._steps_completed.append(step_result)
        return step_result

    def defense_analysis(
        self,
        facts: dict,
        laws: list[dict],
    ) -> dict:
        """
        Step 5: Build defense perspective.

        Identifies which laws support the defense position,
        mitigating factors, and exceptions.
        """
        defense_laws = []
        for law in laws:
            text = law.get("text", "")
            # Laws mentioning rights, exceptions, mitigations
            defense_terms = [
                "حق", "حقوق", "حرية", "استثناء", "إعفاء",
                "تخفيف", "ظروف مخففة", "حسن نية", "ضرورة",
                "دفاع شرعي", "رضا", "تنازل", "صلح", "عفو",
                "براءة", "عدم مسؤولية",
            ]
            relevance = sum(1 for term in defense_terms if term in text)
            if relevance > 0:
                defense_laws.append({
                    **law,
                    "defense_relevance": relevance,
                })

        step_result = {
            "step": "defense_analysis",
            "step_number": 5,
            "supporting_laws_count": len(defense_laws),
            "supporting_laws": defense_laws,
        }
        self._steps_completed.append(step_result)
        return step_result

    def build_reasoning_context(self) -> str:
        """
        Step 6: Build the complete reasoning context for the LLM.

        Combines all step outputs into a structured context block
        that the LLM uses to produce the final legal opinion.
        """
        sections = []

        for step in self._steps_completed:
            step_name = step.get("step", "unknown")
            step_num = step.get("step_number", 0)

            if step_name == "identify_facts":
                facts = step.get("facts", {})
                section = f"### الخطوة {step_num}: تحديد الوقائع\n"
                section += f"- الموضوع: {facts.get('subject', '')}\n"
                if facts.get("actions"):
                    section += f"- الأفعال المحددة: {', '.join(facts['actions'])}\n"
                if facts.get("parties"):
                    section += f"- الأطراف: {', '.join(facts['parties'])}\n"
                if facts.get("keywords"):
                    section += f"- كلمات مفتاحية: {', '.join(facts['keywords'])}\n"
                sections.append(section)

            elif step_name == "classify_case":
                section = f"### الخطوة {step_num}: تصنيف القضية\n"
                section += f"- التصنيف الأساسي: {step.get('primary_category', 'other')}\n"
                if step.get("secondary_categories"):
                    section += f"- تصنيفات ثانوية: {', '.join(step['secondary_categories'])}\n"
                sections.append(section)

            elif step_name == "retrieve_laws":
                section = f"### الخطوة {step_num}: استرجاع النصوص القانونية\n"
                section += f"- إجمالي النصوص: {step.get('total_laws_found', 0)}\n"
                by_source = step.get("laws_by_source", {})
                for src, count in by_source.items():
                    section += f"  - {src}: {count} نصوص\n"
                sections.append(section)

            elif step_name == "prosecution_analysis":
                section = f"### الخطوة {step_num}: تحليل موقف الادعاء/المدعي\n"
                section += f"- نصوص داعمة: {step.get('supporting_laws_count', 0)}\n"
                for law in step.get("supporting_laws", [])[:3]:
                    section += f"  - {law.get('law_name', 'غير محدد')}: {law.get('text', '')[:200]}\n"
                sections.append(section)

            elif step_name == "defense_analysis":
                section = f"### الخطوة {step_num}: تحليل موقف الدفاع\n"
                section += f"- نصوص داعمة: {step.get('supporting_laws_count', 0)}\n"
                for law in step.get("supporting_laws", [])[:3]:
                    section += f"  - {law.get('law_name', 'غير محدد')}: {law.get('text', '')[:200]}\n"
                sections.append(section)

        return "\n".join(sections)

    def run_full_chain(
        self,
        user_query: str,
        retrieved_results: list[dict],
    ) -> dict:
        """
        Execute the complete 6-step reasoning chain.

        Args:
            user_query:        The user's legal question.
            retrieved_results: Documents from hybrid retrieval + reranking.

        Returns:
            Dict with all step results and the final reasoning context.
        """
        self.reset()

        # Step 1: Identify facts
        facts = self.identify_facts(user_query)

        # Step 2: Classify case
        classification = self.classify_case(facts)

        # Step 3: Organize retrieved laws
        retrieval_ctx = self.build_retrieval_context(
            classification, retrieved_results
        )

        # Step 4: Prosecution analysis
        prosecution = self.prosecution_analysis(
            facts, retrieved_results
        )

        # Step 5: Defense analysis
        defense = self.defense_analysis(
            facts, retrieved_results
        )

        # Step 6: Build final context
        reasoning_context = self.build_reasoning_context()

        return {
            "facts": facts,
            "classification": classification,
            "retrieval": retrieval_ctx,
            "prosecution": prosecution,
            "defense": defense,
            "reasoning_context": reasoning_context,
            "primary_category": classification.get("primary_category", "other"),
        }
