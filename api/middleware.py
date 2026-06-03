"""
Qanuni.AI — Middleware
Rate limiting, guest limits, auth decorator, input sanitization.
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


# Rate limiter (in-memory)
_rates: dict[str, deque] = {}
_rate_lock = threading.Lock()


def _check_rate(ip: str, max_per_minute: int = RATE_LIMIT_PER_MINUTE) -> bool:
    """Check if IP is within rate limit. Returns True if allowed."""
    now = time.time()
    with _rate_lock:
        if ip not in _rates:
            _rates[ip] = deque()

        # Remove old entries
        while _rates[ip] and _rates[ip][0] < now - 60:
            _rates[ip].popleft()

        if len(_rates[ip]) >= max_per_minute:
            return False

        _rates[ip].append(now)

        # Periodic cleanup of stale IPs (every 200 IPs to avoid unbounded growth)
        if len(_rates) > 200:
            stale = [k for k, v in _rates.items() if not v or v[-1] < now - 300]
            for k in stale:
                del _rates[k]

        return True


def rate_limit(max_per_minute: int = RATE_LIMIT_PER_MINUTE):
    """Decorator: rate limit by client IP."""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip = request.remote_addr or "unknown"
            if not _check_rate(ip, max_per_minute):
                return jsonify({
                    "error": "تم تجاوز الحد المسموح من الطلبات. يرجى الانتظار دقيقة.",
                    "code": "RATE_LIMITED",
                }), 429
            return f(*args, **kwargs)
        return wrapped
    return decorator


def check_guest_limit(guest_id: str) -> tuple[bool, int]:
    """Check if guest has remaining messages. Returns (allowed, remaining)."""
    usage = get_guest_usage(guest_id)
    count = usage["message_count"]
    remaining = max(0, GUEST_MESSAGE_LIMIT - count)
    return (count < GUEST_MESSAGE_LIMIT, remaining)


def require_auth_or_guest(f):
    """Accept either JWT token or guest ID for auth."""
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
            data = request.get_json(silent=True) or {}
            guest_id = data.get("guest_id", "").strip()

        if not guest_id:
            return jsonify({
                "error": "يرجى تسجيل الدخول أو استخدام الوضع التجريبي.",
                "code": "AUTH_REQUIRED",
            }), 401

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
    """Check guest message limit. Returns (allowed, remaining, error_response)."""
    if g.auth_type == "user":
        return True, -1, None

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
    """Increment guest message count after successful response."""
    if g.auth_type == "guest" and g.guest_id:
        new_count = increment_guest_usage(g.guest_id)
        return max(0, GUEST_MESSAGE_LIMIT - new_count)
    return -1


def sanitize_input(text: str) -> tuple[str, bool]:
    """Clean and validate user input. Returns (cleaned_text, has_injection)."""
    if not text:
        return "", False

    text = text.strip()
    if len(text) > MAX_INPUT_LENGTH:
        text = text[:MAX_INPUT_LENGTH]

    has_injection = False
    text_lower = text.lower()
    for pattern in PROMPT_INJECTION_PATTERNS:
        if re.search(pattern, text_lower, re.IGNORECASE):
            has_injection = True
            break

    return text, has_injection
