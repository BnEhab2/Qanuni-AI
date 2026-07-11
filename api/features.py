"""
Qanuni.AI — Extra Legal Features
Document Upload & Ingestion, AI Legal Drafting, DOCX Exporter, and Case Management System.
"""

import io
import re
import os
import time
import uuid
from typing import Optional
from pathlib import Path
from flask import Blueprint, request, jsonify, g, send_file
from pypdf import PdfReader
import docx
from docx.shared import Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

from api.middleware import require_auth_or_guest, rate_limit, sanitize_input
from api.chat import _services
from config import BASE_DIR

features_bp = Blueprint("features", __name__)

# Allowed file extensions for document upload
ALLOWED_EXTENSIONS = {"pdf", "docx"}


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _add_formatted_runs(paragraph, text):
    """Parse basic markdown bold and add appropriate runs."""
    parts = text.split("**")
    is_bold = False
    for part in parts:
        if not part:
            is_bold = not is_bold
            continue
        run = paragraph.add_run(part)
        run.font.name = "Arial"
        run.font.size = Pt(11)
        run.font.rtl = True
        if is_bold:
            run.bold = True
        is_bold = not is_bold


def markdown_to_docx(title: str, markdown_text: str) -> docx.Document:
    """Convert simple markdown headings, lists, and paragraphs to a styled RTL DOCX document."""
    doc = docx.Document()
    
    # Page Setup: 1-inch margins
    for section in doc.sections:
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)
        
    # Title Header
    p_title = doc.add_paragraph()
    p_title.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_title.paragraph_format.space_after = Pt(24)
    run_title = p_title.add_run(title)
    run_title.font.name = "Arial"
    run_title.font.size = Pt(18)
    run_title.bold = True
    run_title.font.rtl = True
    
    # Process lines
    lines = markdown_text.split("\n")
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if line.startswith("# "):
            p = doc.add_paragraph()
            p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(6)
            run = p.add_run(line[2:])
            run.bold = True
            run.font.name = "Arial"
            run.font.size = Pt(16)
            run.font.rtl = True
        elif line.startswith("## "):
            p = doc.add_paragraph()
            p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(4)
            run = p.add_run(line[3:])
            run.bold = True
            run.font.name = "Arial"
            run.font.size = Pt(14)
            run.font.rtl = True
        elif line.startswith("### "):
            p = doc.add_paragraph()
            p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p.paragraph_format.space_before = Pt(8)
            p.paragraph_format.space_after = Pt(4)
            run = p.add_run(line[4:])
            run.bold = True
            run.font.name = "Arial"
            run.font.size = Pt(12)
            run.font.rtl = True
        elif line.startswith("- ") or line.startswith("* "):
            p = doc.add_paragraph()
            p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p.paragraph_format.left_indent = Inches(0.25)
            p.paragraph_format.space_after = Pt(4)
            
            run_bullet = p.add_run("• ")
            run_bullet.font.name = "Arial"
            run_bullet.font.size = Pt(11)
            run_bullet.font.rtl = True
            
            _add_formatted_runs(p, line[2:])
        else:
            p = doc.add_paragraph()
            p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.15
            _add_formatted_runs(p, line)
            
    return doc


