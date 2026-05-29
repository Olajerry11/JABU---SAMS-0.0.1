# =============================================================================
# JABU-SAMS | app/api/access_logs.py
# Access Logs Blueprint — The immutable scan event API.
#
# Phase 1: Single-event POST and query endpoints.
# Phase 2 (Async Sync Engine): Will add the /batch endpoint for bulk
#          offline sync uploads from tablet Background Sync Managers.
# =============================================================================

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timezone
from app import db
from app.models.core import AccessLog

access_logs_bp = Blueprint("access_logs", __name__)


@access_logs_bp.route("/", methods=["POST"])
@jwt_required()
def create_log():
    """
    POST /api/v1/logs/
    Records a single scan event (online mode).
    
    The guard's JWT identity is used as the guard_id to ensure
    every log is permanently bound to the executing officer.
    """
    data = request.get_json(silent=True) or {}
    required = ["user_id", "location", "action_type", "scan_timestamp"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": "MISSING_FIELDS", "message": f"Required: {missing}"}), 400

    # The guard who is authenticated via JWT IS the guard responsible for this scan
    guard_id = get_jwt_identity()

    try:
        scan_ts = datetime.fromisoformat(data["scan_timestamp"])
    except ValueError:
        return jsonify({"error": "INVALID_TIMESTAMP", "message": "scan_timestamp must be ISO 8601."}), 400

    log = AccessLog(
        user_id               = data["user_id"],
        guard_id              = guard_id,
        location              = data["location"],
        action_type           = data["action_type"],
        scan_timestamp        = scan_ts,
        luggage_photo_url     = data.get("luggage_photo_url"),
        luggage_verified      = data.get("luggage_verified"),
        exit_token_expires_at = data.get("exit_token_expires_at"),
        notes                 = data.get("notes"),
        extra_data            = data.get("extra_data"),
    )

    db.session.add(log)
    db.session.commit()

    return jsonify({"log_id": str(log.log_id), "server_timestamp": log.server_timestamp.isoformat()}), 201


@access_logs_bp.route("/", methods=["GET"])
@jwt_required()
def list_logs():
    """
    GET /api/v1/logs/?user_id=<uuid>&action_type=Entry&page=1
    Queries the audit trail with filters.
    """
    user_id     = request.args.get("user_id")
    action_type = request.args.get("action_type")
    location    = request.args.get("location")
    page        = int(request.args.get("page", 1))
    per_page    = min(int(request.args.get("per_page", 50)), 200)

    query = AccessLog.query
    if user_id:
        query = query.filter_by(user_id=user_id)
    if action_type:
        query = query.filter_by(action_type=action_type)
    if location:
        query = query.filter(AccessLog.location.ilike(f"%{location}%"))

    pagination = query.order_by(AccessLog.scan_timestamp.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return jsonify({
        "logs":        [_serialize_log(l) for l in pagination.items],
        "total":       pagination.total,
        "page":        pagination.page,
        "total_pages": pagination.pages,
    }), 200


def _serialize_log(log: AccessLog) -> dict:
    return {
        "log_id":               str(log.log_id),
        "user_id":              str(log.user_id) if log.user_id else None,
        "guard_id":             str(log.guard_id) if log.guard_id else None,
        "location":             log.location,
        "action_type":          log.action_type,
        "scan_timestamp":       log.scan_timestamp.isoformat(),
        "server_timestamp":     log.server_timestamp.isoformat(),
        "luggage_photo_url":    log.luggage_photo_url,
        "luggage_verified":     log.luggage_verified,
        "exit_token_expires_at":log.exit_token_expires_at.isoformat() if log.exit_token_expires_at else None,
        "notes":                log.notes,
    }
