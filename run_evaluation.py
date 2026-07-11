#!/usr/bin/env python3
"""
Qanuni.AI — Offline Evaluation & Quality Metrics Runner (v1.0)
==============================================================
Runs a battery of test scenarios against the Qanuni.AI Flask API
to measure classification accuracy, retrieval latency, RAG answer
quality, and streaming reliability.

Usage:
    python run_evaluation.py [--base-url http://localhost:5000] [--output results.json]
"""

import argparse
import json
import sys
import time
import requests
from datetime import datetime

# Fix Windows terminal encoding
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── Evaluation Test Scenarios ─────────────────────────────────────────

CLASSIFICATION_SCENARIOS = [
    {
        "query": "ما هي عقوبة السرقة في القانون المصري؟",
        "expected_category": "criminal_law",
        "description": "Basic criminal law classification"
    },
    {
        "query": "إجراءات رفع دعوى طلاق للضرر",
        "expected_category": "family_law",
        "description": "Family law / personal status"
    },
    {
        "query": "ما هي شروط تأسيس شركة ذات مسئولية محدودة؟",
        "expected_category": "corporate_law",
        "description": "Corporate / companies law"
    },
    {
        "query": "حقوق العامل المفصول تعسفياً وفقاً لقانون العمل",
        "expected_category": "labor_law",
        "description": "Labor law classification"
    },
    {
        "query": "عقد بيع شقة وإجراءات التسجيل في الشهر العقاري",
        "expected_category": "real_estate_law",
        "description": "Real estate law"
    },
    {
        "query": "ما هي عقوبة إصدار شيك بدون رصيد؟",
        "expected_category": "commercial_law",
        "description": "Commercial law classification"
    },
    {
        "query": "كيف يتم تنفيذ حكم أجنبي في مصر؟",
        "expected_category": "international_private_law",
        "description": "International private law"
    },
    {
        "query": "ما هي ضمانات قانون الاستثمار للمستثمر الأجنبي؟",
        "expected_category": "investment_law",
        "description": "Investment law"
    },
]

RAG_QUALITY_SCENARIOS = [
    {
        "query": "ما هي عقوبة القتل العمد في القانون المصري؟",
        "must_contain_keywords": ["إعدام", "عقوبة", "قانون العقوبات"],
        "must_have_sources": True,
        "description": "Murder penalty - should cite Penal Code"
    },
    {
        "query": "ما هي حقوق المستأجر في قانون الإيجار الجديد؟",
        "must_contain_keywords": ["إيجار", "مستأجر", "عقد"],
        "must_have_sources": True,
        "description": "Tenant rights - should cite rent law"
    },
    {
        "query": "شروط الخلع في القانون المصري",
        "must_contain_keywords": ["خلع", "زوجة", "مهر"],
        "must_have_sources": True,
        "description": "Khul divorce conditions"
    },
]


