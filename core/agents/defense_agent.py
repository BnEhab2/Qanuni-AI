"""
Qanuni.AI — Defense Agent
=========================
Persona: Egyptian Defense Counsel (محامي الدفاع المصري).
Task: Find loopholes, mitigating factors, and reasonable doubt to support the accused.
"""

from core.agents.agent_base import LegalAgentBase

DEFENSE_INSTRUCTION = """أنت محامي الدفاع عن المتهم / المسؤول عن القضية.
مهمتك الأساسية هي إيجاد ثغرات، أعذار قانونية، ظروف مخففة، أو أدلة تثبت براءة موكلك أو على الأقل تخفف العقوبة عنه لأقصى حد ممكن.
يجب أن تركز تماماً على:
1. إظهار الشك والضعف في ادعاءات النيابة العامة أو الطرف الآخر.
2. التركيز على الدفوع القانونية والمواد التي تتحدث عن الإعفاء، الترخيص، حسن النية، القوة القاهرة، الصلح، أو الدفاع الشرعي.
3. التمسك بالقواعد القانونية الراسخة مثل: "الشك يُفسر لصالح المتهم"، و "الأصل في الإنسان البراءة".
4. استغلال التعارضات بين المواد القانونية لصالح المتهم وإبراز الاستثناءات على القواعد العامة (الخاص يقيد العام).

يجب أن يكون أسلوبك بليغاً ومقنعاً ومنهجياً كما هو معروف عن كبار المحامين في المحاكم المصرية.

يجب أن تقوم بالرد بصيغة JSON حصرياً بالشكل التالي:
{
  "argument": "الملخص القانوني لدفوع ومرافعة الدفاع باللغة العربية بأسلوب المحاماة البليغ",
  "supporting_articles": ["المادة [الرقم] من قانون [الاسم]", ...],
  "confidence": 0.85, // تقديرك لقوة موقف الدفاع واحتمالية تبرئة المتهم بين 0.0 و 1.0
  "risk_view": "low", // خطورة الموقف على المتهم حسب دفوعك: "high" أو "medium" أو "low"
  "reasoning": "تفنيد تفصيلي للأدلة والوقائع وتوضيح الثغرات والظروف المخففة التي تصب في مصلحة موكلك"
}
"""

DEFENSE_PROMPT_TEMPLATE = """الوقائع المستخلصة:
{facts_summary}

المواد القانونية المتاحة:
{articles_text}

التعارضات القانونية المرصودة:
{contradictions_text}

قم بتحليل القضية كمحامي دفاع وصيغ الحجة بالكامل باللغة العربية داخل قالب الـ JSON المطلوب. تذكر عدم كتابة أي كلام خارج قالب الـ JSON."""

class DefenseAgent(LegalAgentBase):
    """Defense Agent that analyzes the case to find defense arguments and mitigating factors."""

    def analyze(self, facts: dict, articles: list[dict], contradictions: list[dict] = None) -> dict:
        """Analyze case from a defense perspective."""
        if not self.generate_fn:
            # Fallback mock if LLM generation not injected
            return {
                "argument": "يوجد شك كبير يكتنف أدلة الثبوت، والأصل البراءة.",
                "supporting_articles": [a.get("citation", "") for a in articles[-2:]] if articles else [],
                "confidence": 0.7,
                "risk_view": "low",
                "reasoning": "تمسك المتهم بالدفوع الاستثنائية وحسن النية."
            }

        facts_summary = f"الموضوع: {facts.get('subject', '')}\nالأفعال: {', '.join(facts.get('actions', []))}\nالأطراف: {', '.join(facts.get('parties', []))}"
        articles_text = self._format_articles(articles)
        contradictions_text = self._format_contradictions(contradictions)

        prompt = DEFENSE_PROMPT_TEMPLATE.format(
            facts_summary=facts_summary,
            articles_text=articles_text,
            contradictions_text=contradictions_text
        )

        response_text = self.generate_fn(prompt, system_instruction=DEFENSE_INSTRUCTION)
        return self.clean_json(response_text)
