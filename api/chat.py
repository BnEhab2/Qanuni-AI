"""
Qanuni.AI — Chat Endpoints
Main chat, SSE streaming, and debate mode.
"""

import json
import uuid
import inspect
import asyncio
import threading
import queue
import re

from flask import Blueprint, request, jsonify, g, Response, stream_with_context

from api.middleware import (
    require_auth_or_guest,
    enforce_guest_limit,
    increment_guest_count,
    rate_limit,
    sanitize_input,
)
from api.models import (
    create_conversation,
    add_message,
    get_conversation,
    update_conversation_title,
    touch_conversation,
    get_conversation_message_count,
    get_messages,
)
from config import GUEST_MESSAGE_LIMIT
from core.caching import cache

chat_bp = Blueprint("chat", __name__)

_CATEGORY_FOLLOW_UPS = {
    "civil_law": ["هل يتوفر عقد كتابي موثق بين الطرفين؟", "هل تم توجيه أي إنذار رسمي على يد محضر؟"],
    "real_estate_law": ["ما تاريخ إبرام عقد الإيجار بالتحديد؟", "هل العقار مخصص لغرض سكني أم تجاري؟"],
    "family_law": ["هل صدرت أي أحكام قضائية سابقة بالنفقات؟", "هل توجد قائمة منقولات زوجية موقعة؟"],
    "labor_law": ["هل عقد العمل محدد المدة أم غير محدد؟", "هل تم تقديم شكوى رسمية إلى مكتب العمل المختص؟"],
    "criminal_law": ["هل تم تحرير محضر رسمي في قسم الشرطة؟", "هل تتوفر تقارير طبية أو شهود للواقعة؟"],
    "commercial_law": ["هل تم تقديم الشيك للبنك في موعده القانوني؟", "هل تم استلام إفادة رفض بنكية بالرجوع؟"],
}

def _get_fast_follow_ups(category: str = "other", user_message: str = "") -> list[str]:
    """Instant heuristic follow-up questions (0ms latency, no extra LLM call)."""
    return _CATEGORY_FOLLOW_UPS.get(category, [
        "هل يتوفر أي مستند كتابي أو عقد رسمي يخص الواقعة؟",
        "هل اتخذت أي إجراء قانوني أو محضر رسمي حتى الآن؟"
    ])

# Injected by app.py after model initialization
_services = {}


def init_chat_services(
    search_laws_fn,
    ask_llm_fn,
    conversation_memory,
    case_memory,
    reasoning_chain_class,
    format_citation_fn,
    build_citation_block_fn,
    run_async_fn,
    session_service,
    runner,
    debate_runner_prosecution,
    debate_runner_defense,
    types_module,
    deep_reasoner,
    contradiction_detector,
    confidence_scorer,
    generate_text_fn=None,
    event_loop=None,
):
    """Inject initialized services from app.py."""
    _services["search_laws"] = search_laws_fn
    _services["ask_llm"] = ask_llm_fn
    _services["conversation_memory"] = conversation_memory
    _services["case_memory"] = case_memory
    _services["LegalReasoningChain"] = reasoning_chain_class
    _services["format_citation"] = format_citation_fn
    _services["build_citation_block"] = build_citation_block_fn
    _services["run_async"] = run_async_fn
    _services["session_service"] = session_service
    _services["runner"] = runner
    _services["debate_runner_prosecution"] = debate_runner_prosecution
    _services["debate_runner_defense"] = debate_runner_defense
    _services["types"] = types_module
    _services["deep_reasoner"] = deep_reasoner
    _services["contradiction_detector"] = contradiction_detector
    _services["confidence_scorer"] = confidence_scorer
    _services["generate_text"] = generate_text_fn
    _services["event_loop"] = event_loop


def _auto_title(text: str) -> str:
    """Short title from first message."""
    clean = text.strip()
    return clean[:40] + "..." if len(clean) > 40 else clean


