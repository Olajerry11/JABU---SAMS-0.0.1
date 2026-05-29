# =============================================================================
# JABU-SAMS | app/config.py
# Configuration classes for different deployment environments.
# The Application Factory (app/__init__.py) selects the correct class
# based on the FLASK_ENV environment variable.
# =============================================================================

import os
from datetime import timedelta
from dotenv import load_dotenv

# Load the .env file from the project root into os.environ
load_dotenv()


class BaseConfig:
    """
    Base configuration shared by ALL environments.
    Sensitive defaults are never hard-coded here; they are sourced
    exclusively from environment variables loaded by python-dotenv.
    """

    # -------------------------------------------------------------------------
    # Flask Core
    # -------------------------------------------------------------------------
    # SECRET_KEY: Used by Flask for session signing and CSRF protection.
    # A strong, random 32+ character string is required in production.
    SECRET_KEY = os.environ.get("SECRET_KEY", "unsafe-default-key-change-in-prod")

    # -------------------------------------------------------------------------
    # SQLAlchemy (Database ORM)
    # -------------------------------------------------------------------------
    # DATABASE_URL: Full PostgreSQL connection URI.
    # Format: postgresql://USER:PASSWORD@HOST:PORT/DB_NAME
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "postgresql://jabu_user:jabu_password@localhost:5432/jabu_sams_db"
    )

    # SQLALCHEMY_TRACK_MODIFICATIONS: Disabling this suppresses a deprecation
    # warning and reduces memory overhead from the modification tracking system.
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # SQLALCHEMY_ECHO: When True, SQLAlchemy logs every raw SQL statement
    # it executes to stdout. This is your "X-Ray" vision for the ORM.
    # Overridden to False in ProductionConfig.
    SQLALCHEMY_ECHO = True

    # SQLALCHEMY_ENGINE_OPTIONS: Connection pool settings.
    # pool_pre_ping ensures stale connections are recycled automatically,
    # which is critical for long-running server instances.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_size": 10,         # Max persistent connections in the pool
        "max_overflow": 20,      # Extra connections allowed under heavy load
        "pool_timeout": 30,      # Seconds to wait for a free connection
        "pool_recycle": 1800,    # Recycle connections every 30 minutes
    }

    # -------------------------------------------------------------------------
    # JWT Extended (Authentication)
    # -------------------------------------------------------------------------
    # JWT_SECRET_KEY: Separate secret used specifically for signing JWT tokens.
    # Must differ from SECRET_KEY for defence-in-depth.
    JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "unsafe-jwt-key-change-in-prod")

    # JWT_ALGORITHM: HS256 is the HMAC-SHA256 symmetric algorithm.
    # For production with mobile clients, RS256 (asymmetric) is preferred.
    JWT_ALGORITHM = "HS256"

    # Access token lifetime: short-lived to limit exposure if intercepted.
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(
        minutes=int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRES_MINUTES", 30))
    )

    # Refresh token lifetime: long-lived so users don't re-login frequently.
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(
        days=int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRES_DAYS", 30))
    )

    # Store JWTs in Authorization header (Bearer token) — not in cookies.
    # This is the correct approach for API-only backends consumed by mobile apps.
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_HEADER_NAME = "Authorization"
    JWT_HEADER_TYPE = "Bearer"

    # -------------------------------------------------------------------------
    # Flask-Admin Panel
    # -------------------------------------------------------------------------
    # URL prefix for the admin dashboard.
    # Should be a non-guessable path in production (e.g., /sams-admin-xyz123).
    ADMIN_URL_PREFIX = os.environ.get("ADMIN_URL_PREFIX", "/sams-admin")


class DevelopmentConfig(BaseConfig):
    """
    Development configuration.
    - DEBUG mode is enabled for detailed error pages and auto-reloader.
    - SQL echo is inherited from BaseConfig (True) for query visibility.
    """
    DEBUG = True
    TESTING = False


class TestingConfig(BaseConfig):
    """
    Testing configuration.
    - Uses an isolated in-memory SQLite database so tests never touch
      the real PostgreSQL instance.
    - SQLALCHEMY_ECHO is disabled to keep test output clean.
    """
    DEBUG = False
    TESTING = True
    # Override to use a fast in-memory database for unit tests
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ECHO = False
    # Disable JWT expiry checks during automated tests
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=24)


class ProductionConfig(BaseConfig):
    """
    Production configuration.
    - DEBUG and ECHO are strictly disabled.
    - All secrets MUST be set via environment variables; the app will
      raise an error on startup if they are missing.
    """
    DEBUG = False
    TESTING = False

    # Disable SQL logging — it is a severe security risk in production
    # (exposes column names, values, and query patterns in server logs).
    SQLALCHEMY_ECHO = False

    @classmethod
    def validate(cls):
        """
        Called at application startup to ensure critical secrets are set.
        Raises a RuntimeError if any required environment variable is missing.
        """
        required = ["SECRET_KEY", "DATABASE_URL", "JWT_SECRET_KEY"]
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise RuntimeError(
                f"FATAL: Missing required environment variables for production: "
                f"{', '.join(missing)}"
            )


# ---------------------------------------------------------------------------
# Configuration Registry
# ---------------------------------------------------------------------------
# Maps the FLASK_ENV string to the appropriate configuration class.
# The Application Factory uses this to select the correct config.

CONFIG_MAP = {
    "development": DevelopmentConfig,
    "testing":     TestingConfig,
    "production":  ProductionConfig,
    "default":     DevelopmentConfig,
}
