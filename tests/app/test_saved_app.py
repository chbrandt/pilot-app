"""
tests/app/test_saved_app.py — GUI-layer tests for the saved-config delete
route (app.saved).

All operations are forwarded to the Pilot API (``GET/DELETE /api/saved/<id>``);
both calls are patched, so no real HTTP access is required.
"""

from unittest.mock import MagicMock, patch

import requests


GET_SESSION_USER_PATCH = "app.auth.get_session_user"

FAKE_NAMESPACE = "user-testsavedns1234"
FAKE_USER = {
    "sub": "test-sub",
    "namespace": FAKE_NAMESPACE,
    "exp": 9999999999,
    "iss": "https://aai.egi.eu",
}


def _logged_in_client(client):
    with client.session_transaction() as sess:
        sess["namespace"] = FAKE_NAMESPACE
        sess["token"] = "fake-token"
        sess["claims"] = {"sub": "test-sub", "exp": 9999999999}
    return client


def _http_error(status_code: int) -> requests.HTTPError:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = {"error": f"HTTP {status_code}"}
    return requests.HTTPError(response=response)


class TestDeleteSavedConfig:
    URL = "/saved/abc123/delete"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_not_found_flashes_and_redirects_to_submit(self, client):
        _logged_in_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch("app.saved.api_get", side_effect=_http_error(404)),
        ):
            resp = client.post(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert "/submit" in resp.headers["Location"]

    def test_container_kind_redirects_to_submit(self, client):
        _logged_in_client(client)
        entry = {"id": "abc123", "kind": "container", "name": "my-job"}
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch("app.saved.api_get", return_value=entry),
            patch("app.saved.api_delete") as mock_delete,
        ):
            resp = client.post(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert "/submit" in resp.headers["Location"]
        mock_delete.assert_called_once_with("/api/saved/abc123")

    def test_helm_kind_redirects_to_releases(self, client):
        _logged_in_client(client)
        entry = {"id": "abc123", "kind": "helm", "release_name": "interlink"}
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch("app.saved.api_get", return_value=entry),
            patch("app.saved.api_delete"),
        ):
            resp = client.post(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert "/releases" in resp.headers["Location"]

    def test_api_delete_failure_still_redirects(self, client):
        """A failed DELETE is flashed, but the user is still redirected home."""
        _logged_in_client(client)
        entry = {"id": "abc123", "kind": "container", "name": "my-job"}
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch("app.saved.api_get", return_value=entry),
            patch("app.saved.api_delete", side_effect=_http_error(500)),
        ):
            resp = client.post(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert "/submit" in resp.headers["Location"]
