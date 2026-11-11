"""
app/auth.py — Flask session-based authentication for the Web GUI layer.

Handles:
- Login / logout routes (HTML form with token paste)
- EGI Check-in OAuth authorization-code login (GA4)
- Session helpers (get_session_user)
- require_login route decorator

The GUI is a pure HTTP client: token validation, group-access checks and
the OIDC code→token exchange all happen in the Pilot API
(``POST /api/auth/verify`` and ``POST /api/auth/oidc/exchange``); the OIDC
client secret never reaches this service.
"""

import base64
import hashlib
import logging
import secrets
import time
from functools import wraps
from typing import Optional
from urllib.parse import urlencode

import requests
from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.api_client import api_get, api_post

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

# Default EGI Check-in issuer (Keycloak realm). Override via site.oidc.issuer.
_DEFAULT_ISSUER = "https://aai.egi.eu/auth/realms/egi"


# ── Session helpers ───────────────────────────────────────────────────


def get_session_user() -> Optional[dict]:
    """
    Return current authenticated user info from the Flask session.

    Returns None if:
    - No token is stored in the session, or
    - The stored token's 'exp' claim is in the past.
    """
    claims = session.get("claims")
    if not claims:
        return None
    exp = claims.get("exp", 0)
    if time.time() > exp:
        return None
    return {
        "sub": claims.get("sub", ""),
        "namespace": session.get("namespace", ""),
        "exp": exp,
        "iss": claims.get("iss", ""),
    }


def require_login(f):
    """
    Flask route decorator that enforces session authentication.

    - HTML requests: redirects to /login with a flash message.
    - JSON / AJAX requests: returns HTTP 401 with a JSON error body.
    """
    import json

    @wraps(f)
    def decorated(*args, **kwargs):
        user = get_session_user()
        if user is None:
            is_json = (
                "application/json" in request.headers.get("Accept", "")
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            )
            if is_json:
                return (
                    json.dumps({"error": "Authentication required", "code": 401}),
                    401,
                    {"Content-Type": "application/json"},
                )
            flash("Please log in with your EGI Check-in access token.", "error")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated


# ── Routes ────────────────────────────────────────────────────────────


def _api_error(exc: requests.HTTPError) -> str:
    """Extract a human-readable message from an HTTPError response."""
    try:
        return exc.response.json().get("error", str(exc))
    except Exception:
        return str(exc)


def _oidc_config() -> dict:
    """
    Return the OIDC client configuration as published by the API's public
    ``GET /api/site`` endpoint, or an empty dict when unavailable.

    Keys: ``client_id`` and ``issuer`` (display-safe only).  The OIDC
    client secret never reaches the GUI: the code→token exchange happens
    inside the API (``POST /api/auth/oidc/exchange``).
    """
    try:
        return api_get("/api/site").get("oidc") or {}
    except Exception as exc:
        logger.warning("Could not fetch the OIDC configuration: %s", exc)
        return {}


def _establish_session(token: str, next_url: str):
    """
    Verify *token* through the Pilot API (``POST /api/auth/verify``) and
    set up the Flask session.

    Returns a redirect response on success, or ``None`` on failure (a
    flash message describing the error has already been queued).
    """
    try:
        result = api_post("/api/auth/verify", {"access_token": token})
    except requests.HTTPError as exc:
        flash(f"Login failed: {_api_error(exc)}", "error")
        return None
    except Exception as exc:
        logger.error("Login verification failed: %s", exc)
        flash(
            "Login failed: could not reach the API (is it running?).",
            "error",
        )
        return None

    return _finalize_session(
        token=result.get("token", token),
        claims=result.get("claims") or {},
        namespace=result.get("namespace", ""),
        next_url=next_url,
    )


