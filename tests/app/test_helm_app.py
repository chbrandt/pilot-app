"""
tests/app/test_helm_app.py — GUI-layer tests for app.helm routes.

app.helm now only hosts deprecated redirects: the InterLink deploy/list UI
was merged into the "Manage Nodes" page (app.hpc, GET /hpc/nodes). See
tests/app/test_hpc_app.py for the functional coverage of that page and of
app.hpc.interlink_deploy / app.hpc.interlink_delete.
"""

from unittest.mock import patch

from tests.conftest import FAKE_NAMESPACE, FAKE_SUB, login_client


GET_SESSION_USER_PATCH = "app.auth.get_session_user"

FAKE_USER = {
    "sub": FAKE_SUB,
    "namespace": FAKE_NAMESPACE,
    "exp": 9999999999,
    "iss": "https://aai.egi.eu",
}


class TestReleasesRedirect:
    URL = "/releases"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_redirects_to_manage_nodes_when_logged_in(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/nodes")


class TestHelmDeployRedirect:
    URL = "/helm"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_redirects_to_manage_nodes_when_logged_in(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/nodes")