@chat_bp.route("/api/chat", methods=["POST"])
@rate_limit(max_per_minute=15)
@require_auth_or_guest
def chat():
    """Main chat endpoint."""
    allowed, remaining, error = enforce_guest_limit()
    if not allowed:
        return error

    data = request.get_json(force=True)
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "الرسالة فارغة."}), 400

    user_message, has_injection = sanitize_input(user_message)
    if has_injection:
        print(f"[Security] Injection attempt from {request.remote_addr}")

    source_filter = data.get("source_filter")
    type_filter = data.get("type_filter")
    category_filter = data.get("category_filter")
    web_search_enabled = data.get("web_search", False)
    conversation_id = data.get("conversation_id")
    session_id = data.get("session_id", "default_session")

    # Create or validate conversation
    if not conversation_id:
        conversation_id = create_conversation(
            user_id=g.user_id,
            guest_id=g.guest_id,
            title=_auto_title(user_message),
        )
    else:
        conv = get_conversation(conversation_id)
        if conv:
            if g.auth_type == "user":
                if conv["user_id"] != g.user_id:
                    return jsonify({"error": "غير مصرح."}), 403
            elif g.auth_type == "guest":
                if conv["guest_id"] != g.guest_id:
                    return jsonify({"error": "غير مصرح."}), 403
            else:
                return jsonify({"error": "غير مصرح."}), 403
        else:
            conversation_id = create_conversation(
                user_id=g.user_id,
                guest_id=g.guest_id,
                title=_auto_title(user_message),
            )

    add_message(conversation_id, "user", user_message)

    # Get services
    search_laws = _services["search_laws"]
    ask_llm = _services["ask_llm"]
    conversation_memory = _services["conversation_memory"]
    case_memory = _services["case_memory"]
    LegalReasoningChain = _services["LegalReasoningChain"]
    format_citation = _services["format_citation"]
    build_citation_block = _services["build_citation_block"]

    # Step 1: Conversation context
    conversation_context = conversation_memory.get_context(session_id)

    # Step 2 & 3 & 4: Deep reasoning / Retrieval / Contradiction / Confidence
    reasoning_context = ""
    detected_category = "other"
    confidence_data = {}
    contradictions_data = []
    risks_data = []
    prosecution_data = {}
    defense_data = {}
    judge_decision_data = {}
    entities_data = {}
    timeline_data = {"events": [], "warnings": []}
    success_probability_data = {"probability": 50, "pros": [], "cons": [], "precedents": []}

    # Search laws (Retrieval) with Redis caching
    cache_key = f"qanuni:search:{user_message}:{source_filter}:{type_filter}:{category_filter}:{web_search_enabled}"
    results = cache.get(cache_key)
    if not results:
        results = search_laws(
            user_message,
            source_filter=source_filter,
            type_filter=type_filter,
            category_filter=category_filter,
        )

        if web_search_enabled:
            try:
                from core.web_search import _ddg_search
                print(f"[WebSearch] Executing pre-reasoning web search for: {user_message[:50]}...")
                web_raw = _ddg_search(user_message, max_results=4)
                web_results = []
                for r in web_raw:
                    web_results.append({
                        "law_name": r.get("title", "نتيجة بحث ويب"),
                        "text": r.get("body", ""),
                        "categories": "إنترنت / أخبار",
                        "source": "web_search",
                        "type": "web",
                        "citation": r.get("href", ""),
                        "article_number": "ويب",
                        "reranker_score": 0.95,
                        "retrieval_method": "web_search"
                    })
                results = web_results + results
            except Exception as e:
                print(f"[WebSearch] Pre-reasoning search failed: {e}")
        cache.set(cache_key, results, ttl=1800)

    if data.get("force_legal_verification", False) or web_search_enabled:
        try:
            from core.legal_validator import validate_retrieved_laws
            validate_retrieved_laws(results, question=user_message, force=bool(data.get("force_legal_verification", False)))
            for result in results:
                if result.get("legal_status") == "unverified":
                    result["text"] = "[LEGAL STATUS: UNVERIFIED — do not present as definitely current] " + result.get("text", "")
        except Exception as exc:
            print(f"[LegalValidator] verification unavailable: {exc}")
            for result in results:
                if result.get("source") != "web_search":
                    result["legal_status"] = "unverified"

    deep_reasoner = _services.get("deep_reasoner")
    contradiction_detector = _services.get("contradiction_detector")
    confidence_scorer = _services.get("confidence_scorer")

    if deep_reasoner and contradiction_detector and confidence_scorer:
        try:
            trace = deep_reasoner.run(
                user_message,
                results,
                contradiction_detector,
                generate_fn=None  # Fast deterministic reasoning (<10ms)
            )
            reasoning_context = trace.reasoning_context
            detected_category = trace.primary_category
            
            prosecution_data = trace.prosecution
            defense_data = trace.defense
            judge_decision_data = trace.judge_decision
            entities_data = getattr(trace, "entities", {})
            timeline_data = getattr(trace, "timeline", {"events": [], "warnings": []})
            success_probability_data = getattr(trace, "success_probability_data", {"probability": 50, "pros": [], "cons": [], "precedents": []})

            # Compute confidence score
            confidence_data = confidence_scorer.score(
                user_message,
                trace.facts,
                {"primary_category": trace.primary_category, "classification_confidence": trace.overall_confidence},
                results,
                trace.contradictions
            )
            contradictions_data = trace.contradictions
            risks_data = [
                {
                    "issue": r["issue"] if isinstance(r, dict) else r.issue,
                    "severity": r["severity"] if isinstance(r, dict) else r.severity,
                    "probability": r["probability"] if isinstance(r, dict) else r.probability,
                    "risk_level": r["risk_level"] if isinstance(r, dict) else r.risk_level,
                    "related_law": r.get("related_law", "") if isinstance(r, dict) else getattr(r, "related_law", ""),
                    "description": r["description"] if isinstance(r, dict) else r.description
                } for r in trace.risk_assessment
            ]
        except Exception as e:
            print(f"[!] Deep reasoning chain error: {e}. Falling back to old chain.")
            reasoning_chain = LegalReasoningChain()
            try:
                chain_result = reasoning_chain.run_full_chain(user_message, results)
                reasoning_context = chain_result.get("reasoning_context", "")
                detected_category = chain_result.get("primary_category", detected_category)
            except Exception as e2:
                print(f"[!] Reasoning fallback chain error: {e2}")
    else:
        reasoning_chain = LegalReasoningChain()
        try:
            chain_result = reasoning_chain.run_full_chain(user_message, results)
            reasoning_context = chain_result.get("reasoning_context", "")
            detected_category = chain_result.get("primary_category", detected_category)
        except Exception as e:
            print(f"[!] Reasoning chain error: {e}")

    # Step 5: Citations
    citation_block = build_citation_block(results)

    # Step 5.5: Web search hint
    web_search_hint = ""
    if web_search_enabled:
        web_search_hint = (
            "### تعليمات إضافية:\n"
            "المستخدم فعّل البحث على الإنترنت. "
            "استخدم أدوات البحث المتاحة لك (search_web أو search_court_rulings أو search_legal_news) "
            "للبحث عن معلومات إضافية على الإنترنت تكمل النصوص المسترجعة من قاعدة البيانات."
        )

    # Step 6: Ask LLM
    answer = ask_llm(
        user_message,
        results,
        reasoning_context=reasoning_context,
        conversation_context=conversation_context,
        citation_block=citation_block,
        web_search_hint=web_search_hint,
    )

    # Step 7: Update memory
    conversation_memory.add_turn(session_id, "user", user_message)
    conversation_memory.add_turn(session_id, "assistant", answer)

    # Step 8: Save to case memory
    try:
        case_memory.save_case_memory(
            user_query=user_message,
            case_summary=answer[:1000],
            retrieved_laws=results,
            reasoning=reasoning_context,
            category=detected_category,
        )
    except Exception as e:
        print(f"[!] Case memory error: {e}")

    # Step 9: Build sources
    sources = []
    seen = set()
    for r in results:
        key = f"{r.get('source', '')}:{r['law_name']}"
        if key not in seen:
            seen.add(key)
            sources.append({
                "law_name": r["law_name"],
                "categories": r["categories"],
                "source": r.get("source", ""),
                "type": r.get("type", ""),
                "citation": r.get("citation", format_citation(r)),
                "article_number": r.get("article_number", ""),
                "legal_status": r.get("legal_status", "unverified"),
                "legal_verification": r.get("legal_verification", {}),
                "snippet": r["text"][:300] + ("…" if len(r["text"]) > 300 else ""),
                "reranker_score": round(r.get("reranker_score", 0), 4),
                "retrieval_method": r.get("retrieval_method", ""),
            })

    add_message(
        conversation_id,
        "assistant",
        answer,
        sources,
        prosecution=prosecution_data,
        defense=defense_data,
        judge_decision=judge_decision_data,
    )
    new_remaining = increment_guest_count()

    conv = get_conversation(conversation_id)
    if conv and conv["title"] == "محادثة جديدة":
        update_conversation_title(conversation_id, _auto_title(user_message))

    # Step 10: Generate fast follow-up questions
    follow_ups = _get_fast_follow_ups(detected_category, user_message)

    return jsonify({
        "answer": answer,
        "sources": sources,
        "classification": detected_category,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "remaining_messages": new_remaining,
        "confidence": confidence_data,
        "contradictions": contradictions_data,
        "risk_assessment": risks_data,
        "prosecution": prosecution_data,
        "defense": defense_data,
        "judge_decision": judge_decision_data,
        "entities": entities_data,
        "timeline": timeline_data,
        "success_probability": success_probability_data,
        "follow_ups": follow_ups
    })


