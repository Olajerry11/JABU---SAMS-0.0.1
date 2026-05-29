# =============================================================================
# JABU-SAMS | app/api/pull_sync.py
# Phase 3: The Tablet Pull-Sync API Blueprint.
#
# PURPOSE:
# These endpoints are the "data pipeline" that keeps every guard tablet's
# SQLite database up to date with the cloud PostgreSQL.
#
# PULL vs PUSH:
#   PUSH (Phase 2) — Tablet → Cloud: POST /api/v1/sync/upload
#                    Tablet dumps its offline scan event queue to the cloud.
#
#   PULL (Phase 3) — Cloud → Tablet: GET /api/v1/sync/pull/*
#                    Tablet fetches updated student/room/allocation records.
#
# INCREMENTAL DELTA SYNC DESIGN:
# Every pull endpoint accepts a `?since=<ISO 8601 timestamp>` parameter.
# The server returns ONLY records whose `updated_at` is AFTER that timestamp.
#
# The tablet workflow:
#   1. On app start: read `last_synced_at` from `sync_metadata` table.
#   2. Call GET /api/v1/sync/pull/users?since=<last_synced_at>
#   3. Upsert the returned records into `local_users`.
#   4. Store the response's `sync_timestamp` as the new `last_synced_at`.
#   5. Repeat for rooms and allocations.
#
# WHY DELTA SYNC?
#   JABU has 10,000+ registered students. Downloading all records on every
#   sync would waste ~5 MB of bandwidth per tablet per session. With delta
#   sync, after the initial bootstrap, typical syncs transfer < 10 KB
#   (only students whose fee status changed since the last pull).
# =============================================================================

from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required

from app import db
from app.models.core import User, Room, RoomAllocation

