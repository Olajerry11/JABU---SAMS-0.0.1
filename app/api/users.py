# =============================================================================
# JABU-SAMS | app/api/users.py
# Users Blueprint — CRUD for all principals (students, staff, guests).
# =============================================================================

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import IntegrityError
from app import db
from app.models.core import User
import bcrypt
import pyotp  # For TOTP secret generation (pip install pyotp)

users_bp = Blueprint("users", __name__)


@users_bp.route("/", methods=["GET"])
@jwt_required()
def list_users():
    """
    GET /api/v1/users/?student_type=Regular&is_active=true&page=1&per_page=20
    Returns a paginated list of users, filterable by type and status.
    """
    student_type = request.args.get("student_type")
    is_active    = request.args.get("is_active", "true").lower() == "true"
    page         = int(request.args.get("page", 1))
    per_page     = min(int(request.args.get("per_page", 20)), 100)

    query = User.query.filter_by(is_active=is_active)
    if student_type:
        query = query.filter_by(student_type=student_type)

    pagination = query.order_by(User.full_name).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "users": [_serialize_user(u) for u in pagination.items],
        "total":       pagination.total,
        "page":        pagination.page,
        "per_page":    pagination.per_page,
        "total_pages": pagination.pages,
    }), 200


@users_bp.route("/<string:user_id>", methods=["GET"])
@jwt_required()
def get_user(user_id: str):
    """GET /api/v1/users/<user_id> — Fetch a single user by UUID."""
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "NOT_FOUND", "message": "User not found."}), 404
    return jsonify(_serialize_user(user)), 200


@users_bp.route("/", methods=["POST"])
@jwt_required()
def create_user():
    """
    POST /api/v1/users/
    Registers a new principal (student, guest, staff, etc.).
    Automatically generates a TOTP secret for QR code generation.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "MISSING_BODY", "message": "Request body is required."}), 400

    required = ["full_name", "student_type"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": "MISSING_FIELDS", "message": f"Required: {missing}"}), 400

    # Generate a unique TOTP shared secret for this user.
    # This base32 secret seeds both the Student App's offline QR generator
    # and the tablet's TOTP validation engine.
    totp_secret = pyotp.random_base32()

    # Hash the password if provided (for staff with admin panel access)
    password_hash = None
    if data.get("password"):
        password_hash = bcrypt.hashpw(
            data["password"].encode("utf-8"), bcrypt.gensalt()
        ).decode("utf-8")

    user = User(
        full_name     = data["full_name"],
        student_type  = data["student_type"],
        matric_no     = data.get("matric_no"),
        email         = data.get("email"),
        fee_status    = data.get("fee_status", "Owing"),
        level         = data.get("level"),
        gate_photo_url= data.get("gate_photo_url"),
        totp_secret   = totp_secret,
        password_hash = password_hash,
    )

    try:
        db.session.add(user)
        db.session.commit()
    except IntegrityError as e:
        db.session.rollback()
        return jsonify({"error": "CONFLICT", "message": "Matric number or email already exists."}), 409

    # Return the TOTP secret ONCE at creation time so the Student App can
    # store it. It will never be returned again through any API endpoint.
    return jsonify({
        **_serialize_user(user),
        "totp_secret": user.totp_secret,  # ONE-TIME delivery — store immediately
    }), 201


@users_bp.route("/<string:user_id>", methods=["PATCH"])
@jwt_required()
def update_user(user_id: str):
    """
    PATCH /api/v1/users/<user_id>
    Partial update — update only the fields provided in the request body.
    Common use cases: fee status updates, photo URL updates after registration.
    """
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "NOT_FOUND", "message": "User not found."}), 404

    data = request.get_json(silent=True) or {}
    allowed_fields = [
        "full_name", "fee_status", "level", "gate_photo_url",
        "is_active", "is_alumni", "email",
    ]

    for field in allowed_fields:
        if field in data:
            setattr(user, field, data[field])

    db.session.commit()
    return jsonify(_serialize_user(user)), 200


def _serialize_user(user: User) -> dict:
    """Converts a User ORM object to a safe JSON-serializable dict.
    NEVER includes totp_secret or password_hash."""
    return {
        "user_id":       str(user.user_id),
        "matric_no":     user.matric_no,
        "full_name":     user.full_name,
        "email":         user.email,
        "student_type":  user.student_type,
        "fee_status":    user.fee_status,
        "level":         user.level,
        "gate_photo_url":user.gate_photo_url,
        "is_active":     user.is_active,
        "is_alumni":     user.is_alumni,
        "created_at":    user.created_at.isoformat() if user.created_at else None,
        "updated_at":    user.updated_at.isoformat() if user.updated_at else None,
    }
