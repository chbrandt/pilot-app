"""
tests/app/test_auth_app.py — GUI-layer tests for app.auth.

The GUI never validates JWTs locally: login (token-paste and the EGI
Check-in OAuth callback) is delegated to the Pilot API's public endpoints —

    POST /api/auth/verify           { access_token }
    POST /api/auth/oidc/exchange    { code, code_verifier, redirect_uri }

— and the OIDC client configuration (client_id / issuer only) comes from the
public ``GET /api/site`` endpoint.  All HTTP calls are patched here; no real
network access is required.
"""

import base64
import hashlib
from unittest.mock import MagicMock, patch

import requests

from tests.conftest import FAKE_NAMESPACE, FAKE_SUB, make_fake_claims

SITE_PATCH = "app.auth.api_get"      # app.auth's imported copy of api_get
API_POST_PATCH = "app.auth.api_post"  # app.auth's imported copy of api_post

FAKE_CLAIMS = make_fake_claims()
VERIFY_OK = {"token": "fake-token", "claims": FAKE_CLAIMS, "namespace": FAKE_NAMESPACE}
EXCHANGE_OK = {
    "token": "fake-access-token-from-oauth",
    "claims": FAKE_CLAIMS,
    "namespace": FAKE_NAMESPACE,
}

SITE_WITH_OIDC = {
    "hostname": "test.local",
    "default_image": "ubuntu:22.04",
    "wstunnel": {"port": 443, "local_port": 4000},
    "oidc": {
        "enabled": True,
        "client_id": "hpc-pilot",
        "issuer": "https://aai.egi.eu/auth/realms/egi",
    },
}
SITE_NO_OIDC = {
    "hostname": "test.local",
    "default_image": "ubuntu:22.04",
    "wstunnel": {"port": 443, "local_port": 4000},
    "oidc": {
        "enabled": False,
        "client_id": "",
        "issuer": "https://aai.egi.eu/auth/realms/egi",
    },
}


def make_login_api_post(
    verify_result=VERIFY_OK,
    exchange_result=EXCHANGE_OK,
    verify_error: Exception = None,
    exchange_error: Exception = None,
):
    """Build an ``app.auth.api_post`` fake keyed on the endpoint path."""
    calls = []

    def fake(path, body=None, timeout=None):
        calls.append((path, body))
        if path == "/api/auth/verify":
            if verify_error:
                raise verify_error
            return verify_result
        if path == "/api/auth/oidc/exchange":
            if exchange_error:
                raise exchange_error
            return exchange_result
        if path == "/api/userspace/":
            return {"created": False}
        if path == "/api/saved/seed":
            return {"seeded": True}
        raise AssertionError(f"unexpected api_post path: {path}")

    return fake, calls


def _http_error(status_code: int, message: str = "boom") -> requests.HTTPError:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"error": message}
    return requests.HTTPError(response=response)


# ---------------------------------------------------------------------------
# POST /login  (token-paste login)
# ---------------------------------------------------------------------------


