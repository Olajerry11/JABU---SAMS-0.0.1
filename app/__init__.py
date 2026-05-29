# =============================================================================
# JABU-SAMS | app/__init__.py
# The Flask Application Factory.
#
# PATTERN: Application Factory
# Instead of creating the Flask app at module level (which causes circular
# import issues and makes testing impossible), we define a create_app()
# function. This lets us create multiple isolated app instances — one for
# development, one for testing, one for production — each with its own config.
# =============================================================================

import os
import logging

from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_admin import Admin
from flask_cors import CORS

from app.config import CONFIG_MAP, ProductionConfig

# =============================================================================
# Extension Instances
# =============================================================================
# These are created here at the module level WITHOUT being bound to any
# specific Flask app. The actual binding happens inside create_app() via
# the extension's .init_app(app) pattern.
# This is the standard Flask approach that prevents circular imports.
# =============================================================================

# SQLAlchemy ORM — the database interface layer
db = SQLAlchemy()

# Flask-Migrate — Alembic wrapper for schema versioning
migrate = Migrate()

# Flask-JWT-Extended — handles access/refresh token lifecycle
jwt = JWTManager()

# Flask-Admin — auto-generates CRUD admin UI from SQLAlchemy models
admin = Admin(name="JABU-SAMS Admin", template_mode="bootstrap4")

# ---------------------------------------------------------------------------
# Phase 2 — Celery (Async Sync Engine)
# ---------------------------------------------------------------------------
# The Celery instance is declared here at module level so it can be imported
# by the worker process (`celery -A app.celery_worker.celery_app worker`).
# It is re-configured inside create_app() to bind Flask's application context
# so tasks can safely access `db.session` and all SQLAlchemy models.
# ---------------------------------------------------------------------------
from app.celery_worker import make_celery

# Module-level Celery instance (unbound to Flask app until create_app() runs)
celery_app = make_celery()


# =============================================================================
# Application Factory
# =============================================================================

