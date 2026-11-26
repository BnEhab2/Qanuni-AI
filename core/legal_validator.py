"""Conservative legal currency checks backed by the existing DuckDuckGo search."""
from __future__ import annotations

import hashlib
import re
import threading
import time
from datetime import date
from api.models import DBConnection, IS_POSTGRES
from core.web_search import _ddg_search

CACHE_TTL_SECONDS = 24 * 60 * 60
_inflight: dict[str, threading.Event] = {}
_inflight_lock = threading.Lock()

LAW_RE = re.compile(r"(?:قانون\s*(?:رقم)?\s*|law\s*(?:no\.?|number)?\s*)(\d{1,4})\s*(?:لسنة|of|/)\s*(\d{4})", re.I)
ARTICLE_RE = re.compile(r"(?:المادة|مادة|article)\s*(?:رقم\s*)?(\d+[مكرر]*)", re.I)
ENACTED = re.compile(r"(صدر|نشر في الجريدة الرسمية|قانون رقم|promulgated|official gazette|enacted)", re.I)
PROPOSED = re.compile(r"(مشروع قانون|مقترح|اقتراح قانون|draft bill|proposed amendment)", re.I)


def _is_current_law(item: dict) -> bool:
    status = item.get("version_status") or item.get("legal_status")
    return status not in {"repealed", "superseded"} and not item.get("outdated", False)


def _law_identity(item: dict) -> tuple[str, str, str]:
    text = " ".join(str(item.get(k, "")) for k in ("law_name", "title", "text", "citation"))
    match = LAW_RE.search(text)
    law_no = str(item.get("law_number") or (match.group(1) if match else ""))
    year = str(item.get("law_year") or (match.group(2) if match else ""))
    article = str(item.get("article_number") or "")
    if not article or article in {"ويب", "web"}:
        article_match = ARTICLE_RE.search(text)
        article = article_match.group(1) if article_match else ""
    return law_no, year, article


def _cached(law_no: str, year: str, article: str) -> dict | None:
    if not law_no or not year or not article:
        return None
    with DBConnection() as db:
        row = db.fetchone("""SELECT v.*, d.law_number, d.law_year, d.title FROM legal_versions v
            JOIN legal_documents d ON d.id=v.document_id WHERE d.law_number=? AND d.law_year=?
            AND v.article_number=? ORDER BY v.verified_at DESC LIMIT 1""", (law_no, year, article))
    if row and row.get("verified_at") and time.time() - float(row["verified_at"]) < CACHE_TTL_SECONDS:
        return {"status": row["version_status"], "source_urls": [row.get("source_url", "")] if row.get("source_url") else [],
                "verified_at": row["verified_at"], "publication_date": row.get("publication_date"),
                "effective_date": row.get("effective_date"), "law_number": law_no, "law_year": year,
                "article_number": article, "evidence": "cached verification"}
    return None


def _search_queries(law_no: str, year: str, article: str, subject: str) -> list[str]:
    base = f'"قانون رقم {law_no} لسنة {year}"'
    article_part = f' "المادة {article}"' if article else ""
    subject = re.sub(r"\s+", " ", subject)[:100]
    queries = [f"site:manshurat.org {base}{article_part} تعديل إلغاء ساري",
               f"site:legislation.gov.eg OR site:cc.gov.eg OR site:scc.gov.eg {base}{article_part} تعديل إلغاء {subject}",
               f"مصر تشريع أحدث {subject or base} تعديل إلغاء"]
    return queries


def _classify(results: list[dict], law_no: str, year: str, article: str) -> tuple[str, list[dict]]:
    matching = []
    for result in results:
        combined = " ".join(str(result.get(k, "")) for k in ("title", "body", "href"))
        # Never infer legal effect from a generic result; exact identifier plus enactment cue is required.
        if law_no in combined and year in combined and (not article or article in combined):
            matching.append(result)
    if not matching:
        return "unverified", []
    texts = [str(r.get("title", "")) + " " + str(r.get("body", "")) for r in matching]
    if any(PROPOSED.search(t) for t in texts) and not any(ENACTED.search(t) for t in texts):
        return "unverified", matching
    # Search snippets are discovery evidence only; they do not prove operative legal text.
    return "unverified", matching


