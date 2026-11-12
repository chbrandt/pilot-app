"""
conftest.py — shared pytest fixtures for Pilot App (GUI) tests.

The GUI is a pure HTTP client of the Pilot API (pilot-api): the ``app``
fixture therefore needs no Kubernetes/Kubeconfig stubbing at all — only a
testing-mode Flask application and a way to inject a fake logged-in
session (mirroring what ``app.auth._finalize_session`` writes after a
successful API login).
"""

import hashlib
import time
from typing import Optional
from unittest.mock import MagicMock

import pytest
import requests

# ---------------------------------------------------------------------------
# Flask application fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def app():
    """Create the GUI Flask application in testing mode."""
    from main import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    flask_app.config["SECRET_KEY"] = "test-secret"
    yield flask_app


@pytest.fixture()
def client(app):
    """Return a Flask test client."""
    return app.test_client()


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

FAKE_SUB = "test-user-sub-12345678"
FAKE_NAMESPACE = "user-" + hashlib.sha256(FAKE_SUB.encode()).hexdigest()[:16]


def make_fake_claims(sub: str = FAKE_SUB, exp_offset: int = 3600) -> dict:
    """Return a minimal fake JWT claims dict (as validated by the API)."""
    return {
        "sub": sub,
        "iss": "https://aai.egi.eu/auth/realms/egi",
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
    }


def login_client(client, sub: str = FAKE_SUB) -> str:
    """
    Inject a fake logged-in session so ``require_login`` passes.

    Returns the fake namespace stored in the session.  The session
    mirrors exactly what ``app.auth._finalize_session`` writes after a
    successful API login: ``token``, ``claims`` and ``namespace``.
    """
    namespace = "user-" + hashlib.sha256(sub.encode()).hexdigest()[:16]
    with client.session_transaction() as sess:
        sess["token"] = "fake-test-token"
        sess["claims"] = make_fake_claims(sub=sub)
        sess["namespace"] = namespace
    return namespace


@pytest.fixture()
def auth_headers(app, client):
    """
    Log the test client in and yield ``(headers, namespace)`` — the same
    tuple the old monorepo's test-suite yielded, for easy porting.
    """
    namespace = login_client(client)
    yield {"Authorization": "Bearer fake-test-token"}, namespace


# ---------------------------------------------------------------------------
# HTTP-error helper
# ---------------------------------------------------------------------------


def http_error(status_code: int, message: Optional[str] = None) -> requests.HTTPError:
    """
    Build a minimal ``requests.HTTPError`` carrying the given status code
    and a JSON ``{"error": ...}`` body.

    Shared by every GUI test file: it lets tests fake API error responses
    without any network access.  ``message`` defaults to ``"HTTP <code>"``.

    Args:
        status_code: The HTTP status the faked response carries.
        message: Optional body for the response's ``error`` field.

    Returns:
        A ``requests.HTTPError`` whose ``response.status_code``,
        ``response.text`` and ``response.json()`` behave as specified.
    """
    response = MagicMock()
    response.status_code = status_code
    response.text = message or f"HTTP {status_code}"
    response.json.return_value = {"error": message or f"HTTP {status_code}"}
    return requests.HTTPError(response=response)