def run_classification_eval(base_url, headers):
    """Test classification accuracy on labeled scenarios."""
    print("\n" + "=" * 60)
    print("📊 Phase 1: Classification Accuracy Evaluation")
    print("=" * 60)

    results = []
    correct = 0
    total = len(CLASSIFICATION_SCENARIOS)

    for i, scenario in enumerate(CLASSIFICATION_SCENARIOS, 1):
        print(f"\n  [{i}/{total}] {scenario['description']}")
        print(f"    Query: {scenario['query'][:60]}...")

        import uuid
        start_time = time.time()
        try:
            res = requests.post(
                f"{base_url}/api/chat",
                json={
                    "message": scenario["query"],
                    "web_search": False,
                },
                headers={
                    "Content-Type": "application/json",
                    "X-Guest-ID": f"eval_class_{uuid.uuid4()}"
                },
                timeout=120,
            )
            latency_ms = (time.time() - start_time) * 1000

            if res.status_code == 200:
                data = res.json()
                classification_val = data.get("classification", "unknown")
                actual_category = classification_val if isinstance(classification_val, str) else classification_val.get("category", "unknown")
                expected = scenario["expected_category"]

                is_correct = actual_category == expected or expected in actual_category
                if is_correct:
                    correct += 1
                    status = "✅ PASS"
                else:
                    status = "❌ FAIL"

                print(f"    Expected: {expected} | Got: {actual_category} → {status}")
                print(f"    Latency: {latency_ms:.0f}ms")

                results.append({
                    "scenario": scenario["description"],
                    "expected": expected,
                    "actual": actual_category,
                    "correct": is_correct,
                    "latency_ms": round(latency_ms, 1),
                })
            else:
                print(f"    ⚠️ HTTP {res.status_code}: {res.text[:100]}")
                results.append({
                    "scenario": scenario["description"],
                    "expected": scenario["expected_category"],
                    "actual": f"HTTP_{res.status_code}",
                    "correct": False,
                    "latency_ms": round(latency_ms, 1),
                })
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            print(f"    ❌ ERROR: {e}")
            results.append({
                "scenario": scenario["description"],
                "expected": scenario["expected_category"],
                "actual": str(e),
                "correct": False,
                "latency_ms": round(latency_ms, 1),
            })

    accuracy = (correct / total * 100) if total > 0 else 0
    avg_latency = sum(r["latency_ms"] for r in results) / len(results) if results else 0

    print(f"\n  ── Classification Summary ──")
    print(f"    Accuracy: {correct}/{total} ({accuracy:.1f}%)")
    print(f"    Avg Latency: {avg_latency:.0f}ms")

    return {
        "phase": "classification",
        "accuracy_pct": round(accuracy, 1),
        "correct": correct,
        "total": total,
        "avg_latency_ms": round(avg_latency, 1),
        "details": results,
    }


def run_rag_quality_eval(base_url, headers):
    """Test RAG answer quality: keyword presence, source citation, latency."""
    print("\n" + "=" * 60)
    print("📚 Phase 2: RAG Answer Quality Evaluation")
    print("=" * 60)

    results = []
    total = len(RAG_QUALITY_SCENARIOS)

    for i, scenario in enumerate(RAG_QUALITY_SCENARIOS, 1):
        print(f"\n  [{i}/{total}] {scenario['description']}")
        print(f"    Query: {scenario['query'][:60]}...")

        import uuid
        start_time = time.time()
        try:
            res = requests.post(
                f"{base_url}/api/chat",
                json={
                    "message": scenario["query"],
                    "web_search": False,
                },
                headers={
                    "Content-Type": "application/json",
                    "X-Guest-ID": f"eval_rag_{uuid.uuid4()}"
                },
                timeout=120,
            )
            latency_ms = (time.time() - start_time) * 1000

            if res.status_code == 200:
                data = res.json()
                answer = data.get("answer", "")
                sources = data.get("sources", [])

                # Check keyword presence
                keywords_found = []
                keywords_missing = []
                for kw in scenario.get("must_contain_keywords", []):
                    if kw in answer:
                        keywords_found.append(kw)
                    else:
                        keywords_missing.append(kw)

                kw_score = len(keywords_found) / max(len(scenario.get("must_contain_keywords", [1])), 1)

                # Check sources
                has_sources = len(sources) > 0
                source_pass = not scenario.get("must_have_sources") or has_sources

                print(f"    Keywords hit: {len(keywords_found)}/{len(scenario.get('must_contain_keywords', []))} → {'✅' if kw_score >= 0.5 else '⚠️'}")
                if keywords_missing:
                    print(f"    Missing: {', '.join(keywords_missing)}")
                print(f"    Sources: {len(sources)} → {'✅' if source_pass else '❌'}")
                print(f"    Answer length: {len(answer)} chars | Latency: {latency_ms:.0f}ms")

                results.append({
                    "scenario": scenario["description"],
                    "keyword_score": round(kw_score, 2),
                    "keywords_found": keywords_found,
                    "keywords_missing": keywords_missing,
                    "source_count": len(sources),
                    "source_pass": source_pass,
                    "answer_length": len(answer),
                    "latency_ms": round(latency_ms, 1),
                })
            else:
                print(f"    ⚠️ HTTP {res.status_code}")
                results.append({
                    "scenario": scenario["description"],
                    "keyword_score": 0,
                    "source_count": 0,
                    "source_pass": False,
                    "answer_length": 0,
                    "latency_ms": round(latency_ms, 1),
                    "error": f"HTTP_{res.status_code}",
                })
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            print(f"    ❌ ERROR: {e}")
            results.append({
                "scenario": scenario["description"],
                "keyword_score": 0,
                "source_count": 0,
                "source_pass": False,
                "answer_length": 0,
                "latency_ms": round(latency_ms, 1),
                "error": str(e),
            })

    avg_kw = sum(r["keyword_score"] for r in results) / len(results) if results else 0
    avg_latency = sum(r["latency_ms"] for r in results) / len(results) if results else 0
    source_pass_rate = sum(1 for r in results if r["source_pass"]) / len(results) * 100 if results else 0

    print(f"\n  ── RAG Quality Summary ──")
    print(f"    Avg Keyword Score: {avg_kw:.0%}")
    print(f"    Source Citation Rate: {source_pass_rate:.0f}%")
    print(f"    Avg Latency: {avg_latency:.0f}ms")

    return {
        "phase": "rag_quality",
        "avg_keyword_score": round(avg_kw, 2),
        "source_citation_rate_pct": round(source_pass_rate, 1),
        "avg_latency_ms": round(avg_latency, 1),
        "details": results,
    }