def verify_law(item: dict, question: str = "", force: bool = False) -> dict:
    law_no, year, article = _law_identity(item)
    if not law_no or not year:
        result = {"status": "unverified", "reason": "law identifier not confidently extractable", "source_urls": [], "verified_at": time.time()}
        item.update(legal_status=result["status"], legal_verification=result)
        return result
    key = f"{law_no}:{year}:{article}"
    if not force:
        cached = _cached(law_no, year, article)
        if cached:
            item.update(legal_status=cached["status"], legal_verification=cached)
            return cached
    # Coalesce concurrent requests for the same identifier.
    with _inflight_lock:
        event = _inflight.get(key)
        if event:
            waiter = event
        else:
            waiter = threading.Event()
            _inflight[key] = waiter
    if event:
        waiter.wait(timeout=30)
        cached = _cached(law_no, year, article)
        if cached:
            item.update(legal_status=cached["status"], legal_verification=cached)
            return cached
    try:
        subject = str(item.get("categories") or item.get("law_name") or question)
        all_results = []
        try:
            for query in _search_queries(law_no, year, article, subject):
                all_results.extend(_ddg_search(query, max_results=5))
        except Exception:
            all_results = []
        unique = {r.get("href", ""): r for r in all_results if r.get("href")}
        results = list(unique.values())
        status, matched = _classify(results, law_no, year, article)
        urls = [r.get("href") for r in matched if r.get("href")]
        verified_at = time.time()
        result = {"status": status, "source_urls": urls, "verified_at": verified_at,
                  "law_number": law_no, "law_year": year, "article_number": article,
                  "publication_date": None, "effective_date": None,
                  "evidence": [r.get("title", "") for r in matched],
                  "reason": "Search results identify possible sources, but accessible enacted text and operative dates were not established." if status == "unverified" else ""}
        _persist_verification(item, law_no, year, article, result)
        item.update(legal_status=status, legal_verification=result)
        return result
    except Exception as exc:
        result = {"status": "unverified", "source_urls": [], "verified_at": time.time(), "reason": f"verification search failed: {exc}"}
        item.update(legal_status="unverified", legal_verification=result)
        return result
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
            waiter.set()


def _persist_verification(item: dict, law_no: str, year: str, article: str, result: dict) -> None:
    if not article:
        # Law-level checks cannot safely share an article-specific cache/version record.
        return
    now = time.time()
    with DBConnection() as db:
        db.execute("INSERT INTO legal_documents (law_number,law_year,title,subject) VALUES (?,?,?,?) ON CONFLICT(law_number,law_year) DO NOTHING",
                   (law_no, year, str(item.get("law_name", "")), str(item.get("categories", ""))))
        doc = db.fetchone("SELECT id FROM legal_documents WHERE law_number=? AND law_year=?", (law_no, year))
        if not doc:
            return
        # Verification is appended as a historical record, never overwriting legal text.
        sql = "INSERT INTO legal_versions (document_id,article_number,text,version_status,publication_date,effective_date,source_url,verified_at,created_at) VALUES (?,?,?,?,?,?,?,?,?)"
        db.execute(sql, (doc["id"], article, str(item.get("text", "")), result["status"], None, None,
                         (result.get("source_urls") or [""])[0], result["verified_at"], now))


def validate_retrieved_laws(results: list[dict], question: str = "", force: bool = False) -> list[dict]:
    requested_year = re.search(r"\b((?:19|20)\d{2})\b|سنة\s+((?:19|20)\d{2})", question, re.I)
    historical = bool(requested_year or re.search(r"في ذلك الوقت|وقت الواقعة|تاريخ الواقعة", question, re.I))
    for item in results:
        if item.get("source") == "web_search" or not _is_current_law(item):
            continue
        verification = verify_law(item, question=question, force=force)
        # Explicit guard supplied to downstream reasoning and generation.
        item["legal_status"] = verification["status"]
        if historical:
            item["historical_question"] = True
            law_no, year, article = _law_identity(item)
            requested = (requested_year.group(1) or requested_year.group(2)) if requested_year else ""
            item["historical_applicability"] = resolve_applicable_version(law_no, year, article, requested) if requested else None
            if not item["historical_applicability"]:
                item["historical_applicability"] = "unverified"
    return results


def resolve_applicable_version(law_number: str, law_year: str, article_number: str, event_year: str) -> dict | None:
    """Resolve an archived article only when stored effective boundaries establish its period."""
    if not all((law_number, law_year, event_year)):
        return None
    try:
        year = int(event_year)
        period_start = date(year, 1, 1)
        period_end = date(year, 12, 31)
    except (TypeError, ValueError):
        return None
    try:
        with DBConnection() as db:
            rows = db.fetchall("""SELECT v.* FROM legal_versions v JOIN legal_documents d ON d.id=v.document_id
                WHERE d.law_number=? AND d.law_year=? AND v.article_number=? ORDER BY v.created_at DESC""",
                               (law_number, law_year, article_number))
    except Exception:
        return None
    matched = []
    for row in rows:
        start = row.get("effective_date") or row.get("valid_from")
        end = row.get("valid_until")
        try:
            start_date = date.fromisoformat(str(start)[:10]) if start else None
            end_date = date.fromisoformat(str(end)[:10]) if end else None
        except ValueError:
            continue
        if start_date and start_date <= period_start and (not end_date or end_date >= period_end):
            matched.append(row)
    # Overlapping periods mean transitional rules or conflicting records need review.
    if len(matched) != 1:
        return None
    row = matched[0]
    return {"status": row["version_status"], "text": row["text"], "valid_from": row.get("valid_from"),
            "valid_until": row.get("valid_until"), "publication_date": row.get("publication_date"),
            "effective_date": row.get("effective_date"), "source_url": row.get("source_url", ""),
            "transition_review_required": True}