def _finalize_session(
    token: str, claims: dict, namespace: str, next_url: Optional[str]
):
    """
    Store API-validated credentials in the Flask session and bootstrap
    the user's namespace / default configs.

    Returns a redirect response on success, or ``None`` when the API
    response was incomplete.
    """
    if not token or not namespace or "sub" not in claims:
        logger.error("Incomplete login response from the API.")
        flash("Login failed: incomplete response from the API.", "error")
        return None

    sub = claims["sub"]

    # Store validated credentials in the session
    session.clear()
    session["token"] = token
    session["claims"] = claims
    session["namespace"] = namespace

    # Auto-create the user's namespace if it doesn't exist yet (via API)
    try:
        result = api_post("/api/userspace/")
        if result.get("created"):
            logger.info(
                "Auto-created namespace '%s' for %s...", namespace, sub[:20]
            )
    except Exception as exc:
        # Non-fatal: namespace may be created on first deploy
        logger.warning("Namespace pre-creation skipped: %s", exc)

    # Seed global default chart configs (e.g. interlink) for this user
    try:
        api_post("/api/saved/seed")
    except Exception as exc:
        logger.warning("Could not seed default chart configs: %s", exc)

    flash(f"Welcome! Your namespace is {namespace}.", "success")
    return redirect(next_url or url_for("app_k8s.home"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    """Login page: accepts and validates an EGI Check-in access token."""
    if request.method == "POST":
        token = request.form.get("token", "").strip()
        if not token:
            flash("Please paste your EGI Check-in access token.", "error")
            return redirect(url_for("auth.login"))

        next_url = request.form.get("next") or url_for("app_k8s.home")
        result = _establish_session(token, next_url)
        return result if result is not None else redirect(url_for("auth.login"))

    # GET — render the login form
    reason = request.args.get("reason")   # "expired"
    refresh = request.args.get("refresh")  # "1"
    next_url = request.args.get("next", "")
    oidc_enabled = bool(_oidc_config().get("client_id"))
    return render_template(
        "login.html",
        reason=reason,
        refresh=refresh,
        next_url=next_url,
        oidc_enabled=oidc_enabled,
    )


# ── EGI Check-in OAuth authorization-code flow (GA4) ──────────────────


def _pkce_challenge(code_verifier: str) -> str:
    """
    Compute the ``S256`` PKCE code challenge (RFC 7636) for *code_verifier*.

    Returns the base64url-encoded SHA-256 digest of the verifier, without
    padding, as required by the ``S256`` method.
    """
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@auth_bp.route("/login/egi")
def egi_login():
    """
    Redirect the user to the EGI Check-in (Keycloak) authorization endpoint.

    Requires an OIDC client registered in EGI Check-in with this manager's
    ``/oauth/callback`` as an allowed redirect URI; configure it under
    ``site.oidc`` in pilot_config.yaml.

    The request carries a random CSRF ``state`` and a PKCE (RFC 7636, S256)
    ``code_challenge``; the matching verifier is kept in the session and
    sent to the token endpoint by :func:`egi_callback`. EGI Check-in
    clients are registered with PKCE required, so the flow fails without
    them.
    """
    oidc = _oidc_config()
    client_id = oidc.get("client_id")
    if not client_id:
        flash(
            "EGI Check-in login is not configured. "
            "Please paste an access token instead.",
            "error",
        )
        return redirect(url_for("auth.login"))

    issuer = oidc.get("issuer", _DEFAULT_ISSUER).rstrip("/")
    redirect_uri = oidc.get("redirect_uri") or (
        request.host_url.rstrip("/") + url_for("auth.egi_callback")
    )

    # CSRF protection: random state echoed back by the IdP
    state = secrets.token_urlsafe(32)
    session["oidc_state"] = state
    session["oidc_next"] = request.args.get("next", "")

    # PKCE (RFC 7636, S256): EGI Check-in rejects authorization requests
    # without a code_challenge/code_challenge_method pair.
    code_verifier = secrets.token_urlsafe(64)
    session["oidc_code_verifier"] = code_verifier

    authorize_url = (
        f"{issuer}/protocol/openid-connect/auth?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid",
                "state": state,
                "code_challenge": _pkce_challenge(code_verifier),
                "code_challenge_method": "S256",
            }
        )
    )
    return redirect(authorize_url)


@auth_bp.route("/oauth/callback")
def egi_callback():
    """
    Exchange the authorization code for a session via the Pilot API.

    Verifies the ``state`` parameter against the one stored by
    :func:`egi_login` (CSRF protection) and, when present, the RFC 9207
    ``iss`` parameter against the configured issuer (IdP mix-up
    protection), then sends the code and the PKCE ``code_verifier`` to the
    API's ``POST /api/auth/oidc/exchange`` — the API performs the token
    exchange with the IdP (the client secret stays server-side) — and
    establishes the session with the API-validated credentials.
    """
    oidc = _oidc_config()
    client_id = oidc.get("client_id")
    if not client_id:
        flash("EGI Check-in login is not configured.", "error")
        return redirect(url_for("auth.login"))

    # ── Verify state (CSRF) ───────────────────────────────────────────
    expected_state = session.pop("oidc_state", None)
    next_url = session.pop("oidc_next", "") or None
    received_state = request.args.get("state", "")
    if not expected_state or expected_state != received_state:
        logger.warning("OAuth state mismatch — possible CSRF attempt.")
        flash("Login failed: invalid state. Please try again.", "error")
        return redirect(url_for("auth.login"))

    # ── Verify the issuer (RFC 9207, IdP mix-up protection) ──────────
    issuer = oidc.get("issuer", _DEFAULT_ISSUER).rstrip("/")
    received_iss = request.args.get("iss", "")
    if received_iss and received_iss.rstrip("/") != issuer:
        logger.warning(
            "OAuth issuer mismatch: expected %s, got %s.", issuer, received_iss
        )
        flash("Login failed: unexpected issuer. Please try again.", "error")
        return redirect(url_for("auth.login"))

    code = request.args.get("code", "")
    if not code:
        flash(
            f"Login failed: no authorization code "
            f"({request.args.get('error', 'unknown error')}).",
            "error",
        )
        return redirect(url_for("auth.login"))

    # ── PKCE verifier (matches the challenge sent by /login/egi) ─────
    code_verifier = session.pop("oidc_code_verifier", None)
    if not code_verifier:
        flash("Login failed: login session expired. Please try again.", "error")
        return redirect(url_for("auth.login"))

    # ── Exchange the code for tokens (server-side, in the API) ─────────
    redirect_uri = oidc.get("redirect_uri") or (
        request.host_url.rstrip("/") + url_for("auth.egi_callback")
    )

    try:
        result = api_post(
            "/api/auth/oidc/exchange",
            {
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
            },
        )
    except requests.HTTPError as exc:
        logger.error("OAuth exchange failed: %s", _api_error(exc))
        flash(f"Login failed: {_api_error(exc)}", "error")
        return redirect(url_for("auth.login"))
    except Exception as exc:
        logger.error("OAuth exchange failed: %s", exc)
        flash("Login failed: could not exchange the authorization code.", "error")
        return redirect(url_for("auth.login"))

    # Establish the session with the API-validated credentials
    result = _finalize_session(
        token=result.get("token", ""),
        claims=result.get("claims") or {},
        namespace=result.get("namespace", ""),
        next_url=next_url,
    )
    return result if result is not None else redirect(url_for("auth.login"))


@auth_bp.route("/logout")
def logout():
    """Clear the session and redirect to the login page."""
    reason = request.args.get("reason")
    session.clear()
    if reason == "expired":
        flash(
            "Your session has expired. Please paste a new token to continue.", "error"
        )
    else:
        flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