@features_bp.route("/api/documents/upload", methods=["POST"])
@require_auth_or_guest
@rate_limit(max_per_minute=5)
def upload_document():
    """Upload PDF/DOCX, parse text, search relevant laws, and run Deep Reasoner analysis. Can link to a Case ID."""
    if "file" not in request.files:
        return jsonify({"error": "لم يتم إرسال أي ملف."}), 400
        
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "ملف فارغ أو غير محدد."}), 400
        
    if not allowed_file(file.filename):
        return jsonify({"error": "الملف غير مدعوم. الصيغ المدعومة هي: PDF و DOCX فقط."}), 400
        
    case_id = request.form.get("case_id")
    
    # Extract text from file
    text = ""
    file_type = file.filename.rsplit(".", 1)[1].lower()
    
    try:
        if file_type == "pdf":
            reader = PdfReader(file)
            for page in reader.pages:
                text += page.extract_text() or ""
        elif file_type == "docx":
            # Seek back to beginning before reading since it's a file stream
            file.seek(0)
            doc = docx.Document(file)
            text = "\n".join([p.text for p in doc.paragraphs])
    except Exception as e:
        return jsonify({"error": f"حدث خطأ أثناء قراءة الملف: {str(e)}"}), 500
        
    text = text.strip()
    if not text:
        return jsonify({"error": "فشل استخراج النص من الملف. يرجى التأكد أن الملف يحتوي على نصوص صالحة."}), 400
        
    # Limit text length to prevent LLM context overflow (up to 8000 characters)
    truncated_text = text[:8000]
    
    # Retrieve services
    search_laws = _services.get("search_laws")
    generate_text = _services.get("generate_text")
    deep_reasoner = _services.get("deep_reasoner")
    contradiction_detector = _services.get("contradiction_detector")
    confidence_scorer = _services.get("confidence_scorer")
    
    if not (search_laws and generate_text and deep_reasoner and contradiction_detector and confidence_scorer):
        return jsonify({"error": "خدمات التحليل والبحث غير متوفرة حالياً."}), 500
        
    try:
        # Step 1: Formulate search query using LLM
        query_prompt = (
            "المستند التالي هو ملف قانوني مصري. استخلص منه موضوع النزاع أو القضية الأساسية في جملة واحدة دقيقة باللغة العربية "
            "لاستخدامها كاستعلام للبحث عن نصوص ومواد القانون ذات الصلة:\n\n"
            f"{truncated_text[:2500]}"
        )
        search_query = generate_text(query_prompt).strip()
        if not search_query:
            search_query = truncated_text[:200]
            
        # Step 2: Search relevant laws
        retrieved_laws = search_laws(search_query, top_k=6)
        
        # Step 3: Run Deep Legal Reasoner on the document text
        trace = deep_reasoner.run(
            user_message=truncated_text,
            retrieved_laws=retrieved_laws,
            contradiction_detector=contradiction_detector,
            generate_fn=generate_text
        )
        
        # Step 4: Compute confidence score
        confidence_data = confidence_scorer.score(
            truncated_text,
            trace.facts,
            {"primary_category": trace.primary_category, "classification_confidence": trace.overall_confidence},
            retrieved_laws,
            trace.contradictions
        )
        
        # If case_id is provided, save as attachment and populate CMS timeline / success rate / judgments
        if case_id and g.auth_type == "user":
            from api.models import (
                add_case_attachment,
                add_case_timeline_event,
                save_case_judgment,
                update_case_success_probability
            )
            
            # Save file on disk
            upload_dir = Path(BASE_DIR) / "data" / "attachments"
            upload_dir.mkdir(parents=True, exist_ok=True)
            safe_filename = f"{uuid.uuid4()}.{file_type}"
            dest_path = upload_dir / safe_filename
            
            # Reset file pointer and save file stream
            file.seek(0)
            file.save(dest_path)
            
            # Log in database
            add_case_attachment(case_id, file.filename, str(dest_path), file_type)
            
            # Populate expected judgment
            verdict_ar = trace.judge_decision.get("verdict_ar", "حكم غير نهائي")
            reasoning = trace.judge_decision.get("reasoning", "لم تسرد حيثيات كافية.")
            save_case_judgment(
                case_id=case_id,
                verdict=verdict_ar,
                reasoning=reasoning,
                expected_verdict=verdict_ar,
                prosecution_summary=trace.prosecution.get("argument", ""),
                defense_summary=trace.defense.get("argument", "")
            )
            
            # Populate success probability
            prob = float(trace.success_probability_data.get("probability", 50.0))
            update_case_success_probability(case_id, prob)
            
            # Populate timeline events
            for event in trace.timeline.get("events", []):
                add_case_timeline_event(
                    case_id=case_id,
                    event_date=event.get("date", "غير محدد"),
                    title=event.get("event", "حدث مستخلص"),
                    description=event.get("implication", ""),
                    is_deadline=0
                )
        
        # Build response
        analysis = {
            "filename": file.filename,
            "facts": trace.facts,
            "primary_category": trace.primary_category,
            "prosecution": trace.prosecution,
            "defense": trace.defense,
            "judge_decision": trace.judge_decision,
            "contradictions": trace.contradictions,
            "risks": [
                {
                    "issue": r["issue"] if isinstance(r, dict) else r.issue,
                    "severity": r["severity"] if isinstance(r, dict) else r.severity,
                    "probability": r["probability"] if isinstance(r, dict) else r.probability,
                    "risk_level": r["risk_level"] if isinstance(r, dict) else r.risk_level,
                    "related_law": r.get("related_law", "") if isinstance(r, dict) else getattr(r, "related_law", ""),
                    "description": r["description"] if isinstance(r, dict) else r.description
                } for r in trace.risk_assessment
            ],
            "retrieved_laws": retrieved_laws,
            "confidence": confidence_data,
            "search_query": search_query,
            "entities": getattr(trace, "entities", {}),
            "timeline": getattr(trace, "timeline", {}),
            "success_probability": getattr(trace, "success_probability_data", {})
        }
        return jsonify(analysis)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"حدث خطأ أثناء تحليل المستند: {str(e)}"}), 500


