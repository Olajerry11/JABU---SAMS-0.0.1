# =============================================================================
# JABU-SAMS | app/api/auth.py
# Authentication Blueprint — Login and token refresh endpoints.
# Phase 1: Skeleton with full JWT token generation logic.
# =============================================================================

from flask import Blueprint, request, jsonify
from flask_jwt_extended import (
    create_access_token,
    create_refresh_token,
    jwt_required,
    get_jwt_identity,
)
from app import db
from app.models.core import User
import bcrypt

# Blueprint definition — registered in the factory at /api/v1/auth
auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    POST /api/v1/auth/login
    Authenticates a staff/admin user and returns a JWT access + refresh token pair.
    
    Note: Gate guards use QR/TOTP scanning, not password login.
    This endpoint is for staff who access the web admin panel.

    Request Body (JSON):
        { "email": "admin@jabu.edu.ng", "password": "securepassword" }

    Returns:
        200: { "access_token": "...", "refresh_token": "...", "user": {...} }
        401: Invalid credentials.
        400: Missing fields.
    """
    data = request.get_json(silent=True)
    if not data or not data.get("email") or not data.get("password"):
        return jsonify({"error": "MISSING_FIELDS", "message": "Email and password are required."}), 400

    # Fetch user by email (indexed for performance)
    user = User.query.filter_by(email=data["email"], is_active=True).first()

    # Validate password against bcrypt hash
    if not user or not user.password_hash:
        return jsonify({"error": "INVALID_CREDENTIALS", "message": "Invalid email or password."}), 401

    if not bcrypt.checkpw(data["password"].encode("utf-8"), user.password_hash.encode("utf-8")):
        return jsonify({"error": "INVALID_CREDENTIALS", "message": "Invalid email or password."}), 401

    # Create JWT tokens — the identity claim stores the user's UUID string
    access_token = create_access_token(identity=str(user.user_id))
    refresh_token = create_refresh_token(identity=str(user.user_id))

    return jsonify({
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "user": {
            "user_id":      str(user.user_id),
            "full_name":    user.full_name,
            "email":        user.email,
            "student_type": user.student_type,
        }
    }), 200


@auth_bp.route("/refresh", methods=["POST"])
@jwt_required(refresh=True)
def refresh():
    """
    POST /api/v1/auth/refresh
    Issues a new short-lived access token using a valid long-lived refresh token.
    Requires: Authorization: Bearer <refresh_token>
    """
    identity = get_jwt_identity()
    new_access_token = create_access_token(identity=identity)
    return jsonify({"access_token": new_access_token}), 200


@auth_bp.route("/me", methods=["GET"])
@jwt_required()
def me():
    """
    GET /api/v1/auth/me
    Returns the profile of the currently authenticated user.
    Requires: Authorization: Bearer <access_token>
    """
    user_id = get_jwt_identity()
    user = db.session.get(User, user_id)

    if not user:
        return jsonify({"error": "USER_NOT_FOUND", "message": "User record not found."}), 404

    return jsonify({
        "user_id":      str(user.user_id),
        "full_name":    user.full_name,
        "matric_no":    user.matric_no,
        "email":        user.email,
        "student_type": user.student_type,
        "fee_status":   user.fee_status,
        "level":        user.level,
        "is_active":    user.is_active,
        "is_alumni":    user.is_alumni,
    }), 200
