"""
Qanuni.AI — Auth API Endpoints
================================
Lightweight authentication: signup, login, JWT, guest sessions.
Includes guest-to-user conversation migration on signup.
"""

import re
import time

import bcrypt
from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token

from config import GUEST_MESSAGE_LIMIT
from api.models import (
    create_user,
    get_user_by_email,
    get_user_by_id,
    update_last_login,
    migrate_guest_to_user,
    get_guest_usage,
)
from api.middleware import rate_limit

auth_bp = Blueprint("auth", __name__)


def _validate_email(email: str) -> bool:
    """Basic email format validation."""
    return bool(re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email))


def _hash_password(password: str) -> str:
    """Hash password with bcrypt."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def _check_password(password: str, hashed: str) -> bool:
    """Verify password against bcrypt hash."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


@auth_bp.route("/api/auth/signup", methods=["POST"])
@rate_limit(max_per_minute=5)
def signup():
    """
    Register a new user account.
    If guest_id is provided, migrates guest conversation history to the new account.
    """
    data = request.get_json(force=True)
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    name = data.get("name", "").strip()
    guest_id = data.get("guest_id", "").strip()

    if not email or not password:
        return jsonify({"error": "البريد الإلكتروني وكلمة المرور مطلوبان."}), 400

    # Specific condition/bypass for the "test" account
    is_test_account = (email.lower() == "test")

    if not is_test_account and not _validate_email(email):
        return jsonify({"error": "صيغة البريد الإلكتروني غير صحيحة."}), 400

    if not is_test_account and len(password) < 6:
        return jsonify({"error": "كلمة المرور يجب أن تكون 6 أحرف على الأقل."}), 400

    if len(password) > 128:
        return jsonify({"error": "كلمة المرور طويلة جداً."}), 400

    # Create user
    password_hash = _hash_password(password)
    user_id = create_user(email, password_hash, name)

    if user_id is None:
        return jsonify({"error": "هذا البريد الإلكتروني مسجل بالفعل."}), 409

    # Migrate guest conversations to new account
    migrated = 0
    if guest_id:
        migrated = migrate_guest_to_user(guest_id, user_id)

    # Issue JWT
    token = create_access_token(identity=str(user_id))
    update_last_login(user_id)

    return jsonify({
        "token": token,
        "user": {
            "id": user_id,
            "email": email,
            "name": name,
        },
        "migrated_conversations": migrated,
        "message": "تم إنشاء الحساب بنجاح!",
    }), 201


@auth_bp.route("/api/auth/login", methods=["POST"])
@rate_limit(max_per_minute=10)
def login():
    """Authenticate user and return JWT."""
    data = request.get_json(force=True)
    email = data.get("email", "").strip()
    password = data.get("password", "").strip()
    guest_id = data.get("guest_id", "").strip()

    if not email or not password:
        return jsonify({"error": "البريد الإلكتروني وكلمة المرور مطلوبان."}), 400

    user = get_user_by_email(email)
    if not user:
        return jsonify({"error": "بيانات الدخول غير صحيحة."}), 401

    if not _check_password(password, user["password"]):
        return jsonify({"error": "بيانات الدخول غير صحيحة."}), 401

    # Migrate guest conversations on login too
    migrated = 0
    if guest_id:
        migrated = migrate_guest_to_user(guest_id, user["id"])

    # Issue JWT
    token = create_access_token(identity=str(user["id"]))
    update_last_login(user["id"])

    return jsonify({
        "token": token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
        },
        "migrated_conversations": migrated,
    })


@auth_bp.route("/api/auth/me", methods=["GET"])
def me():
    """Get current user profile from JWT."""
    from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity

    try:
        verify_jwt_in_request()
        user_id = int(get_jwt_identity())
    except Exception:
        return jsonify({"error": "غير مصرح."}), 401

    user = get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "المستخدم غير موجود."}), 404

    return jsonify({
        "user": {
            "id": user["id"],
            "email": user["email"],
            "name": user["name"],
            "created_at": user["created_at"],
        }
    })


@auth_bp.route("/api/auth/guest", methods=["POST"])
@rate_limit(max_per_minute=20)
def guest_status():
    """Check guest usage status. Returns remaining messages."""
    data = request.get_json(force=True)
    guest_id = data.get("guest_id", "").strip()

    if not guest_id:
        return jsonify({"error": "معرف الضيف مطلوب."}), 400

    usage = get_guest_usage(guest_id)
    remaining = max(0, GUEST_MESSAGE_LIMIT - usage["message_count"])

    return jsonify({
        "guest_id": guest_id,
        "message_count": usage["message_count"],
        "remaining": remaining,
        "limit": GUEST_MESSAGE_LIMIT,
    })
