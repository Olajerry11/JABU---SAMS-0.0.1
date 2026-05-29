# =============================================================================
# JABU-SAMS | app/models/core.py
# SQLAlchemy ORM Models — The Canonical Data Schema.
#
# ARCHITECTURAL DECISION — UUID Primary Keys:
# All primary keys use PostgreSQL's gen_random_uuid() server-side function.
# Rationale: Android tablets at the gate operate offline for extended periods.
# If PKs were sequential integers (1, 2, 3...), two tablets could generate
# the same ID for different records. When their offline queues sync to the
# cloud, one record would silently overwrite the other — data loss with no
# error. UUIDs are statistically guaranteed to be globally unique, so the
# sync engine can safely INSERT every batch without collision checks.
#
# ARCHITECTURAL DECISION — Soft Deletes:
# No record is ever hard-deleted. Instead, an `is_active` boolean flag
# controls visibility. This preserves the full audit trail required for
# university security compliance and is essential for the alumni archival
# batch process.
# =============================================================================

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

# Import `db` from the package root — this is the shared SQLAlchemy instance
# initialized in app/__init__.py. Models must use this exact instance.
from app import db


# =============================================================================
# Custom Base Mixin
# =============================================================================

class TimestampMixin:
    """
    Reusable mixin that adds audit timestamp columns to any model.

    - created_at: Set automatically when a record is first inserted.
    - updated_at: Updated automatically on every subsequent UPDATE.

    Using server_default and onupdate with func.now() delegates the
    timestamp computation to PostgreSQL, ensuring consistency regardless
    of the Python application server's system clock.
    """
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),   # PostgreSQL sets this on INSERT
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),         # PostgreSQL updates this on every UPDATE
        nullable=False,
    )


# =============================================================================
# ENUM TYPE DEFINITIONS
# =============================================================================
# Defining Python Enum objects that SQLAlchemy maps to PostgreSQL native
# ENUM types. Using native PostgreSQL ENUMs provides:
# 1. Type safety at the DB level — invalid values are rejected by the DB.
# 2. Efficient storage (stored as integer internally, displayed as string).
# 3. Schema self-documentation.
# =============================================================================

# --- StudentType Enum ---
# Describes the role/category of every principal registered in the system.
# This single field drives the RBAC engine, room assignment rules, and
# disciplinary restrictions (e.g., cooking prohibition for Regular students).
StudentTypeEnum = Enum(
    "Regular",       # 100L–500L degree students. Downstairs. No cooking.
    "Conversion",    # Direct-entry / conversion programme students.
    "Camp Guest",    # Prayer Camp guests. Upstairs lodges. Cooking permitted.
    "Staff",         # University staff with permanent access rights.
    "Vendor",        # External vendors with time-restricted access.
    "Runner",        # Verified internal delivery runners (chain of custody).
    name="student_type_enum",     # Name of the ENUM type in PostgreSQL
    create_type=True,             # Create the ENUM type in the DB if absent
)

# --- FeeStatus Enum ---
# Fee clearance is the primary gate condition for Regular and Conversion
# students. The sync engine must propagate fee changes to all tablet
# SQLite databases promptly.
FeeStatusEnum = Enum(
    "Cleared",        # Fees fully paid. Gate access permitted.
    "Pending",        # Payment initiated but not confirmed.
    "Owing",          # Outstanding balance. Gate access restricted.
    "Exempt",         # Staff/guests not subject to fee checks.
    name="fee_status_enum",
    create_type=True,
)

# --- Floor Enum ---
# The physical floor assignment is a RBAC-driven rule:
# Regular students → Downstairs | Camp Guests → Upstairs.
# Hard-coding this in the DB prevents manual assignment errors.
FloorEnum = Enum(
    "Downstairs",
    "Upstairs",
    name="floor_enum",
    create_type=True,
)

# --- ActionType Enum ---
# Every gate or porter scan is classified into one of these event types.
# This powers the audit trail, the self-pickup timer logic, and security flags.
ActionTypeEnum = Enum(
    "Entry",              # Student scanned IN at main gate or hostel door.
    "Exit",               # Student scanned OUT at inner gate.
    "Luggage_Cleared",    # Guard completed primary luggage inspection.
    "Luggage_Waived",     # Porter waived secondary search (chain validated).
    "Package_Pickup",     # Exit Token issued for Jumia/delivery pickup.
    "Runner_Handoff",     # Package transferred to a verified internal runner.
    "Security_Flag",      # 20-min timer expired; hostel porter notified.
    "Guest_Registered",   # New Camp Guest registered at gate with voucher.
    "Access_Denied",      # Scan rejected (unpaid fees, expired token, etc.).
    name="action_type_enum",
    create_type=True,
)


