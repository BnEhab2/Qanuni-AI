"""
Qanuni.AI — Confidence Scoring Pipeline
=======================================
Aggregates multiple signals to compute an overall confidence score and flag hallucination risks.
"""

from config import (
    CONFIDENCE_WEIGHT_RETRIEVAL,
    CONFIDENCE_WEIGHT_COVERAGE,
    CONFIDENCE_WEIGHT_CLASSIFICATION,
    CONFIDENCE_WEIGHT_CONTRADICTION,
    CONFIDENCE_WEIGHT_PRECEDENT,
    CONFIDENCE_WEIGHT_EVIDENCE,
    HALLUCINATION_LOW_CONFIDENCE_THRESHOLD,
    HALLUCINATION_MEDIUM_CONFIDENCE_THRESHOLD,
)

class ConfidenceScorer:
    """Computes legal confidence score and flags hallucination risk."""

    def __init__(self, memory_store=None):
        self.memory_store = memory_store

    def score(
        self,
        query: str,
        facts: dict,
        classification: dict,
        retrieved_articles: list[dict],
        contradictions: list[dict],
    ) -> dict:
        """
        Calculate composite confidence score based on various weighted signals.
        """
        # 1. Retrieval Quality Signal (0.25)
        # Average reranker score of top results. Reranker scores are typically between -10 and 10 or 0 and 1.
        # If we have a 'reranker_score', let's use it. If not, fallback to 0.7.
        scores = [a.get("reranker_score", a.get("applicability_score", 0.7)) for a in retrieved_articles]
        if scores:
            # Normalize to 0-1 if they are not already
            norm_scores = []
            for s in scores:
                if s > 1.0:
                    # Reranker scores can be logits, standard sigmoid mapping:
                    import math
                    val = 1 / (1 + math.exp(-s))
                elif s < 0.0:
                    val = 0.0
                else:
                    val = s
                norm_scores.append(val)
            retrieval_signal = sum(norm_scores) / len(norm_scores)
        else:
            retrieval_signal = 0.0

        # 2. Fact Coverage Signal (0.20)
        # What % of detected fact actions/keywords are covered in retrieved articles
        fact_keywords = set(facts.get("keywords", []) + facts.get("actions", []))
        if fact_keywords:
            covered_count = 0
            for kw in fact_keywords:
                for a in retrieved_articles:
                    if kw in a.get("text", ""):
                        covered_count += 1
                        break
            coverage_signal = covered_count / len(fact_keywords)
        else:
            # If no keywords were even extracted, default to 1.0 (no mismatch) or 0.8
            coverage_signal = 0.8

        # 3. Classification Certainty Signal (0.15)
        classification_signal = classification.get("classification_confidence", 0.5)

        # 4. Contradiction Level Signal (0.15)
        # 1.0 minus penalty for each contradiction found
        if contradictions:
            # Subtract based on contradiction severity
            penalty = 0.0
            for c in contradictions:
                sev = c.get("severity", "medium")
                if sev == "high":
                    penalty += 0.3
                elif sev == "medium":
                    penalty += 0.2
                else:
                    penalty += 0.1
            contradiction_signal = max(0.0, 1.0 - penalty)
        else:
            contradiction_signal = 1.0

        # 5. Precedent Consistency Signal (0.15)
        # Query case memory for similar past queries. If highly similar, score is high.
        precedent_signal = 0.7  # Default fallback
        if self.memory_store is not None:
            try:
                similar = self.memory_store.retrieve_similar_cases(query, top_k=1)
                if similar:
                    # Use similarity score from retrieval
                    precedent_signal = similar[0].get("similarity", 0.7)
            except Exception:
                pass

        # 6. Evidence Completeness Signal (0.10)
        # We need at least 3 articles for solid legal grounding
        evidence_signal = min(1.0, len(retrieved_articles) / 3.0)

        # Aggregate weighted score
        overall_confidence = (
            CONFIDENCE_WEIGHT_RETRIEVAL * retrieval_signal +
            CONFIDENCE_WEIGHT_COVERAGE * coverage_signal +
            CONFIDENCE_WEIGHT_CLASSIFICATION * classification_signal +
            CONFIDENCE_WEIGHT_CONTRADICTION * contradiction_signal +
            CONFIDENCE_WEIGHT_PRECEDENT * precedent_signal +
            CONFIDENCE_WEIGHT_EVIDENCE * evidence_signal
        )

        overall_confidence = round(min(1.0, max(0.0, overall_confidence)), 3)

        # Determine label
        if overall_confidence < HALLUCINATION_LOW_CONFIDENCE_THRESHOLD:
            label = "low"
            ar_label = "ثقة منخفضة"
            hallucination_risk = "high"
        elif overall_confidence < HALLUCINATION_MEDIUM_CONFIDENCE_THRESHOLD:
            label = "medium"
            ar_label = "ثقة متوسطة"
            hallucination_risk = "medium"
        else:
            label = "high"
            ar_label = "ثقة عالية"
            hallucination_risk = "low"

        # Generate warnings
        warnings = []
        if len(retrieved_articles) == 0:
            warnings.append("⚠️ لم يتم العثور على أي نصوص قانونية وثيقة الصلة بالموضوع.")
        elif retrieval_signal < 0.4:
            warnings.append("⚠️ النصوص القانونية المسترجعة ضعيفة الصلة بالوقائع.")
        
        if coverage_signal < 0.5:
            warnings.append("⚠️ تغطية الحقائق المستخلصة في نصوص القوانين المتاحة ناقصة.")
            
        if contradictions:
            warnings.append(f"⚠️ تم رصد {len(contradictions)} تعارض قانوني محتمل بين المواد المسترجعة.")

        return {
            "overall_confidence": overall_confidence,
            "confidence_label": label,
            "confidence_label_ar": ar_label,
            "hallucination_risk": hallucination_risk,
            "warnings": warnings,
            "signals": {
                "retrieval_quality": round(retrieval_signal, 3),
                "fact_coverage": round(coverage_signal, 3),
                "classification_certainty": round(classification_signal, 3),
                "contradiction_level": round(contradiction_signal, 3),
                "precedent_consistency": round(precedent_signal, 3),
                "evidence_completeness": round(evidence_signal, 3),
            }
        }
