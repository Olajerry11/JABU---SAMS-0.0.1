# =============================================================================
# JABU-SAMS | app/edge/validators.py
# Phase 3: The Offline QR Validation & Access-Rule Engine.
#
# PURPOSE:
# This module contains the exact logic a guard tablet runs locally to
# accept or deny a student QR scan with ZERO network calls.
#
# It is intentionally written as pure Python (no Flask, no SQLAlchemy) so
# that it can be:
#   1. Run on the server for testing / simulation.
#   2. Ported to Dart/Flutter as a direct algorithmic reference.
#   3. Unit-tested in complete isolation.
#
# VALIDATION FLOW:
#   Student presents QR code
#         │
#         ▼
#   1. Decode QR → extract (user_id, totp_token)
#         │
#         ▼
#   2. validate_totp(totp_secret, totp_token)
#      → False: DENY immediately (invalid/expired token)
#         │
#         ▼
#   3. check_access_rules(user_record)
#      → False: DENY with reason (owing fees, deactivated, etc.)
#         │
#         ▼
#   4. ALLOW → build_access_decision() → tablet renders green screen
# =============================================================================

import pyotp
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# TOTP Validation
# =============================================================================

def validate_totp(totp_secret: str, token: str) -> bool:
    """
    Validates a 6-digit TOTP token against a student's shared secret.

    The Student App generates a new 6-digit token every 30 seconds from
    the `totp_secret` stored on the student's phone. This function uses
    the same secret (retrieved from `local_users`) to independently
    generate the expected token and compare.

    CLOCK DRIFT TOLERANCE:
        `valid_window=1` allows ±1 time-step (±30 seconds) tolerance.
        This is essential because:
          - The student's phone clock may drift from the tablet's clock.
          - The student may present the QR right as a 30s window expires.
        A window of 1 is the industry standard for TOTP validation.

    SECURITY NOTE:
        pyotp uses constant-time string comparison internally, preventing
        timing-oracle attacks where an attacker measures response time
        differences between correct and incorrect token guesses.

    Args:
        totp_secret (str): The BASE32-encoded TOTP secret from `local_users`.
        token (str):       The 6-digit token decoded from the student's QR code.

    Returns:
        bool: True if the token is valid within the ±30s drift window.
    """
    if not totp_secret or not token:
        logger.warning("[TOTP] Validation called with empty secret or token.")
        return False

    try:
        totp = pyotp.TOTP(totp_secret)
        # valid_window=1 = accept current token ±1 interval (±30 seconds)
        is_valid = totp.verify(token, valid_window=1)
        logger.debug(
            f"[TOTP] Validation result: {'PASS' if is_valid else 'FAIL'} "
            f"(token={token[:2]}****)"  # Log only the first 2 digits for security
        )
        return is_valid
    except Exception as e:
        # Malformed secret (e.g., corrupted sync data) — deny safely
        logger.error(f"[TOTP] Validation error: {e}")
        return False


# =============================================================================
# Access Rule Engine
# =============================================================================

# Student types that are subject to fee-status gate restrictions.
# Staff, Guests, and Vendors are exempt from fee checks.
FEE_RESTRICTED_TYPES = {"Regular", "Conversion"}

# Fee statuses that permit gate entry.
PERMITTED_FEE_STATUSES = {"Cleared", "Exempt"}


def check_access_rules(user: dict) -> tuple[bool, str]:
    """
    Evaluates the JABU-SAMS RBAC rules for a student record.

    This function encodes ALL gate access business logic that can be
    determined from the locally cached `local_users` record alone.

    Rules evaluated (in priority order):
        1. is_active must be True  — archived/graduated users cannot enter.
        2. fee_status check        — Regular and Conversion students must
                                     have 'Cleared' or 'Exempt' fee status.

    Args:
        user (dict): A dictionary matching the `local_users` schema:
                     {
                         "user_id":      "uuid",
                         "full_name":    "Adeola Fashola",
                         "student_type": "Regular",
                         "fee_status":   "Owing",
                         "is_active":    1  (SQLite integer) or True
                     }

    Returns:
        tuple[bool, str]:
            (True,  "")                    → Access permitted
            (False, "REASON_CODE: detail") → Access denied, reason for guard UI
    """
    # ------------------------------------------------------------------
    # Rule 1: Account must be active.
    # is_active is stored as INTEGER in SQLite (1=True, 0=False).
    # We handle both int and bool for portability.
    # ------------------------------------------------------------------
    is_active = user.get("is_active")
    if not is_active or is_active == 0:
        reason = (
            "ACCOUNT_INACTIVE: This account has been deactivated. "
            "Contact the University Registry."
        )
        logger.info(
            f"[ACCESS] DENIED — user_id={user.get('user_id')} | "
            f"reason=ACCOUNT_INACTIVE"
        )
        return False, reason

    # ------------------------------------------------------------------
    # Rule 2: Fee clearance check for Regular and Conversion students.
    # Camp Guests, Staff, Vendors, and Runners bypass this check.
    # ------------------------------------------------------------------
    student_type = user.get("student_type", "")
    fee_status   = user.get("fee_status", "Owing")

    if student_type in FEE_RESTRICTED_TYPES:
        if fee_status not in PERMITTED_FEE_STATUSES:
            reason = (
                f"FEES_OUTSTANDING: Access restricted. "
                f"Current fee status: '{fee_status}'. "
                f"Please visit the Bursary to clear your fees."
            )
            logger.info(
                f"[ACCESS] DENIED — user_id={user.get('user_id')} | "
                f"reason=FEES_OUTSTANDING | fee_status={fee_status}"
            )
            return False, reason

    # ------------------------------------------------------------------
    # All rules passed — access is permitted.
    # ------------------------------------------------------------------
    logger.info(
        f"[ACCESS] ALLOWED — user_id={user.get('user_id')} | "
        f"type={student_type} | fee_status={fee_status}"
    )
    return True, ""


