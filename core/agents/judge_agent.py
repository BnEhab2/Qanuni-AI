"""
Qanuni.AI — Judge Agent
=======================
Persona: Neutral Egyptian Judge (القاضي المصري المحايد).
Task: Weight arguments, resolve contradictions, and output a final logical verdict.
"""

from core.agents.agent_base import LegalAgentBase

JUDGE_INSTRUCTION = """أنت قاضٍ مصري محايد، تمثل منصة القضاء والعدالة الهادئة والموضوعية.
مهمتك الأساسية هي تقييم ووزن الحجج والأدلة المقدمة من كل من:
- النيابة العامة (الادعاء)
- محامي المتهم (الدفاع)

يجب أن تقوم بـ:
1. تقييم مدى صحة وقوة انطباق المواد القانونية المستشهد بها من الطرفين على الوقائع.
2. فحص التناقضات بين المواد، وتطبيق القواعد الأصولية لحلها (مثل: اللاحق يلغي السابق، والخاص يقيد العام).
3. تحديد ما إذا كانت الأدلة كافية للإدانة بما لا يدع مجالاً للشك، أم أن هناك شكاً يفسر لمصلحة المتهم.
4. إصدار الحكم النهائي بكل حياد وموضوعية ودقة.

يجب أن يكون أسلوبك رصيناً، متوازناً، يتبع تسلسلاً منطقياً قانونياً كالأحكام الصادرة عن محكمة النقض المصرية ومحاكم الاستئناف.

يجب أن تقوم بالرد بصيغة JSON حصرياً بالشكل التالي:
{
  "verdict": "guilty", // خيار من ثلاثة فقط: "guilty" (إدانة/ثبوت المسؤولية)، "not_guilty" (براءة/عدم ثبوت)، "uncertain" (غير حاسم/حاجة لمزيد من الأدلة)
  "verdict_ar": "إدانة / براءة / غير حاسم", // الحكم باللغة العربية
  "confidence": 0.85, // مدى ثقة المحكمة بالحكم النهائي بناءً على تكامل الأدلة والردود بين 0.0 و 1.0
  "reasoning": "أسباب الحكم والحيثيات التفصيلية باللغة العربية بأسلوب قضائي رصين ومفصل يوازن بين الادعاء والدفاع",
  "key_factors": ["العامل الحاسم الأول الذي بني عليه الحكم", "العامل الثاني", ...] // قائمة العوامل الحاسمة في القضية
}
"""

JUDGE_PROMPT_TEMPLATE = """الوقائع المستخلصة:
{facts_summary}

حجة وأدلة النيابة العامة (الادعاء):
- الخلاصة: {prosecution_arg}
- المواد المستند إليها: {prosecution_articles}
- رأي الادعاء في الخطورة: {prosecution_risk}
- المنطق: {prosecution_reasoning}

حجة وأدلة الدفاع:
- الخلاصة: {defense_arg}
- المواد المستند إليها: {defense_articles}
- رأي الدفاع في الخطورة: {defense_risk}
- المنطق: {defense_reasoning}

المواد القانونية المتاحة كمرجع عام:
{articles_text}

التعارضات القانونية المرصودة:
{contradictions_text}

قم بدراسة الموقف بالكامل وموازنة الحجج، ثم اكتب أسباب الحكم وحيثياته منطقياً وأصدر الحكم في قالب الـ JSON المطلوب. لا تكتب أي نصوص خارج القالب."""

class JudgeAgent(LegalAgentBase):
    """Judge Agent that reviews arguments from both sides and issues a final verdict."""

    def decide(self, facts: dict, prosecution: dict, defense: dict, articles: list[dict], contradictions: list[dict] = None) -> dict:
        """Issue a verdict based on prosecution and defense arguments."""
        if not self.generate_fn:
            # Fallback mock if LLM generation not injected
            return {
                "verdict": "uncertain",
                "verdict_ar": "غير حاسم",
                "confidence": 0.6,
                "reasoning": "نظراً لتعادل حجج الطرفين وعدم كفاية الأدلة القاطعة للفصل بالإدانة الكاملة.",
                "key_factors": ["تعادل الأدلة", "صعوبة الجزم بالبراءة أو الإدانة"]
            }

        facts_summary = f"الموضوع: {facts.get('subject', '')}\nالأفعال: {', '.join(facts.get('actions', []))}\nالأطراف: {', '.join(facts.get('parties', []))}"
        
        prosecution_articles = ", ".join(prosecution.get("supporting_articles", []))
        defense_articles = ", ".join(defense.get("supporting_articles", []))
        
        articles_text = self._format_articles(articles)
        contradictions_text = self._format_contradictions(contradictions)

        prompt = JUDGE_PROMPT_TEMPLATE.format(
            facts_summary=facts_summary,
            prosecution_arg=prosecution.get("argument", ""),
            prosecution_articles=prosecution_articles,
            prosecution_risk=prosecution.get("risk_view", ""),
            prosecution_reasoning=prosecution.get("reasoning", ""),
            defense_arg=defense.get("argument", ""),
            defense_articles=defense_articles,
            defense_risk=defense.get("risk_view", ""),
            defense_reasoning=defense.get("reasoning", ""),
            articles_text=articles_text,
            contradictions_text=contradictions_text
        )

        response_text = self.generate_fn(prompt, system_instruction=JUDGE_INSTRUCTION)
        return self.clean_json(response_text)