def run_streaming_eval(base_url, headers):
    """Test SSE streaming endpoint for reliability and token delivery."""
    print("\n" + "=" * 60)
    print("🌊 Phase 3: Streaming Reliability Evaluation")
    print("=" * 60)

    test_query = "ما هي أركان عقد البيع في القانون المدني المصري؟"
    print(f"\n  Query: {test_query[:60]}...")

    start_time = time.time()
    tokens_received = 0
    events_received = 0
    got_done = False
    got_sources = False
    first_token_ms = None

    import uuid
    try:
        res = requests.post(
            f"{base_url}/api/chat/stream",
            json={
                "message": test_query,
                "web_search": False,
            },
            headers={
                "Content-Type": "application/json",
                "X-Guest-ID": f"eval_stream_{uuid.uuid4()}"
            },
            stream=True,
            timeout=120,
        )

        if res.status_code == 200:
            for line in res.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue

                events_received += 1
                try:
                    event = json.loads(line[5:].strip())
                    event_type = event.get("type", "")

                    if event_type == "token":
                        tokens_received += 1
                        if first_token_ms is None:
                            first_token_ms = (time.time() - start_time) * 1000
                    elif event_type == "sources":
                        got_sources = True
                    elif event_type == "done":
                        got_done = True
                except json.JSONDecodeError:
                    pass

            total_ms = (time.time() - start_time) * 1000

            print(f"    Total events: {events_received}")
            print(f"    Tokens received: {tokens_received}")
            print(f"    First token latency: {first_token_ms:.0f}ms" if first_token_ms else "    First token: N/A")
            print(f"    Got sources event: {'✅' if got_sources else '❌'}")
            print(f"    Got done event: {'✅' if got_done else '❌'}")
            print(f"    Total time: {total_ms:.0f}ms")

            return {
                "phase": "streaming",
                "events_received": events_received,
                "tokens_received": tokens_received,
                "first_token_ms": round(first_token_ms, 1) if first_token_ms else None,
                "total_ms": round(total_ms, 1),
                "got_sources": got_sources,
                "got_done": got_done,
                "pass": got_done and tokens_received > 0,
            }
        else:
            print(f"    ⚠️ HTTP {res.status_code}")
            return {
                "phase": "streaming",
                "pass": False,
                "error": f"HTTP_{res.status_code}",
            }
    except Exception as e:
        print(f"    ❌ ERROR: {e}")
        return {
            "phase": "streaming",
            "pass": False,
            "error": str(e),
        }


