"""
Qanuni.AI — Base Agent Class
============================
Defines the base class for specialized legal agents.
"""

import json
import re

class LegalAgentBase:
    """Base class for all legal reasoning agents."""

    def __init__(self, generate_fn=None):
        self.generate_fn = generate_fn

    def clean_json(self, text: str) -> dict:
        """Utility to extract and parse JSON from LLM response."""
        if not text:
            return {}
        
        # Strip markdown code blocks
        clean_text = text.strip()
        if clean_text.startswith("```json"):
            clean_text = clean_text[7:]
        elif clean_text.startswith("```"):
            clean_text = clean_text[3:]
        if clean_text.endswith("```"):
            clean_text = clean_text[:-3]
        clean_text = clean_text.strip()
        
        # Regex search for JSON block
        try:
            return json.loads(clean_text)
        except json.JSONDecodeError:
            # Try to find a JSON pattern { ... }
            match = re.search(r'\{.*\}', clean_text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        
        # Fallback empty dict
        print(f"[AgentBase] Warning: Failed to parse JSON from text: {text[:200]}")
        return {}

    def _format_articles(self, articles: list[dict]) -> str:
        """Helper to format list of articles for prompt inclusion."""
        parts = []
        for i, a in enumerate(articles, 1):
            parts.append(
                f"[{i}] {a.get('law_name', 'قانون غير محدد')} - مادة {a.get('article_number', 'غير محدد')}\n"
                f"نص المادة: {a.get('text', '')[:1000]}"
            )
        return "\n\n".join(parts)

    def _format_contradictions(self, contradictions: list[dict]) -> str:
        """Helper to format list of contradictions for prompt inclusion."""
        if not contradictions:
            return "لا توجد تعارضات مرصودة."
        parts = []
        for i, c in enumerate(contradictions, 1):
            parts.append(
                f"- تعارض #{i} ({c.get('type')}): {c.get('article_a_name')} ↔ {c.get('article_b_name')}\n"
                f"  الوصف: {c.get('description')}\n"
                f"  الحل المقترح: {c.get('resolution_hint')}"
            )
        return "\n".join(parts)