# =============================================================================
# MODEL 1: User
# =============================================================================

class User(TimestampMixin, db.Model):
    """
    The central identity record for every principal in the JABU-SAMS system.

    Covers all user types: students (Regular, Conversion), Camp Guests,
    Staff, Vendors, and delivery Runners. The `student_type` field drives
    all downstream RBAC, room allocation, and disciplinary logic.

    UUID PK Strategy:
        The UUID is generated by PostgreSQL's gen_random_uuid() function,
        not by Python's uuid.uuid4(). This ensures uniqueness is guaranteed
        at the database layer, even if two application servers attempt to
        insert records simultaneously.
    """

    __tablename__ = "users"

    # --- Primary Key ---
    # server_default delegates UUID generation to PostgreSQL.
    # Python never needs to generate or know this value before insertion.
    user_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        comment="UUID PK generated by PostgreSQL. Prevents offline sync collisions.",
    )

    # --- Identity Fields ---
    # Matric number is the student's official university identifier.
    # Unique and indexed for fast QR validation lookups.
    # nullable=True because Staff/Vendors/Guests may not have a matric number.
    matric_no = Column(
        String(20),
        unique=True,
        nullable=True,
        index=True,
        comment="University matriculation number. NULL for non-student principals.",
    )

    full_name = Column(
        String(150),
        nullable=False,
        comment="Full legal name as registered with the university.",
    )

    # Email for staff/admin login; not required for all student types
    email = Column(
        String(255),
        unique=True,
        nullable=True,
        index=True,
    )

    # Bcrypt-hashed password. Only relevant for users who log in to the
    # web admin panel. Gate QR validation is handled by TOTP, not passwords.
    password_hash = Column(
        String(255),
        nullable=True,
        comment="Bcrypt hash. NULL for students who only use QR/TOTP access.",
    )

    # --- RBAC Fields ---
    student_type = Column(
        StudentTypeEnum,
        nullable=False,
        index=True,
        comment="Primary role driver. Controls room floor, cooking rules, and fee checks.",
    )

    fee_status = Column(
        FeeStatusEnum,
        nullable=False,
        default="Owing",
        server_default="Owing",
        comment="Gate access is blocked when status is 'Owing'.",
    )

    # Academic level (100–500). NULL for non-student types.
    # Updated by the end-of-session batch increment script.
    level = Column(
        Integer,
        nullable=True,
        comment="Current academic level (100, 200, ..., 500). NULL for non-students.",
    )

    # --- Security & Guest Fields ---
    # For Camp Guests: the URL of the live photo captured at registration.
    # Stored in cloud object storage (e.g., Firebase Storage / S3).
    # Guards compare this photo to the physical person presenting the voucher.
    gate_photo_url = Column(
        String(500),
        nullable=True,
        comment="Live photo captured at gate. Mandatory for Camp Guests. Prevents voucher sharing.",
    )

    # TOTP shared secret: the seed used by both the Student App and the
    # validation engine to independently compute the same time-based token.
    # This must be transmitted to the student's device securely (HTTPS only)
    # and never exposed through any API endpoint post-registration.
    totp_secret = Column(
        String(64),
        nullable=False,
        comment="TOTP shared secret. Used by Student App for offline QR generation. NEVER expose via API.",
    )

    # --- Lifecycle Fields ---
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="Soft-delete flag. False = archived/graduated alumni. Never hard-delete.",
    )

    is_alumni = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
        comment="Set to True by end-of-session batch script for 500L graduates.",
    )

    # --- Relationships ---
    # One user → many room allocations (one per academic session)
    room_allocations = relationship(
        "RoomAllocation",
        back_populates="user",
        lazy="dynamic",           # Lazy load: query only when accessed
        cascade="all, delete-orphan",
    )

    # One user → many access log entries
    access_logs = relationship(
        "AccessLog",
        foreign_keys="AccessLog.user_id",
        back_populates="user",
        lazy="dynamic",
    )

    # Access logs where this user acted as the guard/authority
    guard_logs = relationship(
        "AccessLog",
        foreign_keys="AccessLog.guard_id",
        back_populates="guard",
        lazy="dynamic",
    )

    def __repr__(self) -> str:
        return (
            f"<User user_id={self.user_id} matric='{self.matric_no}' "
            f"name='{self.full_name}' type='{self.student_type}'>"
        )


# =============================================================================
# MODEL 2: Room
# =============================================================================