def run_endpoint_health_checks(base_url, headers):
    """Verify all critical API endpoints respond correctly."""
    print("\n" + "=" * 60)
    print("🏥 Phase 4: Endpoint Health Checks")
    print("=" * 60)

    endpoints = [
        ("GET", "/api/glossary", None, "Legal glossary"),
        ("POST", "/api/explain-simple", {"text": "الإعدام هو عقوبة يحكم بها القاضي"}, "Simplify text"),
    ]

    results = []
    for method, path, body, desc in endpoints:
        url = f"{base_url}{path}"
        print(f"\n  [{method}] {path} — {desc}")

        start_time = time.time()
        try:
            if method == "GET":
                res = requests.get(url, headers=headers, timeout=30)
            else:
                res = requests.post(url, json=body, headers={**headers, "Content-Type": "application/json"}, timeout=30)
            
            latency_ms = (time.time() - start_time) * 1000
            is_ok = 200 <= res.status_code < 400
            print(f"    Status: {res.status_code} → {'✅' if is_ok else '❌'} | Latency: {latency_ms:.0f}ms")

            results.append({
                "endpoint": path,
                "method": method,
                "description": desc,
                "status_code": res.status_code,
                "ok": is_ok,
                "latency_ms": round(latency_ms, 1),
            })
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            print(f"    ❌ ERROR: {e}")
            results.append({
                "endpoint": path,
                "method": method,
                "description": desc,
                "status_code": 0,
                "ok": False,
                "latency_ms": round(latency_ms, 1),
                "error": str(e),
            })

    pass_count = sum(1 for r in results if r["ok"])
    print(f"\n  ── Health Check Summary ──")
    print(f"    Passed: {pass_count}/{len(results)}")

    return {
        "phase": "health_checks",
        "passed": pass_count,
        "total": len(results),
        "details": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Qanuni.AI Evaluation Runner")
    parser.add_argument("--base-url", default="http://localhost:5000", help="Base URL of the running Flask API")
    parser.add_argument("--output", default="evaluation_results.json", help="Output JSON file for results")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    headers = {"Content-Type": "application/json"}

    print("╔══════════════════════════════════════════════════════════╗")
    print("║       Qanuni.AI — Comprehensive Evaluation Suite       ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"\n  Target: {base_url}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Verify server is reachable
    try:
        ping = requests.get(f"{base_url}/", timeout=10)
        print(f"  Server Status: {'🟢 Online' if ping.status_code == 200 else '🟡 Reachable but non-200'}")
    except Exception as e:
        print(f"  ❌ Server unreachable at {base_url}: {e}")
        print("  Please start the server first with: python app.py")
        sys.exit(1)

    all_results = {
        "evaluation_date": datetime.now().isoformat(),
        "target_url": base_url,
        "phases": [],
    }

    # Phase 1: Classification
    classification_results = run_classification_eval(base_url, headers)
    all_results["phases"].append(classification_results)

    # Phase 2: RAG Quality
    rag_results = run_rag_quality_eval(base_url, headers)
    all_results["phases"].append(rag_results)

    # Phase 3: Streaming
    streaming_results = run_streaming_eval(base_url, headers)
    all_results["phases"].append(streaming_results)

    # Phase 4: Health Checks
    health_results = run_endpoint_health_checks(base_url, headers)
    all_results["phases"].append(health_results)

    # ── Final Summary ──
    print("\n" + "=" * 60)
    print("📋 FINAL EVALUATION SUMMARY")
    print("=" * 60)
    print(f"  Classification Accuracy: {classification_results.get('accuracy_pct', 0):.1f}%")
    print(f"  RAG Keyword Score:       {rag_results.get('avg_keyword_score', 0):.0%}")
    print(f"  Source Citation Rate:    {rag_results.get('source_citation_rate_pct', 0):.1f}%")
    print(f"  Streaming Reliable:      {'✅ Yes' if streaming_results.get('pass') else '❌ No'}")
    print(f"  Health Checks:           {health_results.get('passed', 0)}/{health_results.get('total', 0)}")
    print(f"  Avg Retrieval Latency:   {rag_results.get('avg_latency_ms', 0):.0f}ms")

    # Save results
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n  Results saved to: {args.output}")
    print("  Done! 🎉")


if __name__ == "__main__":
    main()
