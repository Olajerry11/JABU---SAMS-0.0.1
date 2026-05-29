# =============================================================================
# JABU-SAMS | app/edge/sqlite_schema.py
# Phase 3: The Canonical SQLite Schema for the Guard Tablet.
#
# PURPOSE:
# This file is the single source of truth for the tablet's local database.
# It defines the 5 SQLite tables the Flutter app creates on first launch
# using the sqflite package's `database.execute()` method.
#
# WHY SQLite ON THE TABLET?
# Guard tablets at JABU's Main Gate and Hostel Porter stations operate in
# areas with intermittent cellular coverage. When connectivity drops, the
# tablet must continue to:
#   1. Validate student QR codes → needs `local_users` (with totp_secret)
#   2. Look up room assignments → needs `local_allocations` + `local_rooms`
#   3. Record scan events       → needs `pending_sync_logs` (the event queue)
#
# SYNC ARCHITECTURE:
#   PULL (Server → Tablet):
#     GET /api/v1/sync/pull/users       → fills `local_users`
#     GET /api/v1/sync/pull/rooms       → fills `local_rooms`
#     GET /api/v1/sync/pull/allocations → fills `local_allocations`
#
#   PUSH (Tablet → Server):
#     POST /api/v1/sync/upload          → drains `pending_sync_logs`
#     (Handled by the Phase 2 Celery engine)
#
# SCHEMA MIRRORING POLICY:
# The local_* tables intentionally omit sensitive server-only fields:
#   - `password_hash` is NEVER sent to tablets (guards don't log in with it)
#   - `created_at`/`updated_at` are replaced by `server_updated_at` (the
#     server's timestamp, used as the delta-sync cursor)
# =============================================================================


# =============================================================================
# Table 1: local_users
# =============================================================================
# A synced, read-only cache of student/staff records used for offline
# QR validation. The guard tablet queries this table when it cannot reach
# the cloud API.
#
# CRITICAL FIELD: totp_secret
#   This is the TOTP shared secret — the seed that both the Student App and
#   this validator use to independently compute the same 6-digit token.
#   It is transmitted over HTTPS and stored here in plain text.
#   Physical tablet security (pin lock, supervised access) is sufficient
#   protection for JABU's campus deployment model.
# =============================================================================
CREATE_LOCAL_USERS_SQL = """
CREATE TABLE IF NOT EXISTS local_users (
    -- Primary key mirrors the cloud UUID, stored as TEXT in SQLite
    user_id          TEXT PRIMARY KEY NOT NULL,

    -- Identity fields
    matric_no        TEXT UNIQUE,                  -- NULL for Staff/Vendors/Guests
    full_name        TEXT NOT NULL,
    student_type     TEXT NOT NULL,                -- 'Regular'|'Conversion'|'Camp Guest'|...
    fee_status       TEXT NOT NULL DEFAULT 'Owing',-- 'Cleared'|'Pending'|'Owing'|'Exempt'
    level            INTEGER,                       -- 100–500, NULL for non-students

    -- QR validation fields
    totp_secret      TEXT NOT NULL,                -- TOTP shared secret (HTTPS-only transfer)
    gate_photo_url   TEXT,                         -- Live photo URL for Camp Guest ID check

    -- Lifecycle flag
    is_active        INTEGER NOT NULL DEFAULT 1,   -- 1=active, 0=archived (SQLite has no BOOL)

    -- Sync cursor: the server's updated_at timestamp for this record.
    -- The pull-sync endpoint uses this to send only records newer than
    -- the tablet's last known value (delta sync).
    server_updated_at TEXT NOT NULL                -- ISO 8601 UTC string
);

-- Index on matric_no for fast QR lookup during offline validation
CREATE INDEX IF NOT EXISTS idx_local_users_matric
    ON local_users (matric_no);

-- Index on student_type for filtering by type in the UI
CREATE INDEX IF NOT EXISTS idx_local_users_type
    ON local_users (student_type);
"""


