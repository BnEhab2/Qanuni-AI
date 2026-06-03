"""
Qanuni.AI — Chat Endpoints
Main chat, SSE streaming, and debate mode.
"""

import json
import uuid
import inspect
import asyncio
import threading

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
)
from config import GUEST_MESSAGE_LIMIT

chat_bp = Blueprint("chat", __name__)

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
            if g.user_id and conv["user_id"] != g.user_id:
                return jsonify({"error": "غير مصرح."}), 403
            if g.guest_id and conv["guest_id"] != g.guest_id:
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

    # Search laws (Retrieval)
    results = search_laws(
        user_message,
        source_filter=source_filter,
        type_filter=type_filter,
        category_filter=category_filter,
    )

    deep_reasoner = _services.get("deep_reasoner")
    contradiction_detector = _services.get("contradiction_detector")
    confidence_scorer = _services.get("confidence_scorer")

    if deep_reasoner and contradiction_detector and confidence_scorer:
        try:
            trace = deep_reasoner.run(
                user_message,
                results,
                contradiction_detector,
                generate_fn=_services.get("generate_text")
            )
            reasoning_context = trace.reasoning_context
            detected_category = trace.primary_category
            
            prosecution_data = trace.prosecution
            defense_data = trace.defense
            judge_decision_data = trace.judge_decision

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

    if not conversation_id:
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

            prosecution_data = {}
            defense_data = {}
            judge_decision_data = {}

            results = search_laws(
                user_message,
                source_filter=source_filter,
                type_filter=type_filter,
                category_filter=category_filter,
            )

            deep_reasoner = _services.get("deep_reasoner")
            contradiction_detector = _services.get("contradiction_detector")
            confidence_scorer = _services.get("confidence_scorer")

            if deep_reasoner and contradiction_detector and confidence_scorer:
                try:
                    trace = deep_reasoner.run(
                        user_message,
                        results,
                        contradiction_detector,
                        generate_fn=_services.get("generate_text")
                    )
                    reasoning_context = trace.reasoning_context
                    detected_category = trace.primary_category
                    
                    prosecution_data = trace.prosecution
                    defense_data = trace.defense
                    judge_decision_data = trace.judge_decision

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

            # Stream from agent
            async def _stream_agent():
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

                parts = []
                async for event in runner_obj.run_async(
                    user_id=uid, session_id=session.id, new_message=content,
                ):
                    if event.content and event.content.parts:
                        for part in event.content.parts:
                            if part.text:
                                parts.append(part.text)
                return parts

            response_parts = run_async(_stream_agent())

            full_response = ""
            for part_text in response_parts:
                full_response += part_text
                yield f"data: {json.dumps({'type': 'token', 'data': part_text}, ensure_ascii=False)}\n\n"

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

        except Exception as e:
            print(f"[!] Streaming error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'data': 'حصل خطأ أثناء المعالجة.'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
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

    if not conversation_id:
        conversation_id = create_conversation(
            user_id=g.user_id,
            guest_id=g.guest_id,
            title="مناظرة: " + _auto_title(case_description or user_message),
            mode="debate",
        )

    add_message(conversation_id, "user", user_message)

    search_laws = _services["search_laws"]
    conversation_memory = _services["conversation_memory"]
    format_citation = _services["format_citation"]
    run_async = _services["run_async"]
    session_service = _services["session_service"]
    types = _services["types"]

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