class Room(TimestampMixin, db.Model):
    """
    Represents a physical room or lodge unit within the JABU campus hostels.

    The `floor` field encodes the primary RBAC rule:
    - DOWNSTAIRS → Regular students (no cooking rule applies).
    - UPSTAIRS    → Camp Guests (cooking is permitted).

    Rooms themselves do not change between sessions. Only the Room_Allocations
    mapping changes per session. This decoupling means a single room record
    can be reused across multiple academic years without modification.
    """

    __tablename__ = "rooms"

    # --- Primary Key ---
    room_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        comment="UUID PK. Globally unique room identifier.",
    )

    # --- Room Identity ---
    building_name = Column(
        String(100),
        nullable=False,
        comment="Name of the hostel block (e.g., 'David House', 'Elijah Block').",
    )

    room_number = Column(
        String(20),
        nullable=False,
        comment="Room or lodge number within the building (e.g., 'D-201', 'E-01A').",
    )

    # Floor determines cooking permissions and student type segregation.
    floor = Column(
        FloorEnum,
        nullable=False,
        comment="Downstairs=Regular Students (no cooking). Upstairs=Camp Guests (cooking OK).",
    )

    # Maximum number of occupants this room can hold
    capacity = Column(
        Integer,
        nullable=False,
        default=4,
        comment="Maximum bed/bunk count for this room.",
    )

    # --- Soft Delete ---
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
        comment="Deactivated rooms are excluded from allocation searches.",
    )

    # --- Unique Constraint ---
    # A building + room number combination must be globally unique.
    __table_args__ = (
        UniqueConstraint(
            "building_name", "room_number",
            name="uq_rooms_building_room_number",
        ),
    )

    # --- Relationships ---
    allocations = relationship(
        "RoomAllocation",
        back_populates="room",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Room room_id={self.room_id} building='{self.building_name}' "
            f"number='{self.room_number}' floor='{self.floor}'>"
        )


# =============================================================================
# MODEL 3: RoomAllocation
# =============================================================================

class RoomAllocation(TimestampMixin, db.Model):
    """
    The session-aware mapping between a User and a Room.

    CRITICAL DESIGN PRINCIPLE — Session Decoupling:
    This join table is the mechanism that makes the database "session-dynamic."
    Rooms and Users are stable, long-lived records. Room assignments, however,
    change every academic session (e.g., "2025/2026" → "2026/2027").

    By tracking the `academic_session` string on this mapping table, we can:
    1. Query a student's CURRENT room by filtering on the active session.
    2. View a student's COMPLETE hostel history across all years.
    3. Re-run allocations at the start of each session without touching User
       or Room records.
    4. Run the end-of-session batch script that re-assigns rooms, increments
       levels, and archives graduates — all by manipulating only this table
       and the User.level field.
    """

    __tablename__ = "room_allocations"

    # --- Primary Key ---
    allocation_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        comment="UUID PK for this specific session allocation record.",
    )

    # --- Foreign Keys ---
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="The user being allocated to a room.",
    )

    room_id = Column(
        UUID(as_uuid=True),
        ForeignKey("rooms.room_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="The room being allocated.",
    )

    # --- Session Tracking ---
    # Format: "YYYY/YYYY" (e.g., "2025/2026").
    # This string is the primary dimension for querying active allocations.
    academic_session = Column(
        String(20),
        nullable=False,
        index=True,
        comment="Academic session string (e.g., '2025/2026'). Primary query dimension.",
    )

    # --- Allocation Metadata ---
    # Date the student physically moved into the room
    check_in_date = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Actual move-in date. Set when student first scans into hostel.",
    )

    # Date the student officially checked out (end of session or early exit)
    check_out_date = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="Move-out date. NULL means currently occupying the room.",
    )

    # --- Unique Constraint ---
    # A user can only have ONE room per academic session.
    # Prevents double-booking the same student in two rooms in the same year.
    __table_args__ = (
        UniqueConstraint(
            "user_id", "academic_session",
            name="uq_allocation_user_per_session",
        ),
    )

    # --- Relationships ---
    user = relationship("User", back_populates="room_allocations")
    room = relationship("Room", back_populates="allocations")

    def __repr__(self) -> str:
        return (
            f"<RoomAllocation allocation_id={self.allocation_id} "
            f"user_id={self.user_id} room_id={self.room_id} "
            f"session='{self.academic_session}'>"
        )


# =============================================================================
# MODEL 4: AccessLog
# =============================================================================

