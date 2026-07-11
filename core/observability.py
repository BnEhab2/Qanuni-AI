"""
Qanuni.AI — Observability & Metrics
===================================
Structured logging, tracing context managers, and Prometheus-compatible metrics.
"""

import time
import logging
from functools import wraps
from flask import request, Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# Logger Setup
logger = logging.getLogger("qanuni_observability")
logger.setLevel(logging.INFO)

# Prometheus metrics setup
REQUEST_COUNT = Counter(
    "qanuni_http_requests_total",
    "Total HTTP Requests",
    ["method", "endpoint", "status"]
)

REQUEST_LATENCY = Histogram(
    "qanuni_http_request_duration_seconds",
    "HTTP Request Latency in seconds",
    ["method", "endpoint"]
)

RAG_PIPELINE_LATENCY = Histogram(
    "qanuni_rag_pipeline_stage_duration_seconds",
    "RAG Pipeline individual stage latency in seconds",
    ["stage"]
)

LLM_ERROR_COUNT = Counter(
    "qanuni_llm_errors_total",
    "Total count of LLM API errors",
    ["model"]
)

# Tracing context helper
class RAGTrace:
    """Context manager for tracing the execution time of RAG pipeline stages."""
    def __init__(self, stage_name: str):
        self.stage_name = stage_name
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.time() - self.start_time
        RAG_PIPELINE_LATENCY.labels(stage=self.stage_name).observe(duration)
        logger.info(f"[Trace] Stage '{self.stage_name}' completed in {duration:.4f}s")


# Flask Middleware instrumentation helper
def setup_observability(app):
    """Register request hooks to track metrics in Flask."""

    @app.before_request
    def before_request():
        request.start_time = time.time()

    @app.after_request
    def after_request(response):
        if hasattr(request, "start_time"):
            duration = time.time() - request.start_time
            endpoint = request.endpoint or "static"
            REQUEST_LATENCY.labels(method=request.method, endpoint=endpoint).observe(duration)
            REQUEST_COUNT.labels(method=request.method, endpoint=endpoint, status=response.status_code).inc()
        return response

    @app.route("/metrics")
    def metrics():
        """Prometheus metrics endpoint."""
        return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