@chat_bp.route("/api/chat/stream", methods=["POST"])
@rate_limit(max_per_minute=15)
@require_auth_or_guest
def chat_stream():
    """SSE streaming chat endpoint."""
    allowed, remaining, error = enforce_guest_limit()
    if not allowed:
        return error

    data = request.get_json(force=True)
    user_message = data.get("message", "").strip()
    if not user_message:
        return jsonify({"error": "الرسالة فارغة."}), 400

    user_message, _ = sanitize_input(user_message)

    source_filter = data.get("source_filter")
    type_filter = data.get("type_filter")
    category_filter = data.get("category_filter")
    web_search_enabled = data.get("web_search", False)
    conversation_id = data.get("conversation_id")
    session_id = data.get("session_id", "default_session")

    user_id = g.user_id
    guest_id = g.guest_id
    auth_type = g.auth_type

    if conversation_id:
        conv = get_conversation(conversation_id)
        if conv:
            if g.auth_type == "user":
                if conv["user_id"] != g.user_id:
                    return jsonify({"error": "غير مصرح."}), 403
            elif g.auth_type == "guest":
                if conv["guest_id"] != g.guest_id:
                    return jsonify({"error": "غير مصرح."}), 403
            else:
                return jsonify({"error": "غير مصرح."}), 403
        else:
            conversation_id = create_conversation(
                user_id=user_id,
                guest_id=guest_id,
                title=_auto_title(user_message),
            )
    else:
        conversation_id = create_conversation(
            user_id=user_id,
            guest_id=guest_id,
            title=_auto_title(user_message),
        )

    add_message(conversation_id, "user", user_message)

    def generate():
        search_laws = _services["search_laws"]
        conversation_memory = _services["conversation_memory"]
        case_memory = _services["case_memory"]
        LegalReasoningChain = _services["LegalReasoningChain"]
        format_citation = _services["format_citation"]
        build_citation_block = _services["build_citation_block"]
        run_async = _services["run_async"]
        session_service = _services["session_service"]
        runner_obj = _services["runner"]
        types = _services["types"]

        try:
            conversation_context = conversation_memory.get_context(session_id)

            yield f"data: {json.dumps({'type': 'pipeline_stage', 'data': 'classification'})}\n\n"

            prosecution_data = {}
            defense_data = {}
            judge_decision_data = {}
            entities_data = {}
            timeline_data = {"events": [], "warnings": []}
            success_probability_data = {"probability": 50, "pros": [], "cons": [], "precedents": []}
            confidence_data = {}
            contradictions_data = []
            risks_data = []
            # Safe defaults — MUST be set before any try/except block that may set them
            reasoning_context = ""
            detected_category = "other"

            # ── Variable completeness check (before retrieval) ────────
            deep_reasoner = _services.get("deep_reasoner")
            if deep_reasoner:
                try:
                    # Quick classify + fact extract to detect missing variables
                    _pre_facts = deep_reasoner.extract_facts(user_message)
                    _pre_cls   = deep_reasoner.classify(user_message, _pre_facts)
                    missing    = deep_reasoner.detect_missing_variables(user_message, _pre_facts, _pre_cls)
                    if missing:
                        opts = " | ".join(missing["options_ar"])
                        clarification = f"{missing['question_ar']}\n({opts})"
                        yield f"data: {json.dumps({'type': 'token', 'data': clarification}, ensure_ascii=False)}\n\n"
                        yield f"data: {json.dumps({'type': 'done', 'conversation_id': conversation_id, 'remaining_messages': -1})}\n\n"
                        return
                except Exception as e:
                    print(f"[!] Pre-check variable detection error: {e}")
            # ─────────────────────────────────────────────────────────

            # Step: Retrieval stage
            yield f"data: {json.dumps({'type': 'pipeline_stage', 'data': 'retrieval'})}\n\n"

            # Search laws (Retrieval) with Redis caching
            cache_key = f"qanuni:search:{user_message}:{source_filter}:{type_filter}:{category_filter}:{web_search_enabled}"
            results = cache.get(cache_key)
            if not results:
                results = search_laws(
                    user_message,
                    source_filter=source_filter,
                    type_filter=type_filter,
                    category_filter=category_filter,
                )

                if web_search_enabled:
                    try:
                        from core.web_search import _ddg_search
                        print(f"[WebSearch] Executing pre-reasoning web search for: {user_message[:50]}...")
                        web_raw = _ddg_search(user_message, max_results=4)
                        web_results = []
                        for r in web_raw:
                            web_results.append({
                                "law_name": r.get("title", "نتيجة بحث ويب"),
                                "text": r.get("body", ""),
                                "categories": "إنترنت / أخبار",
                                "source": "web_search",
                                "type": "web",
                                "citation": r.get("href", ""),
                                "article_number": "ويب",
                                "reranker_score": 0.95,
                                "retrieval_method": "web_search"
                            })
                        results = web_results + results
                    except Exception as e:
                        print(f"[WebSearch] Pre-reasoning search failed: {e}")
                cache.set(cache_key, results, ttl=1800)

            if data.get("force_legal_verification", False) or web_search_enabled:
                try:
                    from core.legal_validator import validate_retrieved_laws
                    validate_retrieved_laws(results, question=user_message, force=bool(data.get("force_legal_verification", False)))
                    for result in results:
                        if result.get("legal_status") == "unverified":
                            result["text"] = "[LEGAL STATUS: UNVERIFIED — do not present as definitely current] " + result.get("text", "")
                except Exception as exc:
                    print(f"[LegalValidator] verification unavailable: {exc}")
                    for result in results:
                        if result.get("source") != "web_search":
                            result["legal_status"] = "unverified"

            # Step: Reasoning stage
            yield f"data: {json.dumps({'type': 'pipeline_stage', 'data': 'reasoning'})}\n\n"

            deep_reasoner = _services.get("deep_reasoner")
            contradiction_detector = _services.get("contradiction_detector")
            confidence_scorer = _services.get("confidence_scorer")

            if deep_reasoner and contradiction_detector and confidence_scorer:
                try:
                    trace = deep_reasoner.run(
                        user_message,
                        results,
                        contradiction_detector,
                        generate_fn=None  # Fast deterministic reasoning (<10ms)
                    )
                    reasoning_context = trace.reasoning_context
                    detected_category = trace.primary_category
                    
                    prosecution_data = trace.prosecution
                    defense_data = trace.defense
                    judge_decision_data = trace.judge_decision
                    entities_data = getattr(trace, "entities", {})
                    timeline_data = getattr(trace, "timeline", {"events": [], "warnings": []})
                    success_probability_data = getattr(trace, "success_probability_data", {"probability": 50, "pros": [], "cons": [], "precedents": []})

                    # Compute confidence score
                    confidence_data = confidence_scorer.score(
                        user_message,
                        trace.facts,
                        {"primary_category": trace.primary_category, "classification_confidence": trace.overall_confidence},
                        results,
                        trace.contradictions
                    )
                    contradictions_data = trace.contradictions
                    risks_data = [
                        {
                            "issue": r["issue"] if isinstance(r, dict) else r.issue,
                            "severity": r["severity"] if isinstance(r, dict) else r.severity,
                            "probability": r["probability"] if isinstance(r, dict) else r.probability,
                            "risk_level": r["risk_level"] if isinstance(r, dict) else r.risk_level,
                            "related_law": r.get("related_law", "") if isinstance(r, dict) else getattr(r, "related_law", ""),
                            "description": r["description"] if isinstance(r, dict) else r.description
                        } for r in trace.risk_assessment
                    ]
                except Exception as e:
                    print(f"[!] Deep reasoning chain error: {e}. Falling back to old chain.")
                    reasoning_chain = LegalReasoningChain()
                    try:
                        chain_result = reasoning_chain.run_full_chain(user_message, results)
                        reasoning_context = chain_result.get("reasoning_context", "")
                        detected_category = chain_result.get("primary_category", detected_category)
                    except Exception as e2:
                        print(f"[!] Reasoning fallback chain error: {e2}")
            else:
                reasoning_chain = LegalReasoningChain()
                try:
                    chain_result = reasoning_chain.run_full_chain(user_message, results)
                    reasoning_context = chain_result.get("reasoning_context", "")
                    detected_category = chain_result.get("primary_category", detected_category)
                except Exception as e:
                    print(f"[!] Reasoning chain error: {e}")

            # Step: Decision stage
            yield f"data: {json.dumps({'type': 'pipeline_stage', 'data': 'decision'})}\n\n"

            citation_block = build_citation_block(results)

            # Build sources
            sources = []
            seen = set()
            for r in results:
                key = f"{r.get('source', '')}:{r['law_name']}"
                if key not in seen:
                    seen.add(key)
                    sources.append({
                        "law_name": r["law_name"],
                        "categories": r["categories"],
                        "source": r.get("source", ""),
                        "citation": r.get("citation", format_citation(r)),
                        "article_number": r.get("article_number", ""),
                        "legal_status": r.get("legal_status", "unverified"),
                        "legal_verification": r.get("legal_verification", {}),
                        "snippet": r["text"][:300],
                        "reranker_score": round(r.get("reranker_score", 0), 4),
                    })

            yield f"data: {json.dumps({'type': 'sources', 'data': sources}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'classification', 'data': detected_category})}\n\n"
            yield f"data: {json.dumps({'type': 'confidence', 'data': confidence_data}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'contradictions', 'data': contradictions_data}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'risks', 'data': risks_data}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'prosecution', 'data': prosecution_data}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'defense', 'data': defense_data}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'judge_decision', 'data': judge_decision_data}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'entities', 'data': entities_data}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'success_probability', 'data': success_probability_data}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'timeline', 'data': timeline_data}, ensure_ascii=False)}\n\n"

            # Build LLM prompt
            context_parts = []
            if conversation_context:
                context_parts.append(conversation_context)
                context_parts.append("")
            if reasoning_context:
                context_parts.append("### تحليل منهجي متعدد الخطوات:")
                context_parts.append(reasoning_context)
                context_parts.append("")

            context_parts.append("### النصوص القانونية المسترجعة:")
            for i, ex in enumerate(results, 1):
                context_parts.append(
                    f"--- Excerpt {i} ---\n"
                    f"Law: {ex['law_name']}\n"
                    f"Source: {ex.get('source', '')}\n"
                    f"Text: {ex['text'][:1500]}\n"
                )

            if not results:
                context_parts.append("(لا توجد نصوص قانونية مطابقة في قاعدة البيانات.)")

            if citation_block:
                context_parts.append(citation_block)

            context_block = "\n".join(context_parts)

            web_search_hint = ""
            if web_search_enabled:
                web_search_hint = (
                    "### تعليمات إضافية:\n"
                    "المستخدم فعّل البحث على الإنترنت. "
                    "استخدم أدوات البحث المتاحة لك للبحث عن معلومات إضافية."
                )

            if web_search_hint:
                prompt = f"{context_block}\n\n{web_search_hint}\n\n### User Question:\n{user_message}"
            else:
                prompt = f"{context_block}\n\n### User Question:\n{user_message}"

            # True real-time token streaming from agent via queue bridge
            token_queue = queue.Queue()
            ev_loop = _services.get("event_loop")

            async def _stream_producer():
                try:
                    uid = "default_user"
                    sid = str(uuid.uuid4())
                    sess_result = session_service.create_session(
                        app_name="qanuni", user_id=uid, session_id=sid,
                    )
                    if inspect.iscoroutine(sess_result):
                        session = await sess_result
                    else:
                        session = sess_result

                    content = types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=prompt)],
                    )

                    async for event in runner_obj.run_async(
                        user_id=uid, session_id=session.id, new_message=content,
                    ):
                        if event.content and event.content.parts:
                            for part in event.content.parts:
                                if part.text:
                                    token_queue.put(part.text)
                except Exception as err:
                    token_queue.put(err)
                finally:
                    token_queue.put(None)

            if ev_loop and ev_loop.is_running():
                asyncio.run_coroutine_threadsafe(_stream_producer(), ev_loop)
            else:
                run_async(_stream_producer())

            full_response = ""
            while True:
                try:
                    item = token_queue.get(timeout=60)
                except queue.Empty:
                    break
                if item is None:
                    break
                if isinstance(item, Exception):
                    print(f"[!] Streaming producer error: {item}")
                    break
                full_response += item
                yield f"data: {json.dumps({'type': 'token', 'data': item}, ensure_ascii=False)}\n\n"

            if not full_response:
                full_response = "عذراً، مفيش رد متاح دلوقتي."
                yield f"data: {json.dumps({'type': 'token', 'data': full_response}, ensure_ascii=False)}\n\n"

            conversation_memory.add_turn(session_id, "user", user_message)
            conversation_memory.add_turn(session_id, "assistant", full_response)
            add_message(
                conversation_id,
                "assistant",
                full_response,
                sources,
                prosecution=prosecution_data,
                defense=defense_data,
                judge_decision=judge_decision_data,
            )

            try:
                case_memory.save_case_memory(
                    user_query=user_message,
                    case_summary=full_response[:1000],
                    retrieved_laws=results,
                    reasoning=reasoning_context,
                    category=detected_category,
                )
            except Exception:
                pass

            if auth_type == "guest" and guest_id:
                from api.models import increment_guest_usage
                new_count = increment_guest_usage(guest_id)
                new_remaining = max(0, GUEST_MESSAGE_LIMIT - new_count)
            else:
                new_remaining = -1

            yield f"data: {json.dumps({'type': 'done', 'conversation_id': conversation_id, 'remaining_messages': new_remaining})}\n\n"
            
            # Generate fast follow-up questions for the stream
            follow_ups = _get_fast_follow_ups(detected_category, user_message)
            yield f"data: {json.dumps({'type': 'follow_ups', 'data': follow_ups}, ensure_ascii=False)}\n\n"

        except Exception as e:
            import traceback
            print(f"[!] Streaming error: {e}")
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'data': 'حصل خطأ أثناء المعالجة.'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@chat_bp.route("/api/debate", methods=["POST"])
@rate_limit(max_per_minute=15)
@require_auth_or_guest
def debate():
    """Debate endpoint — AI plays prosecution or defense."""
    allowed, remaining, error = enforce_guest_limit()
    if not allowed:
        return error

    data = request.get_json(force=True)
    user_message = data.get("message", "").strip()
    case_description = data.get("case_description", "").strip()
    session_id = data.get("session_id", "debate_" + str(uuid.uuid4())[:8])
    user_role = data.get("user_role", "defense")
    conversation_id = data.get("conversation_id")

    if not user_message and not case_description:
        return jsonify({"error": "أرسل رسالة أو وصف القضية"}), 400

    user_message, _ = sanitize_input(user_message)
    if case_description:
        case_description, _ = sanitize_input(case_description)

    if conversation_id:
        conv = get_conversation(conversation_id)
        if conv:
            if g.auth_type == "user":
                if conv["user_id"] != g.user_id:
                    return jsonify({"error": "غير مصرح."}), 403
            elif g.auth_type == "guest":
                if conv["guest_id"] != g.guest_id:
                    return jsonify({"error": "غير مصرح."}), 403
            else:
                return jsonify({"error": "غير مصرح."}), 403
        else:
            conversation_id = create_conversation(
                user_id=g.user_id,
                guest_id=g.guest_id,
                title="مناظرة: " + _auto_title(case_description or user_message),
                mode="debate",
            )
    else:
        conversation_id = create_conversation(
            user_id=g.user_id,
            guest_id=g.guest_id,
            title="مناظرة: " + _auto_title(case_description or user_message),
            mode="debate",
        )

    msg_count_before = get_conversation_message_count(conversation_id)
    if msg_count_before >= 5:
        return jsonify({"error": "انتهت الجلسة ونطق القاضي بالحكم. يرجى بدء محادثة أو مرافعة جديدة."}), 400

    add_message(conversation_id, "user", user_message)
    msg_count_after = msg_count_before + 1

    search_laws = _services["search_laws"]
    conversation_memory = _services["conversation_memory"]
    format_citation = _services["format_citation"]
    run_async = _services["run_async"]
    session_service = _services["session_service"]
    types = _services["types"]
    generate_text = _services.get("generate_text")

    # If msg_count_after is 5, this is the Judge's Verdict round!
    if msg_count_after == 5:
        try:
            # 1. Fetch all messages in this conversation
            messages = get_messages(conversation_id)
            
            # 2. Build full transcript
            transcript_parts = []
            for m in messages:
                role_label = ""
                if m["role"] == "user":
                    role_label = "الدفاع (المستخدم)" if user_role == "defense" else "الادعاء (المستخدم)"
                else:
                    role_label = "الادعاء (الذكاء الاصطناعي)" if user_role == "defense" else "الدفاع (الذكاء الاصطناعي)"
                transcript_parts.append(f"{role_label}: {m['content']}")
            full_transcript = "\n".join(transcript_parts)
            
            # 3. Call the Judge LLM
            system_prompt = (
                "أنت قاضٍ جنائي ومدني مصري جليل ومحايد. تقوم بتقييم المحاكمات والمرافعات بدقة تامة طبقاً لأحكام القانون المصري والمبادئ القضائية لمحكمة النقض. "
                "ستقوم بمراجعة وقائع القضية بالكامل، ومرافعة الادعاء، ومرافعة الدفاع. بناءً عليه، ستصدر حكماً عادلاً وتُقيم أداء المحامي (المستخدم) بنقاط قوة ونقاط ضعف ونصائح قانونية لتحسين أدائه. "
                "يجب أن يكون ردك بصيغة JSON صالحة ومطابقة للهيكل التالي تماماً ودون أي نصوص إضافية أو مقدمات:\n\n"
                "{\n"
                '  "verdict": "guilty" أو "not_guilty" أو "partial",\n'
                '  "verdict_ar": "نص الحكم بالعربية الفصحى (مثال: إدانة المتهم بالسجن أو براءة المتهم لعدم كفاية الأدلة)",\n'
                '  "reasoning": "حيثيات وأسباب الحكم بالتفصيل القانوني ومواد القانون المصري"،\n'
                '  "score": 85,\n'
                '  "strengths": ["نقطة قوة 1", "نقطة قوة 2"],\n'
                '  "weaknesses": ["نقطة ضعف 1", "نقطة ضعف 2"],\n'
                '  "advice": "نصيحة قانونية للمحامي لتحسين أدائه ومرافعاته القادمة"\n'
                "}"
            )
            prompt = (
                "المستند التالي هو وقائع الجلسات والمرافعات الشفهية والمكتوبة في القضية:\n\n"
                f"{full_transcript}\n\n"
                "يرجى إصدار الحكم النهائي وتقييم أداء المحامي باللغة العربية الفصحى بصيغة JSON فقط."
            )
            
            judge_response = generate_text(prompt, system_prompt).strip()
            
            # Clean JSON from markdown code blocks
            clean_json_match = re.search(r"({.*})", judge_response, re.DOTALL)
            if clean_json_match:
                judge_response = clean_json_match.group(1)
                
            import json
            judge_data = json.loads(judge_response)
        except Exception as e:
            # Fallback judge structure in case of any JSON/processing error
            judge_data = {
                "verdict": "partial",
                "verdict_ar": "حكم جزئي أو تمهيدي من المحكمة",
                "reasoning": "قررت المحكمة إصدار حكم تمهيدي بندب خبير لإجراء المعاينة الفنية.",
                "score": 75,
                "strengths": ["تقديم الحجة القانونية الأولية"],
                "weaknesses": ["نقص تفصيل الدفوع والمواد القانونية"],
                "advice": "يرجى تقديم مذكرة دفاع أكثر تفصيلاً تشمل أحكام محكمة النقض المصرية."
            }
            
        verdict_text = (
            f"⚖️ **نطق القضاء بالحكم النهائي:**\n\n"
            f"**الحكم:** {judge_data.get('verdict_ar', '')}\n\n"
            f"**حيثيات وأسباب الحكم:**\n{judge_data.get('reasoning', '')}\n\n"
            f"**درجة تقييم أداء المرافعة:** {judge_data.get('score', 0)}/100"
        )
        
        judge_decision = {
            "verdict": judge_data.get("verdict", "partial"),
            "verdict_ar": judge_data.get("verdict_ar", ""),
            "reasoning": judge_data.get("reasoning", ""),
            "key_factors": judge_data.get("strengths", []),
            "confidence": float(judge_data.get("score", 0)) / 100.0
        }
        
        scorecard = {
            "score": judge_data.get("score", 0),
            "strengths": judge_data.get("strengths", []),
            "weaknesses": judge_data.get("weaknesses", []),
            "advice": judge_data.get("advice", "")
        }
        
        add_message(conversation_id, "assistant", verdict_text, prosecution=None, defense=None, judge_decision=judge_decision)
        new_remaining = increment_guest_count()
        
        return jsonify({
            "answer": verdict_text,
            "judge_decision": judge_decision,
            "scorecard": scorecard,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "mode": "debate",
            "trial_finished": True,
            "remaining_messages": new_remaining,
        })

    # Standard Prosecution / Defense rounds (Round 1 & Round 2)
    if user_role == "prosecution":
        chosen_runner = _services["debate_runner_defense"]
        app_name = "qanuni_debate_defense"
        user_label = "حجة النيابة (المستخدم)"
    else:
        chosen_runner = _services["debate_runner_prosecution"]
        app_name = "qanuni_debate_prosecution"
        user_label = "حجة الدفاع (المستخدم)"

    conversation_context = conversation_memory.get_context(session_id)
    search_query = case_description if case_description else user_message
    results = search_laws(search_query)

    law_parts = []
    if case_description:
        law_parts.append(f"### وصف القضية:\n{case_description}\n")
    if conversation_context:
        law_parts.append(conversation_context)
    law_parts.append("### النصوص القانونية المسترجعة:")
    for i, ex in enumerate(results, 1):
        law_parts.append(
            f"--- Excerpt {i} ---\n"
            f"Law: {ex['law_name']}\n"
            f"Citation: {ex.get('citation', format_citation(ex))}\n"
            f"Text: {ex['text'][:1500]}\n"
        )

    context_block = "\n".join(law_parts)
    prompt = f"{context_block}\n\n### {user_label}:\n{user_message}"

    async def _run_debate():
        uid = "debate_user"
        sid = str(uuid.uuid4())
        sess_result = session_service.create_session(
            app_name=app_name, user_id=uid, session_id=sid,
        )
        if inspect.iscoroutine(sess_result):
            session = await sess_result
        else:
            session = sess_result

        content = types.Content(
            role="user", parts=[types.Part.from_text(text=prompt)],
        )
        parts = []
        async for event in chosen_runner.run_async(
            user_id=uid, session_id=session.id, new_message=content,
        ):
            if event.content and event.content.parts:
                for p in event.content.parts:
                    if p.text:
                        parts.append(p.text)
        return "\n".join(parts) if parts else "مفيش رد متاح."

    answer = run_async(_run_debate())

    conversation_memory.add_turn(session_id, "user", user_message)
    conversation_memory.add_turn(session_id, "assistant", answer)

    sources = []
    seen = set()
    for r in results:
        key = f"{r.get('source', '')}:{r['law_name']}"
        if key not in seen:
            seen.add(key)
            sources.append({
                "law_name": r["law_name"],
                "citation": r.get("citation", format_citation(r)),
                "snippet": r["text"][:300],
            })

    add_message(conversation_id, "assistant", answer, sources)
    new_remaining = increment_guest_count()

    return jsonify({
        "answer": answer,
        "sources": sources,
        "session_id": session_id,
        "conversation_id": conversation_id,
        "mode": "debate",
        "trial_finished": False,
        "remaining_messages": new_remaining,
    })


@chat_bp.route("/api/conversation/clear", methods=["POST"])
def clear_conversation():
    """Clear conversational memory for a session."""
    data = request.get_json(force=True)
    session_id = data.get("session_id", "default_session")
    conversation_memory = _services.get("conversation_memory")
    if conversation_memory:
        conversation_memory.clear(session_id)
    return jsonify({"status": "cleared", "session_id": session_id})
