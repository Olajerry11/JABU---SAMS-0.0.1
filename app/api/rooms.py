# =============================================================================
# JABU-SAMS | app/api/rooms.py
# Rooms & Allocations Blueprint.
# =============================================================================

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required
from sqlalchemy.exc import IntegrityError
from app import db
from app.models.core import Room, RoomAllocation

rooms_bp = Blueprint("rooms", __name__)


@rooms_bp.route("/", methods=["GET"])
@jwt_required()
def list_rooms():
    """GET /api/v1/rooms/ — List all active rooms, filterable by floor/building."""
    floor     = request.args.get("floor")
    building  = request.args.get("building")
    query     = Room.query.filter_by(is_active=True)
    if floor:
        query = query.filter_by(floor=floor)
    if building:
        query = query.filter(Room.building_name.ilike(f"%{building}%"))
    rooms = query.order_by(Room.building_name, Room.room_number).all()
    return jsonify({"rooms": [_serialize_room(r) for r in rooms]}), 200


@rooms_bp.route("/", methods=["POST"])
@jwt_required()
def create_room():
    """POST /api/v1/rooms/ — Register a new room/lodge."""
    data = request.get_json(silent=True) or {}
    required = ["building_name", "room_number", "floor"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": "MISSING_FIELDS", "message": f"Required: {missing}"}), 400

    room = Room(
        building_name = data["building_name"],
        room_number   = data["room_number"],
        floor         = data["floor"],
        capacity      = data.get("capacity", 4),
    )
    try:
        db.session.add(room)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "CONFLICT", "message": "Room already exists in this building."}), 409

    return jsonify(_serialize_room(room)), 201


@rooms_bp.route("/allocations", methods=["POST"])
@jwt_required()
def allocate_room():
    """
    POST /api/v1/rooms/allocations
    Assigns a user to a room for a specific academic session.
    Enforces the one-user-per-session unique constraint.
    """
    data = request.get_json(silent=True) or {}
    required = ["user_id", "room_id", "academic_session"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": "MISSING_FIELDS", "message": f"Required: {missing}"}), 400

    allocation = RoomAllocation(
        user_id          = data["user_id"],
        room_id          = data["room_id"],
        academic_session = data["academic_session"],
    )
    try:
        db.session.add(allocation)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "error": "CONFLICT",
            "message": "This user already has a room allocated for this session."
        }), 409

    return jsonify({"allocation_id": str(allocation.allocation_id)}), 201


def _serialize_room(room: Room) -> dict:
    return {
        "room_id":       str(room.room_id),
        "building_name": room.building_name,
        "room_number":   room.room_number,
        "floor":         room.floor,
        "capacity":      room.capacity,
        "is_active":     room.is_active,
    }
