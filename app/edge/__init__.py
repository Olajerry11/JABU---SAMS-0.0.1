# =============================================================================
# JABU-SAMS | app/edge/__init__.py
# The Edge Database Package.
#
# This package contains everything related to the TABLET side of JABU-SAMS:
#
#   sqlite_schema.py  — The canonical SQLite DDL for the guard tablet's
#                       local database. The single source of truth for the
#                       tablet's offline data structure.
#
#   validators.py     — The offline TOTP validation and RBAC access-rule
#                       engine. Tablets run this logic locally with no
#                       network call required.
#
# The pull-sync API endpoints that SERVE data to tablets live in:
#   app/api/pull_sync.py
# =============================================================================