@features_bp.route("/api/draft", methods=["POST"])
@require_auth_or_guest
@rate_limit(max_per_minute=5)
def draft_document():
    """AI-Powered legal drafting for contracts, memorandums, or petitions."""
    data = request.get_json() or {}
    template_type = data.get("template_type")
    case_details = data.get("case_details", "").strip()
    custom_instructions = data.get("custom_instructions", "").strip()
    
    if not template_type or not case_details:
        return jsonify({"error": "معاملات ناقصة. يرجى توفير template_type و case_details."}), 400
        
    if template_type not in ["contract", "memo", "petition"]:
        return jsonify({"error": "نوع القالب غير مدعوم."}), 400
        
    generate_text = _services.get("generate_text")
    if not generate_text:
        return jsonify({"error": "خدمة صياغة النصوص غير متوفرة."}), 500
        
    system_instruction = (
        "أنت خبير صياغة قانونية مصري ممتاز. تقوم بصياغة العقود والمذكرات القانونية وصحف الدعاوى بدقة بالغة وبأسلوب قانوني بليغ ورصين طبقاً لأحكام القانون المصري والمحاكم المصرية. "
        "يجب أن تبدأ الوثيقة بعنوان واضح، تليها الديباجة والأطراف، ثم البنود والشروط بالتفصيل، ثم الخاتمة وتوقيعات الأطراف. "
        "اكتب الصياغة باللغة العربية الفصحى ونسقها باستخدام Markdown بشكل جميل ومنظم للغاية وبشكل احترافي تام ودون أي تعليقات جانبية خارج النص المكتوب."
    )
    
    if template_type == "contract":
        prompt = (
            "قم بصياغة عقد قانوني متكامل ومنظم ببنود واضحة بناءً على التفاصيل التالية:\n\n"
            f"التفاصيل:\n{case_details}\n\n"
            f"تعليمات إضافية:\n{custom_instructions}"
        )
    elif template_type == "memo":
        prompt = (
            "قم بصياغة مذكرة دفاع قانونية رسمية موجهة للمحكمة تتناول الدفوع والأدلة والطلبات بناءً على التفاصيل التالية:\n\n"
            f"التفاصيل:\n{case_details}\n\n"
            f"تعليمات إضافية:\n{custom_instructions}"
        )
    else:  # petition
        prompt = (
            "قم بصياغة صحيفة دعوى قانونية رسمية لتقديمها للمحكمة تشمل الأطراف وموضوع الدعوى والطلبات والوكالة بناءً على التفاصيل التالية:\n\n"
            f"التفاصيل:\n{case_details}\n\n"
            f"تعليمات إضافية:\n{custom_instructions}"
        )
        
    try:
        drafted_text = generate_text(prompt, system_instruction)
        return jsonify({
            "template_type": template_type,
            "draft": drafted_text
        })
    except Exception as e:
        return jsonify({"error": f"حدث خطأ أثناء الصياغة: {str(e)}"}), 500