# =============================================================================
# Table 2: local_rooms
# =============================================================================
# A synced cache of campus hostel room records.
# The Hostel Porter uses this to look up a student's room assignment offline.
# Rooms are stable records — they rarely change between sessions.
# =============================================================================
CREATE_LOCAL_ROOMS_SQL = """
CREATE TABLE IF NOT EXISTS local_rooms (
    room_id          TEXT PRIMARY KEY NOT NULL,
    building_name    TEXT NOT NULL,                -- e.g., 'David House', 'Elijah Block'
    room_number      TEXT NOT NULL,                -- e.g., 'D-201', 'E-01A'
    floor            TEXT NOT NULL,                -- 'Downstairs' | 'Upstairs'
    capacity         INTEGER NOT NULL DEFAULT 4,   -- Maximum occupancy
    is_active        INTEGER NOT NULL DEFAULT 1,   -- 0 = decommissioned room
    server_updated_at TEXT NOT NULL                -- Delta-sync cursor
);
"""


# =============================================================================
# Table 3: local_allocations
# =============================================================================
# A synced cache of RoomAllocation records for the current academic session.
# Allows the Hostel Porter to answer "which room is this student in?"
# without a network call.
#
# Only the CURRENT session's allocations are kept on-device to conserve
# storage. Historical allocations live only in the cloud.
# =============================================================================
CREATE_LOCAL_ALLOCATIONS_SQL = """
CREATE TABLE IF NOT EXISTS local_allocations (
    allocation_id    TEXT PRIMARY KEY NOT NULL,
    user_id          TEXT NOT NULL,                -- FK → local_users.user_id
    room_id          TEXT NOT NULL,                -- FK → local_rooms.room_id
    academic_session TEXT NOT NULL,                -- e.g., '2025/2026'
    check_in_date    TEXT,                         -- ISO 8601 or NULL
    server_updated_at TEXT NOT NULL                -- Delta-sync cursor
);

-- Composite index for fast "what room is user X in for session Y?" queries
CREATE INDEX IF NOT EXISTS idx_local_alloc_user_session
    ON local_allocations (user_id, academic_session);
"""


# =============================================================================
# Table 4: pending_sync_logs
# =============================================================================
# THE OFFLINE EVENT QUEUE — the heart of the offline-first design.
#
# When the tablet has no internet connectivity, every scan event is written
# here instead of being sent directly to the cloud. The record structure
# mirrors the cloud `access_logs` table exactly (same UUID PK strategy)
# so the Phase 2 batch upload requires zero transformation.
#
# IDEMPOTENCY:
#   The log_id UUID is generated by the tablet at scan time. If the same
#   batch is re-uploaded (e.g., after a retry), the cloud's
#   INSERT ... ON CONFLICT DO NOTHING prevents duplicates.
#
# sync_status LIFECYCLE:
#   PENDING    → Initial state when created offline
#   UPLOADING  → Background sync manager has picked this up for upload
#   SYNCED     → Cloud confirmed receipt (202 Accepted received)
#   FAILED     → Upload failed after max retries (manual review needed)
# =============================================================================
CREATE_PENDING_SYNC_LOGS_SQL = """
CREATE TABLE IF NOT EXISTS pending_sync_logs (
    -- UUID generated by the tablet BEFORE being online.
    -- Matches the cloud access_logs.log_id for idempotent sync.
    log_id                TEXT PRIMARY KEY NOT NULL,

    -- Core scan event fields (mirror cloud access_logs schema)
    user_id               TEXT NOT NULL,           -- Scanned student's UUID
    guard_id              TEXT NOT NULL,            -- Executing officer's UUID
    location              TEXT NOT NULL,            -- 'Main Gate' | 'David House' | etc.
    action_type           TEXT NOT NULL,            -- 'Entry' | 'Exit' | 'Luggage_Cleared' | ...
    scan_timestamp        TEXT NOT NULL,            -- ISO 8601 with timezone (tablet clock)

    -- Optional fields
    luggage_photo_url     TEXT,                     -- URL of open-bag photo (nullable)
    luggage_verified      INTEGER,                  -- 1=verified, 0=not, NULL=N/A
    exit_token_expires_at TEXT,                     -- ISO 8601 | NULL
    notes                 TEXT,                     -- Guard's optional note
    extra_data            TEXT,                     -- JSON blob for future metadata

    -- Sync tracking (tablet-side only — not sent to cloud)
    sync_status           TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|UPLOADING|SYNCED|FAILED
    created_at            TEXT NOT NULL,            -- When the tablet recorded this event
    retry_count           INTEGER NOT NULL DEFAULT 0  -- Number of failed upload attempts
);

-- Index for fast queue drain: fetch all PENDING records ordered by scan time
CREATE INDEX IF NOT EXISTS idx_pending_logs_status
    ON pending_sync_logs (sync_status, scan_timestamp);
"""


