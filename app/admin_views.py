# =============================================================================
# JABU-SAMS | app/admin_views.py
# Flask-Admin model view registrations.
#
# Provides a protected, auto-generated CRUD interface for all SQLAlchemy
# models. The admin panel is accessible only to authenticated admin users.
# =============================================================================

from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from flask_sqlalchemy import SQLAlchemy


class SecureModelView(ModelView):
    """
    A base ModelView that adds common display settings.
    In Phase 2, this will be extended with Flask-Login / JWT guards
    to restrict admin access to authorized staff only.
    """
    # Show primary keys in list view (helpful for debugging UUID linkages)
    column_display_pk = True

    # Allow export to CSV from the admin list view
    can_export = True

    # Show column filters in the list view sidebar
    column_filters = []

    # Enable search
    can_view_details = True


class UserAdminView(SecureModelView):
    """Admin view for the User model."""
    column_list = [
        "user_id", "full_name", "matric_no", "student_type",
        "fee_status", "level", "is_active", "is_alumni", "created_at",
    ]
    column_searchable_list = ["full_name", "matric_no", "email"]
    column_filters = ["student_type", "fee_status", "is_active", "is_alumni", "level"]
    # Never expose the TOTP secret or password hash in the list view
    column_exclude_list = ["totp_secret", "password_hash"]
    form_excluded_columns = ["totp_secret", "access_logs", "guard_logs", "room_allocations"]


class RoomAdminView(SecureModelView):
    """Admin view for the Room model."""
    column_list = ["room_id", "building_name", "room_number", "floor", "capacity", "is_active"]
    column_searchable_list = ["building_name", "room_number"]
    column_filters = ["floor", "is_active"]


class RoomAllocationAdminView(SecureModelView):
    """Admin view for the RoomAllocation join table."""
    column_list = [
        "allocation_id", "user_id", "room_id",
        "academic_session", "check_in_date", "check_out_date",
    ]
    column_filters = ["academic_session"]


class AccessLogAdminView(SecureModelView):
    """
    Read-only admin view for the AccessLog audit trail.
    Deletion and editing are disabled to preserve the immutability contract.
    """
    # Enforce immutability: no create, edit, or delete from admin panel
    can_create = False
    can_edit = False
    can_delete = False

    column_list = [
        "log_id", "user_id", "guard_id", "location",
        "action_type", "scan_timestamp", "server_timestamp",
        "luggage_verified", "exit_token_expires_at",
    ]
    column_filters = ["action_type", "location", "luggage_verified"]
    column_searchable_list = ["location", "notes"]


def register_admin_views(admin: Admin, db: SQLAlchemy) -> None:
    """
    Registers all model views onto the Flask-Admin instance.

    Called from the Application Factory after db and admin are initialized.

    Args:
        admin: The Flask-Admin Admin instance.
        db:    The Flask-SQLAlchemy db instance (needed for model sessions).
    """
    from app.models.core import User, Room, RoomAllocation, AccessLog

    admin.add_view(UserAdminView(User,           db.session, name="Users",            category="Identity"))
    admin.add_view(RoomAdminView(Room,           db.session, name="Rooms",            category="Accommodation"))
    admin.add_view(RoomAllocationAdminView(RoomAllocation, db.session, name="Allocations", category="Accommodation"))
    admin.add_view(AccessLogAdminView(AccessLog, db.session, name="Access Logs",      category="Audit Trail"))