@features_bp.route("/api/export/docx", methods=["POST"])
@require_auth_or_guest
def export_docx():
    """Export markdown draft text to a styled RTL DOCX file."""
    data = request.get_json() or {}
    title = data.get("title", "مستند قانوني").strip()
    content = data.get("content", "").strip()
    
    if not content:
        return jsonify({"error": "المستند فارغ. لا يمكن تصدير مستند فارغ."}), 400
        
    try:
        # Clean title for filename
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title)[:50]
        doc = markdown_to_docx(safe_title, content)
        
        buffer = io.BytesIO()
        doc.save(buffer)
        buffer.seek(0)
        
        return send_file(
            buffer,
            as_attachment=True,
            download_name=f"{safe_title}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    except Exception as e:
        return jsonify({"error": f"حدث خطأ أثناء تصدير ملف Word: {str(e)}"}), 500


@features_bp.route("/api/glossary", methods=["GET"])
def get_glossary():
    """Retrieve key Egyptian legal terms and simplified definitions."""
    glossary = {
        "القوة القاهرة": "حادث مفاجئ غير متوقع يستحيل دفعه ويؤدي إلى عدم إمكانية تنفيذ الالتزام (مثل الكوارث الطبيعية).",
        "المسؤولية التقصيرية": "الالتزام بتعويض الضرر الناتج عن عمل غير مشروع (مثل إتلاف ممتلكات الغير أو حوادث السير).",
        "المسؤولية العقدية": "المسؤولية الناشئة عن عدم تنفيذ الالتزام الناشئ عن العقد أو التأخر في تنفيذه.",
        "العقود الملزمة للجانبين": "العقود التي تنشئ التزامات متبادلة في ذمة كل من المتعاقدين (مثل عقد البيع أو الإيجار).",
        "التقادم المسقط": "مرور فترة زمنية يحددها القانون دون المطالبة بالحق، مما يؤدي إلى سقوط حق المطالبة به قضائياً.",
        "الشرط الجزائي": "اتفاق مسبق في العقد يحدد قيمة التعويض المستحق في حال إخلال أحد الأطراف بالتزاماته التعاقدية.",
        "دعوى البطلان": "الدعوى التي تهدف إلى إبطال عقد أو تصرف قانوني لمخالفته أركان الانعقاد أو شروط الصحة.",
        "دعوى الفسخ": "طلب إنهاء العقد وإعادة الأطراف إلى حالتهم قبل التعاقد بسبب عدم قيام الطرف الآخر بتنفيذ التزاماته.",
        "الغبن الفاحش": "عدم التعادل الفادح بين ما يعطيه المتعاقد وما يأخذه، وله أحكام خاصة تؤدي لإبطال أو تعديل العقد.",
        "الجهالة الفاحشة": "عدم وضوح محل التعاقد بشكل يؤدي للنزاع والجهل به يبطل العقد في أحوال معينة.",
        "الأشغال الشاقة": "عقوبة جنائية سالبة للحرية تتضمن تشغيل المحكوم عليه في أعمال مجهدة لصالح الدولة.",
        "التعويض الاتفاقي": "هو ذاته الشرط الجزائي، وهو تعويض يتفق عليه المتعاقدان مسبقاً في حال الإخلال بالعقد."
    }
    return jsonify(glossary)


@features_bp.route("/api/timeline/extract", methods=["POST"])
@require_auth_or_guest
@rate_limit(max_per_minute=10)
def extract_timeline():
    """Analyze legal facts text, extract chronological events, and calculate deadlines/statutes of limitations."""
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    
    if not text:
        return jsonify({"error": "نص الموقف القانوني فارغ."}), 400
        
    generate_text = _services.get("generate_text")
    if not generate_text:
        return jsonify({"error": "خدمة التحليل اللغوي غير متوفرة."}), 500
        
    system_prompt = (
        "أنت خبير قانوني مصري متخصص في استخراج التواريخ وحساب مواعيد مدد السقوط والتقادم القانوني طبقاً لأحكام القانون المدني والجنائي وقانون المرافعات المصري. "
        "مهمتك هي قراءة النص واستخراج الوقائع المرتبطة بتواريخ محددة، ثم تقييم وجود أي مدد تقادم مسقط للحقوق أو مدد سقوط أو مواعيد إجرائية هامة (مثل: 3 سنوات لتقادم دعوى التعويض عن العمل غير المشروع، 15 سنة للتقادم العادي، سنة واحدة لحقوق التجار والعمال، إلخ). "
        "يجب أن يكون ردك بصيغة JSON صالحة ومطابقة للهيكل التالي تماماً ودون أي نصوص إضافية أو تعليقات خارج الجيسون:\n\n"
        "{\n"
        '  "events": [\n'
        '    { "date": "التاريخ المستخرج (مثلاً: 2024/01/15)", "event": "وصف الواقعة باختصار (مثلاً: توقيع عقد البيع)", "implication": "الأثر القانوني لهذه الواقعة" }\n'
        '  ],\n'
        '  "warnings": [\n'
        '    { "type": "نوع التنبيه أو السقوط (مثلاً: تقادم دعوى التعويض)", "days_remaining_or_passed": "المدة المتبقية أو التي انقضت (مثلاً: انقضى التقادم أو متبقي 6 أشهر)", "description": "شرح تفصيلي للتقادم والسند القانوني له" }\n'
        '  ]\n'
        "}"
    )
    
    prompt = (
        "اقرأ النص القانوني التالي واستخرج التواريخ والجدول الزمني والتقادم القانوني المرتبط به بدقة:\n\n"
        f"{text[:4000]}"
    )
    
    try:
        response_text = generate_text(prompt, system_prompt).strip()
        
        # Clean JSON from markdown code blocks
        clean_json_match = re.search(r"({.*})", response_text, re.DOTALL)
        if clean_json_match:
            response_text = clean_json_match.group(1)
            
        import json
        timeline_data = json.loads(response_text)
        return jsonify(timeline_data)
    except Exception as e:
        # Fallback empty structure in case of JSON error
        return jsonify({
            "events": [],
            "warnings": [
                {
                    "type": "خطأ في التحليل",
                    "days_remaining_or_passed": "غير محدد",
                    "description": f"لم نتمكن من تحليل التواريخ والتقادم تلقائياً لهذا النص: {str(e)}"
                }
            ]
        })


# ── Case Management System (CMS) Endpoints ─────────────────────────

@features_bp.route("/api/cases", methods=["GET"])
@require_auth_or_guest
def list_cases():
    if g.auth_type != "user":
        return jsonify({"error": "تسجيل الدخول مطلوب لإدارة القضايا."}), 401
    status = request.args.get("status")
    category = request.args.get("category")
    search_query = request.args.get("search")
    from api.models import get_cases as db_get_cases
    cases = db_get_cases(g.user_id, status=status, category=category, search_query=search_query)
    return jsonify({"cases": cases})


@features_bp.route("/api/cases", methods=["POST"])
@require_auth_or_guest
def add_case():
    if g.auth_type != "user":
        return jsonify({"error": "تسجيل الدخول مطلوب لإدارة القضايا."}), 401
    data = request.get_json() or {}
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    client_name = data.get("client_name", "").strip()
    case_number = data.get("case_number", "").strip()
    court = data.get("court", "").strip()
    category = data.get("category", "").strip()
    success_probability = float(data.get("success_probability", 0.0))
    
    if not title:
        return jsonify({"error": "عنوان القضية مطلوب."}), 400
        
    from api.models import create_case as db_create_case
    case_id = db_create_case(
        g.user_id, title, description, client_name, case_number, court, category, success_probability
    )
    return jsonify({"case_id": case_id, "message": "تم إضافة القضية بنجاح!"}), 201


@features_bp.route("/api/cases/<case_id>", methods=["GET"])
@require_auth_or_guest
def get_case_info(case_id: str):
    if g.auth_type != "user":
        return jsonify({"error": "تسجيل الدخول مطلوب لإدارة القضايا."}), 401
    from api.models import get_case_details as db_get_case_details
    case = db_get_case_details(case_id)
    if not case:
        return jsonify({"error": "القضية غير موجودة."}), 404
    if case["user_id"] != g.user_id:
        return jsonify({"error": "غير مصرح لك باستعراض هذه القضية."}), 403
    return jsonify({"case": case})


@features_bp.route("/api/cases/<case_id>/notes", methods=["POST"])
@require_auth_or_guest
def add_case_note_api(case_id: str):
    if g.auth_type != "user":
        return jsonify({"error": "تسجيل الدخول مطلوب لإدارة القضايا."}), 401
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "محتوى الملاحظة مطلوب."}), 400
        
    from api.models import get_case_details as db_get_case_details, add_case_note as db_add_case_note
    case = db_get_case_details(case_id)
    if not case:
        return jsonify({"error": "القضية غير موجودة."}), 404
    if case["user_id"] != g.user_id:
        return jsonify({"error": "غير مصرح لك."}), 403
        
    note_id = db_add_case_note(case_id, g.user_id, content)
    return jsonify({"note_id": note_id, "message": "تم إضافة الملاحظة بنجاح!"}), 201