# =============================================================================
# Table 5: sync_metadata
# =============================================================================
# Stores the tablet's sync state — one row per synced table.
# The pull-sync engine uses `last_synced_at` to compute the `?since=`
# parameter for the next incremental pull, ensuring only NEW data is fetched.
#
# Also stores the `tablet_id` (the device's own UUID), which is included
# in every batch upload so the cloud can track which device uploaded what.
# =============================================================================
CREATE_SYNC_METADATA_SQL = """
CREATE TABLE IF NOT EXISTS sync_metadata (
    -- The table name this row tracks (e.g., 'users', 'rooms', 'allocations')
    table_name       TEXT PRIMARY KEY NOT NULL,

    -- The ISO 8601 UTC timestamp of the last SUCCESSFUL sync for this table.
    -- On the next pull, the tablet sends ?since=<this value> to get only
    -- records updated after this point.
    last_synced_at   TEXT,                          -- NULL = never synced (triggers bootstrap)

    -- Total records currently cached in the local_* table
    record_count     INTEGER NOT NULL DEFAULT 0,

    -- The device's own UUID. Set once on first launch.
    -- Included in every batch upload payload as `tablet_id`.
    tablet_id        TEXT NOT NULL
);

-- Seed the three tracked tables on schema creation.
-- The tablet_id placeholder ('UNSET') is replaced by the Flutter app
-- on first launch using a generated UUID stored in secure device storage.
INSERT OR IGNORE INTO sync_metadata (table_name, last_synced_at, record_count, tablet_id)
VALUES
    ('users',       NULL, 0, 'UNSET'),
    ('rooms',       NULL, 0, 'UNSET'),
    ('allocations', NULL, 0, 'UNSET');
"""


# =============================================================================
# CREATE_ALL_SQL
# =============================================================================
# A single SQL string containing all 5 table definitions in dependency order.
#
# FLUTTER USAGE:
#   In your Flutter sqflite `onCreate` callback, execute this string:
#
#   ```dart
#   import 'package:jabu_sams/db/schema.dart'; // Generated from this file
#
#   Future<void> _onCreate(Database db, int version) async {
#     for (final statement in kCreateAllSql.split(';')) {
#       final trimmed = statement.trim();
#       if (trimmed.isNotEmpty) {
#         await db.execute(trimmed);
#       }
#     }
#   }
#   ```
#
# SERVER USAGE:
#   Import this module to read schema constants for documentation,
#   testing, or generating migration scripts.
# =============================================================================
CREATE_ALL_SQL = "\n\n".join([
    CREATE_LOCAL_USERS_SQL,
    CREATE_LOCAL_ROOMS_SQL,
    CREATE_LOCAL_ALLOCATIONS_SQL,
    CREATE_PENDING_SYNC_LOGS_SQL,
    CREATE_SYNC_METADATA_SQL,
])


# =============================================================================
# Schema Version
# =============================================================================
# Increment this when any table definition changes.
# The Flutter app compares this against its stored version and
# runs ALTER TABLE / DROP+RECREATE migrations accordingly.
SCHEMA_VERSION = 1
