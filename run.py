# =============================================================================
# JABU-SAMS | run.py
# Application Entry Point.
#
# Usage:
#   Development:  python run.py
#   Flask CLI:    flask run
#   Migrations:   flask db init / flask db migrate / flask db upgrade
# =============================================================================

import os
from app import create_app

# Create the app using the environment specified by FLASK_ENV
app = create_app(os.environ.get("FLASK_ENV", "development"))

if __name__ == "__main__":
    # Run the Flask development server.
    # In production, this is replaced by a WSGI server (e.g., Gunicorn):
    #   gunicorn "app:create_app()" --workers 4 --bind 0.0.0.0:5000
    app.run(
        host="0.0.0.0",     # Bind to all interfaces (accessible on local network)
        port=5000,
        debug=app.config.get("DEBUG", False),
        use_reloader=True,  # Auto-reload on code changes in development
    )
