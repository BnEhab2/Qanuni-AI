"""
Qanuni.AI — Prosecutor Agent
=============================
Persona: Egyptian Prosecution (النيابة العامة المصرية).
Task: Build the strongest possible case for conviction / liability, interpreting laws strictly.
"""

from core.agents.agent_base import LegalAgentBase

PROSECUTOR_INSTRUCTION = """أنت ممثل النيابة العامة المصرية / سلطة الادعاء.
مهمتك الأساسية هي بناء أقوى حجة قانونية لإثبات الجرم أو المسؤولية ضد المتهم/المسؤول بناءً على الوقائع والمواد المسترجعة.
يجب أن تركز تماماً على:
1. إدانة المتهم وتأكيد وقوع الفعل المخالف للقانون.
2. تفسير نصوص المواد القانونية بأقصى درجات الشدة والصرامة لصالح المجتمع وحماية القانون.
3. تضخيم وتسليط الضوء على الأدلة والوقائع التي تدين المتهم.
4. إهمال أو إضعاف أي دفوع أو تبريرات يقدمها المتهم (مثل حسن النية أو الظروف المخففة) إلا إذا كانت قاطعة قانونياً ولا يمكن تجاوزها.

يجب أن يكون أسلوبك حازماً وقوياً ومنهجياً بأسلوب وكلاء النيابة العامة.

يجب أن تقوم بالرد بصيغة JSON حصرياً بالشكل التالي:
{
  "argument": "الملخص القانوني للمرافعة والاتهام باللغة العربية بأسلوب النيابة العامة البليغ والقوي",
  "supporting_articles": ["المادة [الرقم] من قانون [الاسم]", ...],
  "confidence": 0.95, // نسبة تقديرك لقوة قضية الادعاء بين 0.0 و 1.0
  "risk_view": "high", // رؤيتك لخطورة الجرم للمجتمع: "high" أو "medium" أو "low"
  "reasoning": "التفنيد التفصيلي خطوة بخطوة للوقائع وكيف تنطبق عليها نصوص التجريم المشددة"
}
"""

PROSECUTOR_PROMPT_TEMPLATE = """الوقائع المستخلصة:
{facts_summary}

المواد القانونية المتاحة:
{articles_text}

التعارضات القانونية المرصودة:
{contradictions_text}

قم بتحليل القضية كمدعي عام وصيغ الحجة بالكامل باللغة العربية داخل قالب الـ JSON المطلوب. تذكر عدم كتابة أي كلام خارج قالب الـ JSON."""

class ProsecutorAgent(LegalAgentBase):
    """Prosecution Agent that analyzes the case to build an indictment."""

    def analyze(self, facts: dict, articles: list[dict], contradictions: list[dict] = None) -> dict:
        """Analyze case from a prosecution perspective."""
        if not self.generate_fn:
            # Fallback mock if LLM generation not injected
            return {
                "argument": "المتهم اقترف الفعل المجرم والمستند إلى الأدلة والوقائع الواضحة.",
                "supporting_articles": [a.get("citation", "") for a in articles[:2]],
                "confidence": 0.8,
                "risk_view": "high",
                "reasoning": "تطابق الوقائع مع مواد التجريم والعقوبة."
            }

        facts_summary = f"الموضوع: {facts.get('subject', '')}\nالأفعال: {', '.join(facts.get('actions', []))}\nالأطراف: {', '.join(facts.get('parties', []))}"
        articles_text = self._format_articles(articles)
        contradictions_text = self._format_contradictions(contradictions)

        prompt = PROSECUTOR_PROMPT_TEMPLATE.format(
            facts_summary=facts_summary,
            articles_text=articles_text,
            contradictions_text=contradictions_text
        )

        response_text = self.generate_fn(prompt, system_instruction=PROSECUTOR_INSTRUCTION)
        return self.clean_json(response_text)