def create_app(config_name: str = None) -> Flask:
    """
    Flask Application Factory.

    Creates, configures, and returns the Flask application instance.
    All extensions, blueprints, and error handlers are registered here.

    Args:
        config_name (str): Key into CONFIG_MAP ('development', 'testing',
                           'production'). Defaults to the FLASK_ENV
                           environment variable, or 'development' if unset.

    Returns:
        Flask: A fully configured, ready-to-run Flask application instance.
    """

    # -------------------------------------------------------------------------
    # 1. Resolve Configuration
    # -------------------------------------------------------------------------
    # Priority: explicit argument > FLASK_ENV env var > 'default'
    if config_name is None:
        config_name = os.environ.get("FLASK_ENV", "default").lower()

    config_class = CONFIG_MAP.get(config_name, CONFIG_MAP["default"])

    # Safety gate: if running in production, validate all required env vars
    if config_name == "production":
        ProductionConfig.validate()

    # -------------------------------------------------------------------------
    # 2. Create & Configure the Flask App
    # -------------------------------------------------------------------------
    app = Flask(
        __name__,
        # instance_path holds .env, secrets, and SQLite dev DBs — kept outside
        # source control
        instance_relative_config=False,
    )

    # Load all config attributes from the selected configuration class
    app.config.from_object(config_class)

    # -------------------------------------------------------------------------
    # 3. Configure Logging
    # -------------------------------------------------------------------------
    # Set up structured logging so all log entries include timestamps,
    # log level, and the originating module name.
    logging.basicConfig(
        level=logging.DEBUG if app.config.get("DEBUG") else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    app.logger.info(
        f"JABU-SAMS starting in '{config_name}' mode | "
        f"DB: {app.config['SQLALCHEMY_DATABASE_URI']}"
    )

    # -------------------------------------------------------------------------
    # 4. Initialize Extensions
    # -------------------------------------------------------------------------
    # Each extension is bound to this specific app instance here.

    # Database ORM
    db.init_app(app)

    # Migrations (must come after db)
    migrate.init_app(app, db)

    # JWT Manager
    jwt.init_app(app)

    # CORS — allow requests from Flutter mobile clients and web admin panel.
    # Restrict origins in production via CORS_ORIGINS env var.
    CORS(
        app,
        resources={
            r"/api/*": {
                "origins": os.environ.get("CORS_ORIGINS", "*"),
                "methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
                "allow_headers": ["Authorization", "Content-Type"],
            }
        },
    )

    # -------------------------------------------------------------------------
    # 5. Register Admin Panel Views
    # -------------------------------------------------------------------------
    # Import models here (inside the factory) to avoid circular imports.
    # Admin views are registered after db is initialized and bound to app.
    with app.app_context():
        from app.models.core import (
            User,
            Room,
            RoomAllocation,
            AccessLog,
        )
        from app.admin_views import (
            register_admin_views,
        )

        # Bind admin to this app instance
        admin.init_app(app, url=app.config.get("ADMIN_URL_PREFIX", "/sams-admin"))

        # Register all model views onto the admin panel
        register_admin_views(admin, db)

    # -------------------------------------------------------------------------
    # 5b. Initialize Celery with Flask App Context (Phase 2)
    # -------------------------------------------------------------------------
    # Re-create the Celery instance bound to THIS Flask app instance.
    # This replaces the module-level unbound `celery_app` with a context-aware
    # version. The global reference is updated so that tasks imported anywhere
    # in the application use the Flask-bound instance.
    #
    # Why re-create instead of just calling .init_app()?
    # Celery has no official init_app() pattern. The Flask-binding is achieved
    # by subclassing celery.Task with a __call__ that wraps execution in
    # `with app.app_context()`. make_celery(app) does exactly this.
    global celery_app
    celery_app = make_celery(app)
    app.logger.info("[Phase 2] Celery async engine initialized and bound to Flask app context.")

    # -------------------------------------------------------------------------
    # 6. Register API Blueprints
    # -------------------------------------------------------------------------
    # Blueprints partition the API into logical domains.
    # Each blueprint is imported and registered with a URL prefix.
    with app.app_context():
        from app.api.auth import auth_bp
        from app.api.users import users_bp
        from app.api.access_logs import access_logs_bp
        from app.api.rooms import rooms_bp
        # Phase 2 — Async Sync Engine: upload, status polling, health check
        from app.api.sync import sync_bp
        # Phase 3 — Edge Pull-Sync: tablet incremental delta downloads
        from app.api.pull_sync import pull_sync_bp

        app.register_blueprint(auth_bp,         url_prefix="/api/v1/auth")
        app.register_blueprint(users_bp,        url_prefix="/api/v1/users")
        app.register_blueprint(rooms_bp,        url_prefix="/api/v1/rooms")
        app.register_blueprint(access_logs_bp,  url_prefix="/api/v1/logs")
        # Phase 2: POST /upload, GET /status/<id>, GET /health
        app.register_blueprint(sync_bp,         url_prefix="/api/v1/sync")
        # Phase 3: GET /pull/users, /pull/rooms, /pull/allocations, /pull/bootstrap
        app.register_blueprint(pull_sync_bp,    url_prefix="/api/v1/sync")

    # -------------------------------------------------------------------------
    # 7. Register JWT Error Handlers
    # -------------------------------------------------------------------------
    # Provide structured JSON error responses for all JWT-related failures
    # instead of the default HTML error pages.

    @jwt.expired_token_loader
    def expired_token_callback(jwt_header, jwt_payload):
        return {
            "error": "TOKEN_EXPIRED",
            "message": "Your session has expired. Please log in again."
        }, 401

    @jwt.invalid_token_loader
    def invalid_token_callback(error_string):
        return {
            "error": "INVALID_TOKEN",
            "message": f"Token validation failed: {error_string}"
        }, 422

    @jwt.unauthorized_loader
    def missing_token_callback(error_string):
        return {
            "error": "AUTHORIZATION_REQUIRED",
            "message": "A valid Bearer token is required to access this resource."
        }, 401

    @jwt.revoked_token_loader
    def revoked_token_callback(jwt_header, jwt_payload):
        return {
            "error": "TOKEN_REVOKED",
            "message": "This token has been revoked. Please log in again."
        }, 401

    # -------------------------------------------------------------------------
    # 8. Register General HTTP Error Handlers
    # -------------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(e):
        return {"error": "NOT_FOUND", "message": str(e)}, 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        return {"error": "METHOD_NOT_ALLOWED", "message": str(e)}, 405

    @app.errorhandler(500)
    def internal_error(e):
        app.logger.exception("Internal Server Error")
        return {"error": "INTERNAL_SERVER_ERROR", "message": "An unexpected error occurred."}, 500

    # -------------------------------------------------------------------------
    # 9. Shell Context (flask shell)
    # -------------------------------------------------------------------------
    # Makes `db` and all models available automatically in `flask shell`
    # without needing manual imports. Invaluable for debugging.

    @app.shell_context_processor
    def make_shell_context():
        from app.models.core import User, Room, RoomAllocation, AccessLog
        return {
            "db": db,
            "User": User,
            "Room": Room,
            "RoomAllocation": RoomAllocation,
            "AccessLog": AccessLog,
        }

    app.logger.info("JABU-SAMS application factory complete. All components initialized.")
    return app
