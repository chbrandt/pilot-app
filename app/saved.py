"""
app/saved.py — Web GUI routes for saved deployment configuration management.

All operations go through the Pilot API (``app.api_client``); the GUI holds
no local saved-config store of its own.

Routes
------
POST /saved/<config_id>/delete   Remove a saved configuration entry
"""

import logging

import requests
from flask import Blueprint, flash, redirect, url_for

from app.auth import require_login
from app.api_client import api_delete, api_error, api_get

logger = logging.getLogger(__name__)

saved_bp = Blueprint("app_saved", __name__)


@saved_bp.route("/saved/<config_id>/delete", methods=["POST"])
@require_login
def delete_saved_config(config_id):
    """Remove a saved deployment configuration."""
    # Read the entry first (for its label + kind), then delete it via the API.
    entry = None
    try:
        entry = api_get(f"/api/saved/{config_id}")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            flash("Saved configuration not found.", "error")
            return redirect(url_for("app_k8s.index"))
        flash(f"Could not read saved configuration: {api_error(exc)}", "error")
        return redirect(url_for("app_k8s.index"))
    except Exception as exc:
        logger.error("Reading saved configuration failed: %s", exc)
        flash(f"Could not read saved configuration: {exc}", "error")
        return redirect(url_for("app_k8s.index"))

    label = (entry.get("name") or entry.get("release_name") or config_id) if entry else config_id

    try:
        api_delete(f"/api/saved/{config_id}")
        flash(f"Saved configuration '{label}' removed.", "success")
    except requests.HTTPError as exc:
        flash(f"Could not remove saved configuration: {api_error(exc)}", "error")
    except Exception as exc:
        logger.error("Removing saved configuration failed: %s", exc)
        flash(f"Could not remove saved configuration: {exc}", "error")

    # Redirect back to the appropriate deploy form
    kind = entry.get("kind") if entry else None
    if kind == "helm":
        return redirect(url_for("app_helm.releases"))
    return redirect(url_for("app_k8s.index"))