# =============================================================================
# Full Access Decision Builder
# =============================================================================

def build_access_decision(
    user: dict,
    token: str,
    location: str,
    action_type: str = "Entry",
    guard_id: Optional[str] = None,
) -> dict:
    """
    Runs the complete validation pipeline and returns a structured decision
    payload ready to be rendered by the Flutter tablet UI.

    This is the single function the Flutter app calls after decoding a QR code.
    It combines TOTP validation and RBAC rule checking into one atomic result.

    The returned dict also contains the pre-populated AccessLog fields so
    the tablet can write a `pending_sync_logs` record directly from this
    output — no additional data transformation needed.

    Args:
        user (dict):        The `local_users` record for the scanned student.
        token (str):        The 6-digit TOTP token from the decoded QR code.
        location (str):     The scanning checkpoint (e.g., 'Main Gate').
        action_type (str):  The event type (default 'Entry').
        guard_id (str):     UUID of the officer performing the scan.

    Returns:
        dict: {
            "allowed":        bool,
            "denial_reason":  str  (empty string if allowed),
            "user_id":        str,
            "full_name":      str,
            "student_type":   str,
            "room_info":      str  (formatted room string, populated separately),
            "scan_timestamp": str  (ISO 8601 UTC — the tablet's clock at this instant),
            "log_payload":    dict (ready to INSERT into pending_sync_logs)
        }
    """
    import uuid

    # Record the scan timestamp at the moment this function is called —
    # this is the tablet's "ground truth" event time.
    scan_timestamp = datetime.now(timezone.utc).isoformat()

    # Pre-generate a UUID for the log record — matches the tablet's offline
    # idempotent UUID strategy described in the schema comments.
    log_id = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Step 1: TOTP Validation
    # ------------------------------------------------------------------
    totp_secret = user.get("totp_secret", "")
    totp_valid  = validate_totp(totp_secret, token)

    if not totp_valid:
        return {
            "allowed":        False,
            "denial_reason":  "INVALID_TOKEN: QR code is invalid or has expired. Ask the student to refresh their JABU-SAMS app.",
            "user_id":        user.get("user_id"),
            "full_name":      user.get("full_name", "Unknown"),
            "student_type":   user.get("student_type", "Unknown"),
            "room_info":      "",
            "scan_timestamp": scan_timestamp,
            "log_payload": {
                "log_id":         log_id,
                "user_id":        user.get("user_id"),
                "guard_id":       guard_id,
                "location":       location,
                "action_type":    "Access_Denied",
                "scan_timestamp": scan_timestamp,
                "notes":          "TOTP validation failed.",
                "sync_status":    "PENDING",
                "created_at":     scan_timestamp,
                "retry_count":    0,
            },
        }

    # ------------------------------------------------------------------
    # Step 2: RBAC Access Rule Check
    # ------------------------------------------------------------------
    allowed, denial_reason = check_access_rules(user)

    final_action_type = action_type if allowed else "Access_Denied"

    # ------------------------------------------------------------------
    # Step 3: Build the complete decision payload.
    # ------------------------------------------------------------------
    return {
        "allowed":        allowed,
        "denial_reason":  denial_reason,
        "user_id":        user.get("user_id"),
        "full_name":      user.get("full_name", "Unknown"),
        "student_type":   user.get("student_type", "Unknown"),
        "room_info":      "",   # Populated by the tablet after querying local_allocations
        "scan_timestamp": scan_timestamp,
        "log_payload": {
            # Fields for pending_sync_logs
            "log_id":         log_id,
            "user_id":        user.get("user_id"),
            "guard_id":       guard_id,
            "location":       location,
            "action_type":    final_action_type,
            "scan_timestamp": scan_timestamp,
            "notes":          denial_reason if not allowed else None,
            "sync_status":    "PENDING",
            "created_at":     scan_timestamp,
            "retry_count":    0,
        },
    }