@features_bp.route("/api/cases/<case_id>/timeline", methods=["POST"])
@require_auth_or_guest
def add_case_timeline_api(case_id: str):
    if g.auth_type != "user":
        return jsonify({"error": "تسجيل الدخول مطلوب لإدارة القضايا."}), 401
    data = request.get_json() or {}
    event_date = data.get("event_date", "").strip()
    title = data.get("title", "").strip()
    description = data.get("description", "").strip()
    is_deadline = int(data.get("is_deadline", 0))
    
    if not event_date or not title:
        return jsonify({"error": "التاريخ وعنوان الحدث مطلوبان."}), 400
        
    from api.models import get_case_details as db_get_case_details, add_case_timeline_event as db_add_event
    case = db_get_case_details(case_id)
    if not case:
        return jsonify({"error": "القضية غير موجودة."}), 404
    if case["user_id"] != g.user_id:
        return jsonify({"error": "غير مصرح لك."}), 403
        
    event_id = db_add_event(case_id, event_date, title, description, is_deadline)
    return jsonify({"event_id": event_id, "message": "تم إضافة الحدث للجدول الزمني بنجاح!"}), 201


@features_bp.route("/api/cases/<case_id>/attachments", methods=["POST"])
@require_auth_or_guest
def add_case_attachment_api(case_id: str):
    if g.auth_type != "user":
        return jsonify({"error": "تسجيل الدخول مطلوب لإدارة القضايا."}), 401
    if "file" not in request.files:
        return jsonify({"error": "لم يتم إرسال أي ملف."}), 400
        
    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "ملف فارغ أو غير محدد."}), 400
        
    if not allowed_file(file.filename):
        return jsonify({"error": "الملف غير مدعوم."}), 400
        
    from api.models import get_case_details as db_get_case_details, add_case_attachment as db_add_attachment
    case = db_get_case_details(case_id)
    if not case:
        return jsonify({"error": "القضية غير موجودة."}), 404
    if case["user_id"] != g.user_id:
        return jsonify({"error": "غير مصرح لك."}), 403
        
    upload_dir = Path(BASE_DIR) / "data" / "attachments"
    upload_dir.mkdir(parents=True, exist_ok=True)
    
    file_ext = file.filename.rsplit(".", 1)[1].lower()
    safe_filename = f"{uuid.uuid4()}.{file_ext}"
    dest_path = upload_dir / safe_filename
    
    try:
        file.save(dest_path)
        att_id = db_add_attachment(case_id, file.filename, str(dest_path), file_ext)
        return jsonify({"attachment_id": att_id, "file_name": file.filename, "message": "تم تحميل الملف بنجاح!"}), 201
    except Exception as e:
        return jsonify({"error": f"فشل تحميل الملف: {str(e)}"}), 500


