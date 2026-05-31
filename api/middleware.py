"""
Qanuni.AI — Auth & Security Middleware
=======================================
- Guest message limit enforcement (server-side)
- JWT + guest hybrid auth decorator
- Lightweight in-memory rate limiter
- Input sanitization + prompt injection filter
"""

import re
import time
import html
import threading
from functools import wraps
from collections import deque

from flask import request, jsonify, g
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity

from config import (
    GUEST_MESSAGE_LIMIT,
    RATE_LIMIT_PER_MINUTE,
    MAX_INPUT_LENGTH,
    PROMPT_INJECTION_PATTERNS,
)
from api.models import get_guest_usage, increment_guest_usage


# ── Rate Limiter (in-memory, lightweight) ────────────────────────────

_rate_store: dict[str, deque] = {}
_rate_lock = threading.Lock()


def _check_rate_limit(ip: str, max_per_minute: int = RATE_LIMIT_PER_MINUTE) -> bool:
    """Check if IP has exceeded rate limit. Returns True if allowed."""
    now = time.time()
    with _rate_lock:
        if ip not in _rate_store:
            _rate_store[ip] = deque()

        # Remove entries older than 60 seconds
        while _rate_store[ip] and _rate_store[ip][0] < now - 60:
            _rate_store[ip].popleft()

        if len(_rate_store[ip]) >= max_per_minute:
            return False

        _rate_store[ip].append(now)

        # Cleanup: remove IPs with no recent activity (prevent memory leak)
        if len(_rate_store) > 1000:
            stale = [k for k, v in _rate_store.items() if not v or v[-1] < now - 300]
            for k in stale:
                del _rate_store[k]

        return True


def rate_limit(max_per_minute: int = RATE_LIMIT_PER_MINUTE):
    """Decorator: rate limit by client IP."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip = request.remote_addr or "unknown"
            if not _check_rate_limit(ip, max_per_minute):
                return jsonify({
                    "error": "تم تجاوز الحد المسموح من الطلبات. يرجى الانتظار دقيقة.",
                    "code": "RATE_LIMITED",
                }), 429
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ── Guest Limit Enforcement ──────────────────────────────────────────

def check_guest_limit(guest_id: str) -> tuple[bool, int]:
    """
    Check if guest has remaining messages.
    Returns (allowed, remaining).
    NEVER trust the frontend — this is the authoritative check.
    """
    usage = get_guest_usage(guest_id)
    count = usage["message_count"]
    remaining = max(0, GUEST_MESSAGE_LIMIT - count)
    return (count < GUEST_MESSAGE_LIMIT, remaining)


# ── Auth/Guest Hybrid Decorator ─────────────────────────────────────

def require_auth_or_guest(f):
    """
    Flexible auth decorator that accepts either:
    1. JWT token in Authorization header → sets g.user_id
    2. X-Guest-ID header → sets g.guest_id (with limit enforcement)

    Sets g.auth_type = 'user' | 'guest'
    """
    @wraps(f)
    def wrapped(*args, **kwargs):
        g.user_id = None
        g.guest_id = None
        g.auth_type = None

        # Try JWT first
        try:
            verify_jwt_in_request()
            user_id = get_jwt_identity()
            if user_id:
                g.user_id = int(user_id)
                g.auth_type = "user"
                return f(*args, **kwargs)
        except Exception:
            pass

        # Fall back to guest
        guest_id = request.headers.get("X-Guest-ID", "").strip()
        if not guest_id:
            # Also check JSON body
            data = request.get_json(silent=True) or {}
            guest_id = data.get("guest_id", "").strip()

        if not guest_id:
            return jsonify({
                "error": "يرجى تسجيل الدخول أو استخدام الوضع التجريبي.",
                "code": "AUTH_REQUIRED",
            }), 401

        # Validate guest_id format (prevent injection)
        if not re.match(r'^[a-zA-Z0-9_-]{8,64}$', guest_id):
            return jsonify({
                "error": "معرف الجلسة غير صالح.",
                "code": "INVALID_GUEST_ID",
            }), 400

        g.guest_id = guest_id
        g.auth_type = "guest"
        return f(*args, **kwargs)

    return wrapped


def enforce_guest_limit():
    """
    Call this INSIDE a route after require_auth_or_guest.
    For guests, checks and enforces message limit.
    Returns (allowed, remaining, error_response).
    If not allowed, returns a JSON error response to send back.
    """
    if g.auth_type == "user":
        return True, -1, None  # Unlimited for registered users

    allowed, remaining = check_guest_limit(g.guest_id)
    if not allowed:
        return False, 0, jsonify({
            "error": "لقد استنفدت رسائلك المجانية. سجل حساب للمتابعة.",
            "code": "GUEST_LIMIT_REACHED",
            "remaining": 0,
            "limit": GUEST_MESSAGE_LIMIT,
        }), 403

    return True, remaining, None


def increment_guest_count():
    """Call after a successful guest message to increment their count."""
    if g.auth_type == "guest" and g.guest_id:
        new_count = increment_guest_usage(g.guest_id)
        return max(0, GUEST_MESSAGE_LIMIT - new_count)
    return -1  # unlimited for users


# ── Input Sanitization ───────────────────────────────────────────────

def sanitize_input(text: str) -> tuple[str, bool]:
    """
    Sanitize user input text.
    Returns (cleaned_text, has_injection).
    """
    if not text:
        return "", False

    # Trim and truncate
    text = text.strip()
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]

    # Check for prompt injection patterns
    has_injection = False
    text_lower = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            has_injection = True
            break

    # HTML-escape to prevent XSS (even though we escape on display too)
    text = html.escape(text)

    return text, has_injection