# Blueprint registered at /api/v1/sync (merged with Phase 2 sync_bp prefix)
pull_sync_bp = Blueprint("pull_sync", __name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Maximum records returned per response page.
# Tablets paginate using the `next_cursor` field in the response.
DEFAULT_PAGE_SIZE = 500

# The academic session used when no ?session= param is provided.
# Update this at the start of each academic year.
CURRENT_SESSION = "2025/2026"


# =============================================================================
# Helper: Parse the `?since=` query parameter
# =============================================================================

def _parse_since(since_str: str | None) -> datetime:
    """
    Parses the `?since=` query parameter into a timezone-aware datetime.

    If `since` is not provided or cannot be parsed, defaults to the Unix
    epoch (1970-01-01) — which effectively returns ALL records.
    This is the correct behaviour for a brand-new tablet's first sync.

    Args:
        since_str: ISO 8601 string from the query parameter, or None.

    Returns:
        A timezone-aware datetime object.
    """
    if not since_str:
        # Epoch = return everything (first-time bootstrap behaviour)
        return datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(since_str)
        if dt.tzinfo is None:
            # Assume UTC if no timezone info provided
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        current_app.logger.warning(
            f"[PULL SYNC] Invalid `since` timestamp '{since_str}', "
            f"defaulting to epoch (full sync)."
        )
        return datetime.fromtimestamp(0, tz=timezone.utc)


# =============================================================================
# Helper: Parse the `?cursor=` pagination parameter
# =============================================================================

def _parse_cursor(cursor_str: str | None) -> int:
    """
    Parses the `?cursor=` pagination offset (integer offset strategy).

    Args:
        cursor_str: The cursor string from the query param, or None.

    Returns:
        Integer offset for the SQLAlchemy `.offset()` call.
    """
    if not cursor_str:
        return 0
    try:
        return max(0, int(cursor_str))
    except (ValueError, TypeError):
        return 0


# =============================================================================
# ENDPOINT 1: GET /api/v1/sync/pull/users
# =============================================================================

@pull_sync_bp.route("/pull/users", methods=["GET"])
@jwt_required()
def pull_users():
    """
    GET /api/v1/sync/pull/users?since=<ISO timestamp>&cursor=<int>

    Returns a paginated delta of User records updated after `since`.
    The tablet upserts these into its `local_users` SQLite table.

    EXCLUDED FIELDS (never sent to tablets):
        - password_hash : Admin credential — never exposed to field devices.

    QUERY PARAMETERS:
        since  (str, optional) : ISO 8601 timestamp. Default = epoch (all records).
        cursor (int, optional) : Pagination offset. Default = 0.
        limit  (int, optional) : Records per page. Default = 500, max = 1000.

    RESPONSE 200:
        {
            "sync_timestamp": "2026-05-29T22:00:00Z",  // Store as next `since`
            "count":          47,
            "has_more":       false,
            "next_cursor":    null,
            "users": [ { ... } ]
        }
    """
    since     = _parse_since(request.args.get("since"))
    offset    = _parse_cursor(request.args.get("cursor"))
    limit     = min(int(request.args.get("limit", DEFAULT_PAGE_SIZE)), 1000)

    # Capture the server's current time BEFORE querying.
    # The tablet stores this as `last_synced_at` so future deltas start here.
    sync_timestamp = datetime.now(timezone.utc)

    # Query: all users updated after `since`, paginated
    query = (
        db.session.query(User)
        .filter(User.updated_at > since)
        .order_by(User.updated_at.asc())  # Oldest-first ensures no records are skipped
    )

    total_matching = query.count()
    users          = query.offset(offset).limit(limit).all()
    has_more       = (offset + len(users)) < total_matching
    next_cursor    = (offset + limit) if has_more else None

    # Serialize — explicitly exclude password_hash
    users_data = [
        {
            "user_id":          str(u.user_id),
            "matric_no":        u.matric_no,
            "full_name":        u.full_name,
            "student_type":     u.student_type,
            "fee_status":       u.fee_status,
            "level":            u.level,
            "totp_secret":      u.totp_secret,        # Sent over HTTPS only
            "gate_photo_url":   u.gate_photo_url,
            "is_active":        u.is_active,
            "server_updated_at": u.updated_at.isoformat() if u.updated_at else None,
        }
        for u in users
    ]

    current_app.logger.info(
        f"[PULL SYNC] /pull/users — since={since.isoformat()} | "
        f"returning={len(users_data)} | has_more={has_more}"
    )

    return jsonify({
        "sync_timestamp": sync_timestamp.isoformat(),
        "count":          len(users_data),
        "has_more":       has_more,
        "next_cursor":    next_cursor,
        "users":          users_data,
    }), 200


# =============================================================================
# ENDPOINT 2: GET /api/v1/sync/pull/rooms
# =============================================================================

@pull_sync_bp.route("/pull/rooms", methods=["GET"])
@jwt_required()
def pull_rooms():
    """
    GET /api/v1/sync/pull/rooms?since=<ISO timestamp>

    Returns all Room records updated after `since`.
    Room records are stable — this endpoint typically returns 0 records
    after the initial bootstrap (rooms rarely change mid-session).

    RESPONSE 200:
        {
            "sync_timestamp": "2026-05-29T22:00:00Z",
            "count":          180,
            "has_more":       false,
            "next_cursor":    null,
            "rooms": [ { ... } ]
        }
    """
    since     = _parse_since(request.args.get("since"))
    offset    = _parse_cursor(request.args.get("cursor"))
    limit     = min(int(request.args.get("limit", DEFAULT_PAGE_SIZE)), 1000)

    sync_timestamp = datetime.now(timezone.utc)

    query = (
        db.session.query(Room)
        .filter(Room.updated_at > since)
        .order_by(Room.updated_at.asc())
    )

    total_matching = query.count()
    rooms          = query.offset(offset).limit(limit).all()
    has_more       = (offset + len(rooms)) < total_matching
    next_cursor    = (offset + limit) if has_more else None

    rooms_data = [
        {
            "room_id":           str(r.room_id),
            "building_name":     r.building_name,
            "room_number":       r.room_number,
            "floor":             r.floor,
            "capacity":          r.capacity,
            "is_active":         r.is_active,
            "server_updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rooms
    ]

    current_app.logger.info(
        f"[PULL SYNC] /pull/rooms — since={since.isoformat()} | "
        f"returning={len(rooms_data)} | has_more={has_more}"
    )

    return jsonify({
        "sync_timestamp": sync_timestamp.isoformat(),
        "count":          len(rooms_data),
        "has_more":       has_more,
        "next_cursor":    next_cursor,
        "rooms":          rooms_data,
    }), 200


# =============================================================================
# ENDPOINT 3: GET /api/v1/sync/pull/allocations
# =============================================================================

@pull_sync_bp.route("/pull/allocations", methods=["GET"])
@jwt_required()
def pull_allocations():
    """
    GET /api/v1/sync/pull/allocations?since=<ISO timestamp>&session=2025/2026

    Returns RoomAllocation records for the specified academic session
    updated after `since`.

    Only the CURRENT session's allocations are synced to tablets to
    conserve local storage. The `?session=` parameter allows overriding
    the default current session (useful for year-end admin tasks).

    RESPONSE 200:
        {
            "sync_timestamp":    "2026-05-29T22:00:00Z",
            "academic_session":  "2025/2026",
            "count":             3150,
            "has_more":          false,
            "next_cursor":       null,
            "allocations": [ { ... } ]
        }
    """
    since           = _parse_since(request.args.get("since"))
    offset          = _parse_cursor(request.args.get("cursor"))
    limit           = min(int(request.args.get("limit", DEFAULT_PAGE_SIZE)), 1000)
    academic_session = request.args.get("session", CURRENT_SESSION)

    sync_timestamp = datetime.now(timezone.utc)

    query = (
        db.session.query(RoomAllocation)
        .filter(
            RoomAllocation.academic_session == academic_session,
            RoomAllocation.updated_at > since,
        )
        .order_by(RoomAllocation.updated_at.asc())
    )

    total_matching = query.count()
    allocations    = query.offset(offset).limit(limit).all()
    has_more       = (offset + len(allocations)) < total_matching
    next_cursor    = (offset + limit) if has_more else None

    allocations_data = [
        {
            "allocation_id":     str(a.allocation_id),
            "user_id":           str(a.user_id),
            "room_id":           str(a.room_id),
            "academic_session":  a.academic_session,
            "check_in_date":     a.check_in_date.isoformat() if a.check_in_date else None,
            "server_updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }
        for a in allocations
    ]

    current_app.logger.info(
        f"[PULL SYNC] /pull/allocations — session={academic_session} | "
        f"since={since.isoformat()} | returning={len(allocations_data)} | "
        f"has_more={has_more}"
    )

    return jsonify({
        "sync_timestamp":   sync_timestamp.isoformat(),
        "academic_session": academic_session,
        "count":            len(allocations_data),
        "has_more":         has_more,
        "next_cursor":      next_cursor,
        "allocations":      allocations_data,
    }), 200


# =============================================================================
# ENDPOINT 4: GET /api/v1/sync/pull/bootstrap
# =============================================================================

@pull_sync_bp.route("/pull/bootstrap", methods=["GET"])
@jwt_required()
def pull_bootstrap():
    """
    GET /api/v1/sync/pull/bootstrap?session=2025/2026

    A one-shot endpoint for a BRAND-NEW TABLET's initial sync.

    Returns the first page of ALL active users, ALL active rooms, and
    ALL current-session allocations in a single response.

    For large datasets (10,000+ students), the tablet must paginate:
        1. Call /bootstrap → receive first 500 users, next_cursor for users.
        2. Call /pull/users?cursor=500 to get the next page.
        3. Continue until has_more=false for all three collections.

    The tablet detects it needs bootstrap when `sync_metadata.last_synced_at`
    is NULL (i.e., the device has never successfully synced before).

    RESPONSE 200:
        {
            "sync_timestamp":    "2026-05-29T22:00:00Z",
            "academic_session":  "2025/2026",
            "users":       { "count": 3200, "has_more": true,  "next_cursor": 500, "data": [...] },
            "rooms":       { "count": 180,  "has_more": false, "next_cursor": null, "data": [...] },
            "allocations": { "count": 3150, "has_more": false, "next_cursor": null, "data": [...] }
        }
    """
    limit            = min(int(request.args.get("limit", DEFAULT_PAGE_SIZE)), 1000)
    academic_session = request.args.get("session", CURRENT_SESSION)

    sync_timestamp = datetime.now(timezone.utc)

    # -------------------------------------------------------------------------
    # Fetch first page of active users
    # -------------------------------------------------------------------------
    user_query   = db.session.query(User).filter(User.is_active == True).order_by(User.updated_at.asc())
    total_users  = user_query.count()
    users        = user_query.limit(limit).all()
    users_has_more   = len(users) < total_users
    users_next_cursor = limit if users_has_more else None

    users_data = [
        {
            "user_id":           str(u.user_id),
            "matric_no":         u.matric_no,
            "full_name":         u.full_name,
            "student_type":      u.student_type,
            "fee_status":        u.fee_status,
            "level":             u.level,
            "totp_secret":       u.totp_secret,
            "gate_photo_url":    u.gate_photo_url,
            "is_active":         u.is_active,
            "server_updated_at": u.updated_at.isoformat() if u.updated_at else None,
        }
        for u in users
    ]

    # -------------------------------------------------------------------------
    # Fetch first page of active rooms
    # -------------------------------------------------------------------------
    room_query   = db.session.query(Room).filter(Room.is_active == True).order_by(Room.updated_at.asc())
    total_rooms  = room_query.count()
    rooms        = room_query.limit(limit).all()
    rooms_has_more    = len(rooms) < total_rooms
    rooms_next_cursor = limit if rooms_has_more else None

    rooms_data = [
        {
            "room_id":           str(r.room_id),
            "building_name":     r.building_name,
            "room_number":       r.room_number,
            "floor":             r.floor,
            "capacity":          r.capacity,
            "is_active":         r.is_active,
            "server_updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rooms
    ]

    # -------------------------------------------------------------------------
    # Fetch first page of current-session allocations
    # -------------------------------------------------------------------------
    alloc_query  = (
        db.session.query(RoomAllocation)
        .filter(RoomAllocation.academic_session == academic_session)
        .order_by(RoomAllocation.updated_at.asc())
    )
    total_allocs = alloc_query.count()
    allocations  = alloc_query.limit(limit).all()
    allocs_has_more    = len(allocations) < total_allocs
    allocs_next_cursor = limit if allocs_has_more else None

    allocations_data = [
        {
            "allocation_id":     str(a.allocation_id),
            "user_id":           str(a.user_id),
            "room_id":           str(a.room_id),
            "academic_session":  a.academic_session,
            "check_in_date":     a.check_in_date.isoformat() if a.check_in_date else None,
            "server_updated_at": a.updated_at.isoformat() if a.updated_at else None,
        }
        for a in allocations
    ]

    current_app.logger.info(
        f"[PULL SYNC] /pull/bootstrap — session={academic_session} | "
        f"users={len(users_data)} | rooms={len(rooms_data)} | "
        f"allocations={len(allocations_data)}"
    )

    return jsonify({
        "sync_timestamp":   sync_timestamp.isoformat(),
        "academic_session": academic_session,
        "users": {
            "count":       len(users_data),
            "total":       total_users,
            "has_more":    users_has_more,
            "next_cursor": users_next_cursor,
            "data":        users_data,
        },
        "rooms": {
            "count":       len(rooms_data),
            "total":       total_rooms,
            "has_more":    rooms_has_more,
            "next_cursor": rooms_next_cursor,
            "data":        rooms_data,
        },
        "allocations": {
            "count":       len(allocations_data),
            "total":       total_allocs,
            "has_more":    allocs_has_more,
            "next_cursor": allocs_next_cursor,
            "data":        allocations_data,
        },
    }), 200