def discover_updates() -> int:
    """Periodic DDG discovery; every finding is pending until reviewed."""
    current_year = date.today().year
    queries = [f"site:manshurat.org قانون مصري تعديل قانون {current_year}",
               f"site:manshurat.org إلغاء قانون مصر {current_year}",
               f"site:legislation.gov.eg OR site:cc.gov.eg تشريع جديد تعديل قانون مصر {current_year}"]
    found = []
    for query in queries:
        found.extend(_ddg_search(query, max_results=10))
    inserted = 0
    now = time.time()
    for result in found:
        url = result.get("href", "")
        title, desc = result.get("title", ""), result.get("body", "")
        fingerprint = hashlib.sha256((url + title + desc).encode("utf-8")).hexdigest()
        match = LAW_RE.search(title + " " + desc)
        article_match = ARTICLE_RE.search(title + " " + desc)
        with DBConnection() as db:
            try:
                db.execute("INSERT INTO legal_updates (fingerprint,law_number,law_year,article_number,title,description,source_url,discovered_at,review_status,verification_status) VALUES (?,?,?,?,?,?,?,?,'pending','unverified')",
                           (fingerprint, match.group(1) if match else "", match.group(2) if match else "", article_match.group(1) if article_match else "", title, desc, url, now))
                inserted += 1
            except Exception:
                pass
    return inserted


def approve_update(update_id: int, *, article_text: str, effective_date: str | None = None,
                   publication_date: str | None = None, admin_verified: bool = False,
                   relationship: str = "amended", index_metadata: dict | None = None) -> int:
    """Activate a reviewed update and synchronize only its article in Chroma/BM25.

    Requires an explicit reviewer attestation and the enacted article text. The search
    discovery itself is never enough to activate an update.
    """
    if not admin_verified or not article_text.strip():
        raise ValueError("Admin verification and the enacted article text are required")
    if relationship not in {"amended", "partially_repealed", "repealed", "superseded"}:
        raise ValueError("Unsupported legal relationship")
    with DBConnection() as db:
        update = db.fetchone("SELECT * FROM legal_updates WHERE id=?", (update_id,))
        if not update:
            raise ValueError("Update not found")
        if update.get("review_status") == "approved":
            raise ValueError("Update has already been approved")
        if not update.get("law_number") or not update.get("law_year"):
            raise ValueError("Update is missing an exact law number and year")
        article = str(update.get("article_number") or "")
        db.execute("INSERT INTO legal_documents (law_number,law_year,title) VALUES (?,?,?) ON CONFLICT(law_number,law_year) DO NOTHING",
                   (update["law_number"], update["law_year"], update.get("title", "")))
        doc = db.fetchone("SELECT id FROM legal_documents WHERE law_number=? AND law_year=?", (update["law_number"], update["law_year"]))
        now = time.time()
        status = relationship
        db.execute("UPDATE legal_versions SET version_status='amended',valid_until=? WHERE document_id=? AND article_number=? AND valid_until IS NULL",
                   (effective_date, doc["id"], article))
        db.execute("INSERT INTO legal_versions (document_id,article_number,text,version_status,valid_from,publication_date,effective_date,source_url,verified_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (doc["id"], article, article_text, status, effective_date, publication_date,
                    effective_date, update.get("source_url", ""), now, now))
        version_id = db.cursor.lastrowid if not IS_POSTGRES else None
        if IS_POSTGRES:
            version_row = db.fetchone("SELECT id FROM legal_versions WHERE document_id=? AND article_number=? ORDER BY id DESC LIMIT 1", (doc["id"], article))
            version_id = version_row.get("id") if version_row else None
        db.execute("INSERT INTO legal_amendments (document_id,version_id,relationship,source_url,publication_date,effective_date,details) VALUES (?,?,?,?,?,?,?)",
                   (doc["id"], version_id, relationship, update.get("source_url", ""), publication_date, effective_date, update.get("description", "")))
        db.execute("UPDATE legal_updates SET review_status='approved',verification_status='verified' WHERE id=?", (update_id,))
        metadata = dict(index_metadata or {})
        metadata.update({"law_number": str(update["law_number"]), "law_year": str(update["law_year"]),
                         "article_number": article, "legal_status": status,
                         "valid_from": effective_date or "", "valid_until": ""})
        try:
            from core.retrieval import get_hybrid_retriever
            get_hybrid_retriever().replace_legal_version(update["law_number"], update["law_year"], article, article_text, metadata)
        except Exception:
            # Raising causes DBConnection to roll back; caller can retry after index recovery.
            raise
        return int(version_id or 0)


def start_update_checker(interval_seconds: int = 6 * 60 * 60) -> None:
    """Start one daemon worker; safe to call from app initialization."""
    if getattr(start_update_checker, "_started", False):
        return
    start_update_checker._started = True
    def worker():
        while True:
            time.sleep(interval_seconds)
            try:
                discover_updates()
            except Exception as exc:
                print(f"[LegalValidator] update scan failed: {exc}")
    threading.Thread(target=worker, name="legal-update-checker", daemon=True).start()
