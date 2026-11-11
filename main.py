"""
Pilot App — Flask entry point for the HPC Pilot web GUI.

The GUI is a pure HTTP client of the Pilot API (the ``pilot-api``
repository): every backend operation — job submission, node deployment,
saved configs, and even login verification / the OIDC code exchange —
goes through ``API_BASE_URL``.  No Kubernetes, Helm, or JWT logic lives
here.

Usage
-----
    # Run the GUI against a locally running API (pilot-api):
    export API_BASE_URL=http://localhost:5000
    python main.py

    # On a different port (e.g. sharing the machine with the API):
    FLASK_PORT=5001 python main.py

Environment variables
---------------------
API_BASE_URL      Base URL of the Pilot API (default: http://localhost:5000)
FLASK_PORT        Port to listen on (default: 5000)
FLASK_DEBUG       Set to "1" to enable debug mode
FLASK_SECRET_KEY  Flask session secret (change in production!)
"""

import logging
import os

from flask import Flask, jsonify

# ── Logging ───────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


def create_app() -> Flask:
    """
    Application factory for the web GUI.

    Creates and configures the Flask application, registers the GUI
    blueprints, exposes a public /health endpoint, and sets up the
    template context processor.
    """
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "app", "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "app", "static"),
    )
    app.secret_key = os.environ.get(
        "FLASK_SECRET_KEY", "dev-secret-change-in-production"
    )

    # ── GUI blueprints ────────────────────────────────────────────────
    from app.auth import auth_bp
    from app.k8s import k8s_bp
    from app.helm import helm_bp
    from app.hpc import hpc_bp
    from app.saved import saved_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(k8s_bp)
    app.register_blueprint(helm_bp)
    app.register_blueprint(hpc_bp)
    app.register_blueprint(saved_bp)

    # ── Public health endpoint (liveness probe, no auth) ───────────────
    @app.route("/health", methods=["GET"])
    def health():
        """Liveness probe — public, returns a simple JSON status."""
        return jsonify({"status": "Service alive"})

    # ── Context processor — injects current_user into every template ──
    @app.context_processor
    def inject_user():
        from app.auth import get_session_user

        return {"current_user": get_session_user()}

    return app


# ── Entry point ────────────────────────────────────────────────────────

app = create_app()

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)
    port = int(os.environ.get("FLASK_PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=debug)
