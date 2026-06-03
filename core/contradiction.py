"""
Qanuni.AI — Contradiction Detection Module
==========================================
Identifies temporal, scope, penalty, procedural, and right/obligation conflicts
between retrieved legal articles.
"""

import re

# Temporal patterns: e.g. "لسنة 1937", "لسنة ٢٠٠٣"
ARABIC_DIGITS = {
    '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
    '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
}

def translate_arabic_digits(text: str) -> str:
    for ar, en in ARABIC_DIGITS.items():
        text = text.replace(ar, en)
    return text

def extract_year_from_text(text: str) -> int:
    """Attempts to extract a 4-digit Gregorian year from text (title or content)."""
    if not text:
        return 0
    text_en = translate_arabic_digits(text)
    # Search for "لسنة YYYY" or "عام YYYY" or just a 4-digit year near legal contexts
    matches = re.findall(r'(?:لسنة|عام|سنة|سنة\s+المالية)\s*(\d{4})', text_en)
    if matches:
        return int(matches[0])
    
    # Generic 4-digit years between 1800 and 2100
    all_years = re.findall(r'\b(18\d{2}|19\d{2}|20\d{2})\b', text_en)
    if all_years:
        return int(all_years[0])
    return 0

class ContradictionDetector:
    """Detects various types of contradictions in retrieved articles."""

    def __init__(self):
        pass

    def detect(self, articles: list[dict]) -> list[dict]:
        """
        Compare all pairs of articles to identify potential contradictions.
        Max articles is usually <= 10, so O(N^2) comparison is cheap (<= 45 pairs).
        """
        contradictions = []
        if len(articles) < 2:
            return contradictions

        # Normalize and prepare data
        prep_articles = []
        for a in articles:
            text = a.get("text", "")
            title = a.get("law_name", "") or a.get("title", "")
            
            # Extract article number if possible
            art_num_match = re.search(r'(?:ال)?مادة\s+(?:رقم\s*)?(\d+)', text[:150])
            art_num = art_num_match.group(1) if art_num_match else "غير محدد"
            
            # Extract year from title or text
            year = extract_year_from_text(title)
            if not year:
                year = extract_year_from_text(text[:500])
                
            prep_articles.append({
                "original": a,
                "text": text,
                "title": title,
                "art_num": art_num,
                "year": year,
                "category": a.get("category", "").strip().lower(),
                "keywords": set(a.get("keywords", "").split(",") if isinstance(a.get("keywords"), str) else a.get("keywords", []))
            })

        # Pairwise comparison
        for i in range(len(prep_articles)):
            for j in range(i + 1, len(prep_articles)):
                art_a = prep_articles[i]
                art_b = prep_articles[j]

                # 1. Check topic overlap. We only compare if they share category or keywords, or have high term overlap.
                shared_keywords = art_a["keywords"].intersection(art_b["keywords"])
                has_category_match = art_a["category"] == art_b["category"] and art_a["category"] != ""
                
                # Check keyword overlap in texts
                common_terms = ["عقوبة", "حبس", "سجن", "غرامة", "تعويض", "بطلان", "فسخ", "حق", "التزام", "منع", "حظر"]
                shared_terms = [t for t in common_terms if t in art_a["text"] and t in art_b["text"]]

                if not (has_category_match or len(shared_keywords) >= 2 or len(shared_terms) >= 2):
                    continue

                # 2. Check for Temporal Contradiction (Newer law overrides older on same topic)
                if art_a["year"] > 0 and art_b["year"] > 0 and art_a["year"] != art_b["year"]:
                    newer = art_a if art_a["year"] > art_b["year"] else art_b
                    older = art_b if art_a["year"] > art_b["year"] else art_a
                    
                    # If they reference the exact same concepts or numbers
                    if art_a["art_num"] == art_b["art_num"] and art_a["art_num"] != "غير محدد":
                        contradictions.append({
                            "type": "temporal",
                            "article_a_name": f"{art_a['title']} - مادة {art_a['art_num']}",
                            "article_b_name": f"{art_b['title']} - مادة {art_b['art_num']}",
                            "severity": "high",
                            "description": f"تعارض زمني: المادة الصادرة عام {newer['year']} تعدل أو تحل محل المادة الصادرة عام {older['year']}.",
                            "resolution_hint": f"المادة الصادرة في عام {newer['year']} هي الواجبة التطبيق عملاً بقاعدة اللاحق يلغي السابق."
                        })
                        continue

                # 3. Check for Penalty Contradictions (Different penalties for same actions/keywords)
                # Let's extract numbers associated with "حبس" or "غرامة" to see if they differ
                # e.g., "حبس مدة لا تقل عن سنة" vs "حبس مدة لا تقل عن سنتين"
                penalty_a = self._extract_penalty_duration(art_a["text"])
                penalty_b = self._extract_penalty_duration(art_b["text"])
                if penalty_a and penalty_b and penalty_a != penalty_b:
                    contradictions.append({
                        "type": "penalty",
                        "article_a_name": f"{art_a['title']} - مادة {art_a['art_num']}",
                        "article_b_name": f"{art_b['title']} - مادة {art_b['art_num']}",
                        "severity": "medium",
                        "description": "اختلاف العقوبات: تنص المادتان على مدد حبس أو غرامات متفاوتة لنفس الفعل أو الجرم.",
                        "resolution_hint": "تُطبق العقوبة الأشد إذا كانت الجريمة تتطلب ظرفاً مشدداً، أو العقوبة الأخف لصالح المتهم حسب ظروف الدعوى."
                    })
                    continue

                # 4. Right vs Obligation Conflict (Allows/Grants vs Prohibits/Restricts)
                allows_a = any(w in art_a["text"] for w in ["يجوز", "يمنح", "حق", "يسمح"])
                prohibits_b = any(w in art_b["text"] for w in ["يحظر", "لا يجوز", "يمنع", "يعاقب"])
                
                allows_b = any(w in art_b["text"] for w in ["يجوز", "يمنح", "حق", "يسمح"])
                prohibits_a = any(w in art_a["text"] for w in ["يحظر", "لا يجوز", "يمنع", "يعاقب"])

                if (allows_a and prohibits_b) or (allows_b and prohibits_a):
                    contradictions.append({
                        "type": "conflict",
                        "article_a_name": f"{art_a['title']} - مادة {art_a['art_num']}",
                        "article_b_name": f"{art_b['title']} - مادة {art_b['art_num']}",
                        "severity": "high",
                        "description": "تعارض الحقوق والواجبات: إحدى المواد ترخص فعلاً بينما تحظره أو تقيده المادة الأخرى.",
                        "resolution_hint": "يُعتبر النص الخاص استثناءً على النص العام (العام يخصص والخاص يقيد العام) أو يُلغى التراخي بالمنع."
                    })
                    continue

                # 5. Scope Contradiction (General vs Specific)
                is_general_a = any(w in art_a["text"] for w in ["كل من", "عامة", "جميع"])
                is_specific_b = any(w in art_b["text"] for w in ["استثناء", "ما لم", "إلا إذا", "بشرط"])
                
                is_general_b = any(w in art_b["text"] for w in ["كل من", "عامة", "جميع"])
                is_specific_a = any(w in art_a["text"] for w in ["استثناء", "ما لم", "إلا إذا", "بشرط"])

                if (is_general_a and is_specific_b) or (is_general_b and is_specific_a):
                    spec = art_b if is_specific_b else art_a
                    gen = art_a if is_specific_b else art_b
                    contradictions.append({
                        "type": "scope",
                        "article_a_name": f"{art_a['title']} - مادة {art_a['art_num']}",
                        "article_b_name": f"{art_b['title']} - مادة {art_b['art_num']}",
                        "severity": "medium",
                        "description": "تعارض نطاق التطبيق (عام مقابل خاص): نص عام يقابله استثناء أو شرط محدد في نص آخر.",
                        "resolution_hint": f"المادة الاستثنائية ({spec['title']} مادة {spec['art_num']}) تقيد النص العام عملاً بقاعدة 'الخاص يقيد العام'."
                    })

        return contradictions

    def _extract_penalty_duration(self, text: str) -> str:
        """Helper to find penalty duration keywords in text to flag conflicts."""
        match = re.search(r'(?:حبس|سجن)\s+(?:مدة\s+)?(?:لا\s+تقل\s+عن|لا\s+تزيد\s+على)?\s*(\d+|سنة|سنتين|أشهر|سنوات)', text)
        if match:
            return match.group(0)
        return ""