class TestTokenPasteLogin:
    URL = "/login"

    def test_missing_token_redirects_to_login(self, client):
        resp = client.post(self.URL, data={"token": ""}, follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_success_establishes_session(self, client):
        fake, calls = make_login_api_post()
        with patch(API_POST_PATCH, side_effect=fake):
            resp = client.post(
                self.URL, data={"token": "a-real-looking-token"}, follow_redirects=False
            )

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"
        with client.session_transaction() as sess:
            assert sess["token"] == "fake-token"
            assert sess["claims"]["sub"] == FAKE_SUB
            assert sess["namespace"] == FAKE_NAMESPACE
        # verify + namespace bootstrap + saved-config seeding were called
        paths = [c[0] for c in calls]
        assert paths[0] == "/api/auth/verify"
        assert "/api/userspace/" in paths
        assert "/api/saved/seed" in paths

    def test_invalid_token_flashes_and_redirects(self, client):
        fake, _ = make_login_api_post(
            verify_error=_http_error(401, "Token validation failed: bad signature")
        )
        with patch(API_POST_PATCH, side_effect=fake):
            resp = client.post(
                self.URL, data={"token": "bad-token"}, follow_redirects=False
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        # No session must have been established
        with client.session_transaction() as sess:
            assert "namespace" not in sess

    def test_unreachable_api_flashes_and_redirects(self, client):
        fake, _ = make_login_api_post(verify_error=requests.ConnectionError("down"))
        with patch(API_POST_PATCH, side_effect=fake):
            resp = client.post(
                self.URL, data={"token": "whatever"}, follow_redirects=False
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_incomplete_api_response_redirects(self, client):
        fake, _ = make_login_api_post(verify_result={"token": "", "claims": {}, "namespace": ""})
        with patch(API_POST_PATCH, side_effect=fake):
            resp = client.post(
                self.URL, data={"token": "whatever"}, follow_redirects=False
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# GET /login/egi  (redirect to the EGI Check-in authorize endpoint)
# ---------------------------------------------------------------------------


class TestEgiLoginRedirect:
    URL = "/login/egi"

    def test_redirects_to_login_when_not_configured(self, client):
        with patch(SITE_PATCH, return_value=SITE_NO_OIDC):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_unreachable_api_disables_button(self, client):
        with patch(SITE_PATCH, side_effect=requests.ConnectionError("down")):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_redirects_to_checkin_authorize_url(self, client):
        with patch(SITE_PATCH, return_value=SITE_WITH_OIDC):
            resp = client.get(self.URL, follow_redirects=False)

        assert resp.status_code == 302
        location = resp.headers["Location"]
        assert "https://aai.egi.eu/auth/realms/egi/protocol/openid-connect/auth" in location
        assert "client_id=hpc-pilot" in location
        assert "response_type=code" in location
        assert "state=" in location
        # PKCE (RFC 7636): the challenge pair is mandatory for Check-in
        assert "code_challenge=" in location
        assert "code_challenge_method=S256" in location
        # The state must be stored in the session for the callback to verify
        with client.session_transaction() as sess:
            assert sess["oidc_state"]

    def test_pkce_challenge_matches_session_verifier(self, client):
        with patch(SITE_PATCH, return_value=SITE_WITH_OIDC):
            resp = client.get(self.URL, follow_redirects=False)

        location = resp.headers["Location"]
        with client.session_transaction() as sess:
            verifier = sess["oidc_code_verifier"]
        # RFC 7636: verifiers must be 43-128 characters
        assert verifier and len(verifier) >= 43
        expected = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode("ascii")
        )
        assert f"code_challenge={expected}" in location

    def test_next_url_stored_in_session(self, client):
        with patch(SITE_PATCH, return_value=SITE_WITH_OIDC):
            client.get(self.URL + "?next=/jobs", follow_redirects=False)
        with client.session_transaction() as sess:
            assert sess["oidc_next"] == "/jobs"


# ---------------------------------------------------------------------------
# GET /oauth/callback  (code exchange happens in the API)
# ---------------------------------------------------------------------------


class TestEgiCallback:
    URL = "/oauth/callback"

    def _seed_session(self, client, state="expected-state", verifier="fake-code-verifier"):
        with client.session_transaction() as sess:
            sess["oidc_state"] = state
            sess["oidc_next"] = ""
            if verifier is not None:
                sess["oidc_code_verifier"] = verifier

    def test_redirects_to_login_when_not_configured(self, client):
        with patch(SITE_PATCH, return_value=SITE_NO_OIDC):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_state_mismatch_redirects_to_login(self, client):
        self._seed_session(client)
        fake, calls = make_login_api_post()
        with (
            patch(SITE_PATCH, return_value=SITE_WITH_OIDC),
            patch(API_POST_PATCH, side_effect=fake),
        ):
            resp = client.get(
                self.URL + "?state=wrong-state&code=abc", follow_redirects=False
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        assert calls == []  # no exchange attempt

    def test_missing_code_redirects_to_login(self, client):
        self._seed_session(client)
        with patch(SITE_PATCH, return_value=SITE_WITH_OIDC):
            resp = client.get(self.URL + "?state=expected-state", follow_redirects=False)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_pkce_verifier_redirects_to_login(self, client):
        self._seed_session(client, verifier=None)
        fake, calls = make_login_api_post()
        with (
            patch(SITE_PATCH, return_value=SITE_WITH_OIDC),
            patch(API_POST_PATCH, side_effect=fake),
        ):
            resp = client.get(
                self.URL + "?state=expected-state&code=abc", follow_redirects=False
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        assert calls == []

    def test_issuer_mismatch_redirects_to_login(self, client):
        self._seed_session(client)
        fake, calls = make_login_api_post()
        with (
            patch(SITE_PATCH, return_value=SITE_WITH_OIDC),
            patch(API_POST_PATCH, side_effect=fake),
        ):
            resp = client.get(
                self.URL + "?state=expected-state&code=abc&iss=https://evil.example.com",
                follow_redirects=False,
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        assert calls == []  # IdP mix-up protection: no exchange attempt

    def test_success_establishes_session(self, client):
        self._seed_session(client)
        fake, calls = make_login_api_post()
        with (
            patch(SITE_PATCH, return_value=SITE_WITH_OIDC),
            patch(API_POST_PATCH, side_effect=fake),
        ):
            resp = client.get(
                self.URL + "?state=expected-state&code=auth-code"
                "&iss=https://aai.egi.eu/auth/realms/egi",
                follow_redirects=False,
            )

        assert resp.status_code == 302
        assert resp.headers["Location"] == "/"
        # The API-returned access token must be stored in the session
        with client.session_transaction() as sess:
            assert sess["token"] == "fake-access-token-from-oauth"
            assert sess["claims"]["sub"] == FAKE_SUB
            assert sess["namespace"] == FAKE_NAMESPACE

        exchange = [c for c in calls if c[0] == "/api/auth/oidc/exchange"]
        assert len(exchange) == 1
        body = exchange[0][1]
        # PKCE possession must be proven to the API (and through it, the IdP)
        assert body["code"] == "auth-code"
        assert body["code_verifier"] == "fake-code-verifier"
        assert body["redirect_uri"].endswith("/oauth/callback")

    def test_exchange_failure_redirects_to_login(self, client):
        self._seed_session(client)
        fake, _ = make_login_api_post(exchange_error=_http_error(401, "invalid_grant"))
        with (
            patch(SITE_PATCH, return_value=SITE_WITH_OIDC),
            patch(API_POST_PATCH, side_effect=fake),
        ):
            resp = client.get(
                self.URL + "?state=expected-state&code=abc", follow_redirects=False
            )
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# GET /login  (button visibility, driven by GET /api/site)
# ---------------------------------------------------------------------------


class TestLoginPageOidcButton:
    URL = "/login"

    def test_button_hidden_when_oidc_not_configured(self, client):
        with patch(SITE_PATCH, return_value=SITE_NO_OIDC):
            resp = client.get(self.URL)
        assert resp.status_code == 200
        assert "Log in with EGI Check-in" not in resp.data.decode()

    def test_button_shown_when_oidc_configured(self, client):
        with patch(SITE_PATCH, return_value=SITE_WITH_OIDC):
            resp = client.get(self.URL)
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Log in with EGI Check-in" in html
        assert "/login/egi" in html
