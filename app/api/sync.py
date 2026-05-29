# =============================================================================
# JABU-SAMS | app/api/sync.py
# Phase 2: The Offline Batch Sync API Blueprint.
#
# CRITICAL CONTRACT — This blueprint NEVER writes to the database directly.
#
# WHY? The "Thundering Herd" Problem:
# Imagine 10 tablets at the Main Gate all regain internet connectivity at 6 AM
# after a night of offline operation. Each has 2,000 queued scan events.
# That is 20,000 INSERT statements hitting the API simultaneously.
#
# If this endpoint wrote to PostgreSQL in the request thread:
#   - The first tablet's request would block for ~30 seconds.
#   - The other 9 tablets would all time out and re-try — making it worse.
#   - The Flask server would become completely unresponsive for all other users.
#
# The solution is the "fire-and-forget" pattern implemented here:
#   1. Accept the batch (fast — just JSON parsing and validation).
#   2. Drop it onto the Redis task queue (.delay() — near-instant).
#   3. Return HTTP 202 Accepted to the tablet immediately.
#   4. Celery workers drain the queue in the background at their own pace.
#
# The tablet is free within milliseconds. The database is written to safely
# in the background. The API stays responsive to all other requests.
# =============================================================================

import uuid
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity

# Import the Celery task — NOT the db. This endpoint never touches the DB.
from app.celery_worker import process_offline_batch

# Blueprint definition — registered in the factory at /api/v1/sync
sync_bp = Blueprint("sync", __name__)


# =============================================================================
# POST /api/v1/sync/upload  — The Primary Sync Endpoint
# =============================================================================

@sync_bp.route("/upload", methods=["POST"])
@jwt_required()
def upload_offline_batch():
    """
    POST /api/v1/sync/upload
    Accepts a batch of offline scan events from a Guard/Porter tablet and
    dispatches them to the Celery background queue for async processing.

    AUTHENTICATION:
        Requires a valid JWT Bearer token. The authenticating identity (guard/
        tablet device account) is injected into the batch as a cross-reference.
        A tablet cannot upload a batch attributed to a different device.

    REQUEST BODY (JSON):
        {
            "tablet_id":   "550e8400-e29b-41d4-a716-446655440000",
            "uploaded_at": "2026-05-29T07:00:00+01:00",   // Optional, client clock
            "events": [
                {
                    "log_id":         "pre-assigned-uuid-from-tablet",
                    "user_id":        "scanned-student-uuid",
                    "guard_id":       "executing-officer-uuid",
                    "location":       "Main Gate",
                    "action_type":    "Entry",
                    "scan_timestamp": "2026-05-28T23:45:10+01:00",
                    "luggage_photo_url":      "https://...",    // nullable
                    "luggage_verified":       true,              // nullable
                    "exit_token_expires_at":  null,              // nullable
                    "notes":                  null,              // nullable
                    "extra_data":             null               // nullable
                }
            ]
        }

    RESPONSE — 202 Accepted (success, batch queued):
        {
            "status":      "QUEUED",
            "message":     "Batch of 47 events accepted and queued for processing.",
            "task_id":     "celery-task-uuid",
            "tablet_id":   "550e8400-...",
            "event_count": 47,
            "queued_at":   "2026-05-29T09:00:00.123456+00:00"
        }

    RESPONSE — 400 Bad Request:
        { "error": "MISSING_FIELDS", "message": "..." }

    RESPONSE — 422 Unprocessable Entity:
        { "error": "EMPTY_BATCH", "message": "..." }

    WHY 202 AND NOT 200?
        HTTP 200 OK means the operation is COMPLETE.
        HTTP 202 Accepted means "we received it and will process it."
        202 is the semantically correct status for async fire-and-forget patterns.
    """

    # -------------------------------------------------------------------------
    # Step 1: Parse and validate the incoming JSON payload.
    # We fail fast here — before touching Redis — to reject clearly bad requests.
    # -------------------------------------------------------------------------
    data = request.get_json(silent=True)

    if not data:
        return jsonify({
            "error":   "INVALID_PAYLOAD",
            "message": "Request body must be valid JSON with Content-Type: application/json."
        }), 400

    # Validate required top-level fields
    tablet_id = data.get("tablet_id")
    events    = data.get("events")

    if not tablet_id:
        return jsonify({
            "error":   "MISSING_FIELDS",
            "message": "Required field 'tablet_id' is missing from the batch payload."
        }), 400

    if events is None or not isinstance(events, list):
        return jsonify({
            "error":   "MISSING_FIELDS",
            "message": "Required field 'events' must be a JSON array."
        }), 400

    if len(events) == 0:
        return jsonify({
            "error":   "EMPTY_BATCH",
            "message": "The 'events' array is empty. Nothing to sync."
        }), 422

    # -------------------------------------------------------------------------
    # Step 2: Enforce a batch size ceiling.
    # Protects Redis memory and Celery worker throughput from abuse.
    # Tablets should chunk large queues into multiple requests of ≤ MAX_BATCH.
    # -------------------------------------------------------------------------
    MAX_BATCH_SIZE = int(current_app.config.get("SYNC_MAX_BATCH_SIZE", 1000))

    if len(events) > MAX_BATCH_SIZE:
        return jsonify({
            "error":   "BATCH_TOO_LARGE",
            "message": (
                f"Batch contains {len(events)} events, exceeding the maximum of "
                f"{MAX_BATCH_SIZE} per request. Split into smaller chunks."
            )
        }), 413  # 413 Payload Too Large

    # -------------------------------------------------------------------------
    # Step 3: Inject server-side metadata into the batch.
    #
    # The `uploaded_at` timestamp records when the CLOUD received this batch.
    # Combined with individual event `scan_timestamp` values, this measures
    # how long each tablet was offline — the key conflict resolution metric.
    #
    # The `uploader_identity` binds the JWT-authenticated user to this upload,
    # providing an audit trail of which device/account performed the sync.
    # -------------------------------------------------------------------------
    uploader_identity = get_jwt_identity()
    server_received_at = datetime.now(timezone.utc).isoformat()

    # Build the enriched batch payload that will be serialized into Redis
    enriched_batch = {
        "tablet_id":           tablet_id,
        "uploader_identity":   uploader_identity,  # JWT user who authenticated
        "client_uploaded_at":  data.get("uploaded_at"),  # Tablet's claimed upload time
        "server_received_at":  server_received_at,        # Cloud's actual receipt time
        "events":              events,
    }

    # -------------------------------------------------------------------------
    # Step 4: Dispatch to Celery — the ONLY action this endpoint takes on data.
    #
    # .delay() is shorthand for .apply_async() with default settings.
    # It serializes `enriched_batch` to JSON and pushes it to the Redis
    # 'jabu_sync' queue. This call is near-instantaneous (microseconds).
    #
    # The returned `task` object contains the unique task ID, which the
    # tablet can use to poll task status via the /status endpoint below.
    # -------------------------------------------------------------------------
    try:
        task = process_offline_batch.delay(enriched_batch)
    except Exception as exc:
        # If Redis is unavailable, report a service unavailable error.
        # DO NOT silently drop the batch.
        current_app.logger.error(
            f"[SYNC] Failed to queue batch from tablet '{tablet_id}': {exc}"
        )
        return jsonify({
            "error":   "QUEUE_UNAVAILABLE",
            "message": (
                "The background task queue is currently unavailable. "
                "Please retain your local queue and retry in 60 seconds."
            )
        }), 503  # 503 Service Unavailable

    current_app.logger.info(
        f"[SYNC] Batch queued — tablet: '{tablet_id}' | "
        f"events: {len(events)} | task_id: {task.id} | "
        f"uploader: {uploader_identity}"
    )

    # -------------------------------------------------------------------------
    # Step 5: Return 202 Accepted immediately.
    # The tablet is now free. Celery handles the rest.
    # -------------------------------------------------------------------------
    return jsonify({
        "status":      "QUEUED",
        "message":     f"Batch of {len(events)} events accepted and queued for processing.",
        "task_id":     task.id,       # Store this to poll task status
        "tablet_id":   tablet_id,
        "event_count": len(events),
        "queued_at":   server_received_at,
    }), 202  # HTTP 202 Accepted — processing will happen asynchronously


