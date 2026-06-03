"""
Qanuni.AI — Conversations API
CRUD for persistent chat conversations.
"""

from flask import Blueprint, request, jsonify, g

from api.middleware import require_auth_or_guest, rate_limit
from api.models import (
    get_conversations,
    get_conversation,
    get_messages,
    create_conversation,
    update_conversation_title,
    delete_conversation,
    search_conversations,
)

conversations_bp = Blueprint("conversations", __name__)


@conversations_bp.route("/api/conversations", methods=["GET"])
@rate_limit(max_per_minute=30)
@require_auth_or_guest
def list_conversations():
    """List all conversations for the current user/guest."""
    limit = request.args.get("limit", 50, type=int)
    convs = get_conversations(
        user_id=g.user_id,
        guest_id=g.guest_id,
        limit=min(limit, 100),
    )
    return jsonify({"conversations": convs})


@conversations_bp.route("/api/conversations/<conv_id>", methods=["GET"])
@rate_limit(max_per_minute=30)
@require_auth_or_guest
def get_single_conversation(conv_id: str):
    """Get a conversation with all its messages."""
    conv = get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "المحادثة غير موجودة."}), 404

    if g.auth_type == "user":
        if conv["user_id"] != g.user_id:
            return jsonify({"error": "غير مصرح."}), 403
    elif g.auth_type == "guest":
        if conv["guest_id"] != g.guest_id:
            return jsonify({"error": "غير مصرح."}), 403
    else:
        return jsonify({"error": "غير مصرح."}), 403

    messages = get_messages(conv_id)

    return jsonify({
        "conversation": {
            "id": conv["id"],
            "title": conv["title"],
            "mode": conv["mode"],
            "created_at": conv["created_at"],
            "updated_at": conv["updated_at"],
        },
        "messages": messages,
    })


@conversations_bp.route("/api/conversations", methods=["POST"])
@rate_limit(max_per_minute=20)
@require_auth_or_guest
def create_new_conversation():
    """Create a new conversation."""
    data = request.get_json(force=True)
    mode = data.get("mode", "research")

    conv_id = create_conversation(
        user_id=g.user_id,
        guest_id=g.guest_id,
        mode=mode,
    )

    return jsonify({
        "conversation_id": conv_id,
        "title": "محادثة جديدة",
        "mode": mode,
    }), 201


@conversations_bp.route("/api/conversations/<conv_id>/title", methods=["PUT"])
@rate_limit(max_per_minute=20)
@require_auth_or_guest
def update_title(conv_id: str):
    """Update a conversation's title."""
    conv = get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "المحادثة غير موجودة."}), 404

    if g.auth_type == "user":
        if conv["user_id"] != g.user_id:
            return jsonify({"error": "غير مصرح."}), 403
    elif g.auth_type == "guest":
        if conv["guest_id"] != g.guest_id:
            return jsonify({"error": "غير مصرح."}), 403
    else:
        return jsonify({"error": "غير مصرح."}), 403

    data = request.get_json(force=True)
    title = data.get("title", "").strip()[:80]
    if not title:
        return jsonify({"error": "العنوان مطلوب."}), 400

    update_conversation_title(conv_id, title)
    return jsonify({"status": "updated", "title": title})


@conversations_bp.route("/api/conversations/<conv_id>", methods=["DELETE"])
@rate_limit(max_per_minute=20)
@require_auth_or_guest
def delete_conv(conv_id: str):
    """Delete a conversation and all its messages."""
    conv = get_conversation(conv_id)
    if not conv:
        return jsonify({"error": "المحادثة غير موجودة."}), 404

    if g.auth_type == "user":
        if conv["user_id"] != g.user_id:
            return jsonify({"error": "غير مصرح."}), 403
    elif g.auth_type == "guest":
        if conv["guest_id"] != g.guest_id:
            return jsonify({"error": "غير مصرح."}), 403
    else:
        return jsonify({"error": "غير مصرح."}), 403

    delete_conversation(conv_id)
    return jsonify({"status": "deleted"})


@conversations_bp.route("/api/conversations/search", methods=["POST"])
@rate_limit(max_per_minute=20)
@require_auth_or_guest
def search_convs():
    """Search conversations by title or content."""
    data = request.get_json(force=True)
    query = data.get("query", "").strip()

    if not query or len(query) < 2:
        return jsonify({"error": "البحث يجب أن يكون حرفين على الأقل."}), 400

    results = search_conversations(
        query=query,
        user_id=g.user_id,
        guest_id=g.guest_id,
    )

    return jsonify({"conversations": results})