class AccessLog(TimestampMixin, db.Model):
    """
    The immutable telemetric audit trail for every gate/porter scan event.

    IMMUTABILITY CONTRACT:
    Records in this table are NEVER updated after insertion. They represent
    the permanent, tamper-evident history of every physical movement event
    at the gate or hostel. Each record is cryptographically bound to the
    executing authority (guard_id) who performed the scan.

    OFFLINE-FIRST SYNC DESIGN:
    When tablets operate offline, they write to their local SQLite `access_logs`
    table (same schema). The Background Sync Manager (Phase 2) bulk-uploads
    these records here via a batch POST endpoint. The `scan_timestamp` field
    (captured by the tablet at scan time) is ALWAYS preserved as the true
    event time. The `server_timestamp` records when the cloud received the
    data — the delta between the two reveals how long the tablet was offline.

    TWO-TIMESTAMP STRATEGY:
        scan_timestamp   → When did the physical scan happen? (Tablet clock)
        server_timestamp → When did the cloud receive this record? (Server clock)
        
    This delta is the key to conflict resolution in the sync engine.
    """

    __tablename__ = "access_logs"

    # --- Primary Key ---
    # CRITICAL: This UUID is generated by the TABLET (offline) before sync,
    # not by PostgreSQL. The tablet inserts this UUID into its SQLite queue,
    # and the cloud does an INSERT ... ON CONFLICT DO NOTHING using this UUID.
    # This makes the bulk sync endpoint perfectly idempotent — re-uploading
    # the same batch never creates duplicate records.
    log_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
        comment=(
            "UUID generated OFFLINE by the tablet before sync. "
            "Idempotent re-uploads are safe because this PK is pre-assigned."
        ),
    )

    # --- Core Foreign Keys ---
    # The person whose QR code was scanned
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,    # Nullable: unknown users still generate a denied log
        index=True,
        comment="The scanned principal. NULL for unrecognized QR attempts.",
    )

    # The guard or porter who performed the scan.
    # This field is the Data Integrity anchor — every event is permanently
    # attributed to a specific officer. Null is NOT permitted.
    guard_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,    # Nullable only if the guard account is later deleted
        index=True,
        comment=(
            "The officer (guard/porter) executing the scan. "
            "Immutably bound for audit accountability."
        ),
    )

    # --- Event Classification ---
    # The physical gate or checkpoint where the scan occurred.
    # Examples: 'Main Gate', 'Inner Gate', 'David House Hostel', 'Elijah Block'
    location = Column(
        String(150),
        nullable=False,
        comment="Human-readable location identifier for the scanning checkpoint.",
    )

    action_type = Column(
        ActionTypeEnum,
        nullable=False,
        index=True,
        comment="The classified event type. Drives RBAC checks and timer triggers.",
    )

    # --- Timestamps (The Two-Clock Strategy) ---
    # scan_timestamp: Recorded by the tablet the instant the QR is decoded.
    # This is the TRUE event time, even if the tablet was offline for hours.
    scan_timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        comment=(
            "Tablet clock at scan moment. The ground truth event time. "
            "Preserved even after delayed sync."
        ),
    )

    # server_timestamp: Set by the cloud when this record is received.
    # The gap between this and scan_timestamp = offline duration.
    server_timestamp = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="UTC time the cloud API received and stored this log. Used for conflict resolution.",
    )

    # --- Luggage Trust-Chain Fields ---
    # URL of the photo of the opened bag, captured by the Main Gate guard.
    # The Hostel Porter retrieves this URL from the synced local DB to
    # visually verify the bag without performing a second physical search.
    luggage_photo_url = Column(
        String(500),
        nullable=True,
        comment=(
            "URL of gate-captured open-bag photo. "
            "Hostel Porter uses this to waive the secondary search."
        ),
    )

    # Confirms the Hostel Porter visually matched the gate photo to the bag.
    luggage_verified = Column(
        Boolean,
        nullable=True,
        comment="True when Hostel Porter confirms gate photo matches the physical bag.",
    )

    # --- Package Pickup / Self-Pickup Timer Fields ---
    # UTC deadline by which the student must scan back in after a pickup exit.
    exit_token_expires_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "UTC expiry for the 20-minute self-pickup exit token. "
            "Null for non-pickup scans. Exceeding this triggers a Security_Flag."
        ),
    )

    # --- Supplementary Data ---
    # JSON blob for future extensibility — e.g., device fingerprint,
    # GPS coordinates, voucher codes, runner handoff metadata.
    # Using Text + manual JSON parsing avoids a JSON column type dependency.
    extra_data = Column(
        Text,
        nullable=True,
        comment="JSON-encoded supplementary payload. Extensible for future event metadata.",
    )

    # Human-readable notes added by the guard/porter at scan time
    notes = Column(
        Text,
        nullable=True,
        comment="Optional officer notes recorded at the time of the scan event.",
    )

    # --- Relationships ---
    user = relationship(
        "User",
        foreign_keys=[user_id],
        back_populates="access_logs",
    )

    guard = relationship(
        "User",
        foreign_keys=[guard_id],
        back_populates="guard_logs",
    )

    def __repr__(self) -> str:
        return (
            f"<AccessLog log_id={self.log_id} action='{self.action_type}' "
            f"location='{self.location}' scanned_at={self.scan_timestamp}>"
        )