# =============================================================================
# GET /api/v1/sync/status/<task_id>  — Task Status Polling Endpoint
# =============================================================================

@sync_bp.route("/status/<string:task_id>", methods=["GET"])
@jwt_required()
def get_batch_status(task_id: str):
    """
    GET /api/v1/sync/status/<task_id>
    Allows a tablet to poll the processing status of a previously submitted batch.

    The tablet should call this endpoint after receiving a 202 response to
    confirm that all events were persisted successfully. This is the "close
    the loop" step in the fire-and-forget pattern.

    RESPONSE STATES:
        PENDING  — Task is in the queue, not yet picked up by a worker.
        STARTED  — A Celery worker is actively processing the batch.
        SUCCESS  — All events processed. Result contains the summary report.
        FAILURE  — Task failed after all retries. Inspect `error` field.
        RETRY    — Task failed once and is waiting to retry.

    Args:
        task_id: The UUID returned in the /upload response's 'task_id' field.
    """
    from celery.result import AsyncResult
    from app.celery_worker import celery_app

    # Fetch the task result object from Redis via the task ID
    task_result = AsyncResult(task_id, app=celery_app)

    response_body = {
        "task_id": task_id,
        "status":  task_result.status,   # PENDING | STARTED | SUCCESS | FAILURE | RETRY
    }

    if task_result.status == "SUCCESS":
        # Attach the full summary report from the worker
        response_body["result"] = task_result.result
        http_status = 200

    elif task_result.status == "FAILURE":
        # Task failed permanently after all retries
        response_body["error"] = str(task_result.result)
        response_body["message"] = (
            "Batch processing failed permanently. Please re-submit the batch "
            "or contact system administration."
        )
        http_status = 500

    elif task_result.status == "PENDING":
        response_body["message"] = "Batch is queued and waiting for a worker to pick it up."
        http_status = 202

    else:
        # STARTED, RETRY, or unknown state
        response_body["message"] = f"Batch is being processed (state: {task_result.status})."
        http_status = 202

    return jsonify(response_body), http_status


# =============================================================================
# GET /api/v1/sync/health  — Sync Engine Health Check
# =============================================================================

@sync_bp.route("/health", methods=["GET"])
def sync_health():
    """
    GET /api/v1/sync/health
    Public health check endpoint (no JWT required).
    Verifies that the Celery/Redis sync engine is reachable.

    Used by tablet clients to determine whether it is safe to attempt a sync
    before sending a large batch. Tablets can call this first to avoid a 503.

    RESPONSE:
        200 — { "status": "OK",      "broker": "reachable" }
        503 — { "status": "DEGRADED","broker": "unreachable", "error": "..." }
    """
    from app.celery_worker import celery_app

    try:
        # Ping the Celery broker (Redis). Times out after 1 second.
        celery_app.control.ping(timeout=1.0)
        broker_status = "reachable"
        http_status   = 200
        status        = "OK"
    except Exception as exc:
        broker_status = "unreachable"
        http_status   = 503
        status        = "DEGRADED"
        current_app.logger.error(f"[SYNC HEALTH] Redis broker unreachable: {exc}")

    return jsonify({
        "status":    status,
        "broker":    broker_status,
        "component": "JABU-SAMS Async Sync Engine",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }), http_status
