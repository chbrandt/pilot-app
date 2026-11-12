"""
app/api_client.py — Thin HTTP client for calling the Pilot API from the GUI.

The GUI (pilot-app) is a pure HTTP client of the API service (pilot-api):
it uses this module instead of importing any backend code, so every
operation — including login verification — goes over HTTP.

Configuration
-------------
API_BASE_URL   Base URL of the REST API (default: ``http://localhost:5000``).
               Override with the ``API_BASE_URL`` environment variable when
               running the API on a different host/port.

Usage
-----
    from app.api_client import api_get, api_post, api_delete

    jobs   = api_get("/api/jobs")
    result = api_post("/api/jobs", {"name": "my-job", "image": "ubuntu:22.04", "node_name": "vk-1"})
    result = api_delete("/api/jobs/my-job")

All helpers forward the Bearer token stored in the Flask session so that the
API's ``require_token`` decorator is satisfied transparently.

Errors
------
``requests.HTTPError`` is raised for 4xx/5xx responses.  Route handlers in
``app/`` should catch it and translate it to flash messages with the
shared :func:`api_error` helper defined in this module.
"""

import logging
import os

import requests
from flask import session

logger = logging.getLogger(__name__)

# Base URL of the Pilot API service (pilot-api). In-cluster the deploy
# chart sets this to the api Service; locally it defaults to localhost:5000.
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:5000")

# Read timeouts (seconds) for calls from the GUI layer to the REST API.
#
# DEFAULT_TIMEOUT covers ordinary JSON endpoints (job lists, status polls,
# namespace ensure, …).  LONG_TIMEOUT covers operations that block on slow
# remote work — Helm ``install --wait`` (up to ~5 min) and the HPC deploy
# sequence (several mccli/SSH steps: pip installs, binary downloads, …).
#
# LONG_TIMEOUT must stay *below* the Ingress ``proxy-read-timeout`` (360 s in
# the chart) so the browser connection is not cut off before the GUI can
# render a result page.
DEFAULT_TIMEOUT = 60
LONG_TIMEOUT = 300


# ── Helpers ───────────────────────────────────────────────────────────


def api_error(exc: requests.HTTPError) -> str:
    """
    Extract a human-readable error message from a ``requests.HTTPError``.

    GUI route handlers use this to translate API error responses into flash
    messages; the single shared implementation lives here, next to the
    client that raises the error.

    Args:
        exc: The raised ``requests.HTTPError``.  Its ``response`` usually
            carries a JSON body with an ``error`` key, but may be anything.

    Returns:
        The API's ``error`` field when present, otherwise ``str(exc)``.
    """
    try:
        return exc.response.json().get("error", str(exc))
    except Exception:
        return str(exc)


def _headers() -> dict:
    """Build request headers, injecting the current session Bearer token."""
    token = session.get("token", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ── Public interface ──────────────────────────────────────────────────


def api_get(path: str, timeout: int = DEFAULT_TIMEOUT, params: dict | None = None) -> dict | list:
    """
    Send a GET request to the API.

    Parameters
    ----------
    path : str
        Path relative to ``API_BASE_URL``, e.g. ``"/api/jobs"``.
    timeout : int
        Read timeout in seconds (default :data:`DEFAULT_TIMEOUT`).
    params : dict, optional
        URL query parameters appended to the request.

    Returns
    -------
    dict | list
        Parsed JSON response body.

    Raises
    ------
    requests.HTTPError
        On 4xx / 5xx responses.
    """
    url = f"{API_BASE_URL}{path}"
    logger.debug("API GET %s", url)
    r = requests.get(url, headers=_headers(), timeout=timeout, params=params)
    r.raise_for_status()
    return r.json()


def api_post(path: str, body: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """
    Send a POST request to the API.

    Parameters
    ----------
    path : str
        Path relative to ``API_BASE_URL``, e.g. ``"/api/interlink"``.
    body : dict, optional
        JSON-serialisable request body.
    timeout : int
        Read timeout in seconds.  Use :data:`LONG_TIMEOUT` for long-running
        operations (Helm ``install --wait``, HPC deploy).

    Returns
    -------
    dict
        Parsed JSON response body.

    Raises
    ------
    requests.HTTPError
        On 4xx / 5xx responses.
    """
    url = f"{API_BASE_URL}{path}"
    logger.debug("API POST %s body=%s", url, body)
    r = requests.post(url, json=body or {}, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()


def api_delete(path: str, body: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """
    Send a DELETE request to the API.

    Parameters
    ----------
    path : str
        Path relative to ``API_BASE_URL``, e.g. ``"/api/jobs/my-job"``.
    body : dict, optional
        JSON-serialisable request body (e.g. ``{"hpc_name": "test-echo"}``).
    timeout : int
        Read timeout in seconds (default :data:`DEFAULT_TIMEOUT`).

    Returns
    -------
    dict
        Parsed JSON response body.

    Raises
    ------
    requests.HTTPError
        On 4xx / 5xx responses.
    """
    url = f"{API_BASE_URL}{path}"
    logger.debug("API DELETE %s body=%s", url, body)
    r = requests.delete(url, json=body, headers=_headers(), timeout=timeout)
    r.raise_for_status()
    return r.json()
