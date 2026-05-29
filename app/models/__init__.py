# =============================================================================
# JABU-SAMS | app/models/__init__.py
# Exposes all models from a single import point.
# =============================================================================

from app.models.core import User, Room, RoomAllocation, AccessLog

__all__ = ["User", "Room", "RoomAllocation", "AccessLog"]