@features_bp.route("/api/cases/analytics", methods=["GET"])
@require_auth_or_guest
def get_cms_analytics_api():
    if g.auth_type != "user":
        return jsonify({"error": "تسجيل الدخول مطلوب لإدارة القضايا."}), 401
    from api.models import get_cms_analytics as db_get_analytics
    analytics = db_get_analytics(g.user_id)
    return jsonify(analytics)


@features_bp.route("/api/cases/<case_id>", methods=["DELETE"])
@require_auth_or_guest
def delete_case_api(case_id: str):
    if g.auth_type != "user":
        return jsonify({"error": "تسجيل الدخول مطلوب لإدارة القضايا."}), 401
    from api.models import get_case_details as db_get_case_details, delete_case as db_delete_case
    case = db_get_case_details(case_id)
    if not case:
        return jsonify({"error": "القضية غير موجودة."}), 404
    if case["user_id"] != g.user_id:
        return jsonify({"error": "غير مصرح لك."}), 403
        
    db_delete_case(case_id)
    return jsonify({"message": "تم حذف القضية بنجاح!"})


@features_bp.route("/api/explain-simple", methods=["POST"])
def explain_simple():
    """Explain a complex legal text in simple/casual Arabic."""
    data = request.get_json() or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "النص فارغ."}), 400
        
    generate_text = _services.get("generate_text")
    if not generate_text:
        return jsonify({"error": "خدمة صياغة النصوص غير متوفرة."}), 500
        
    system_prompt = (
        "أنت مستشار قانوني مصري شاطر يبسط القانون للناس العاديين. "
        "مهمتك إعادة كتابة وتفسير النص القانوني المعقد المرفق بلغة عربية بسيطة ومفهومة جداً (لهجة مصرية مهذبة أو فصحى مبسطة للغاية) "
        "مع شرح المفاهيم القانونية الصعبة باختصار ووضوح تام، دون أي مصطلحات معقدة."
    )
    prompt = f"اشرح النص القانوني التالي بلغة بسيطة وسهلة:\n\n{text[:4000]}"
    try:
        explained = generate_text(prompt, system_prompt).strip()
        return jsonify({
            "explanation": explained,
            "simplified": explained,
            "result": explained
        })
    except Exception as e:
        return jsonify({"error": f"حدث خطأ أثناء التبسيط: {str(e)}"}), 500
