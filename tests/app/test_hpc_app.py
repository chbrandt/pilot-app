"""
tests/app/test_hpc_app.py — GUI-layer tests for app.hpc routes.

Patches:
- ``app.auth.get_session_user`` so ``require_login`` passes without a real
  session.
- ``app.hpc.api_get`` / ``api_post`` / ``api_delete`` — the GUI's copies of
  the ``app.api_client`` helpers (imported via ``from app.api_client import
  …``).  ``api_get`` is faked with a *path-keyed* function: ``/api/site``,
  ``/api/hpc/nodes`` and ``/api/interlink`` all go through it.

No real mccli, SSH, HPC nodes or HTTP access is required.
"""

from unittest.mock import patch

from tests.conftest import FAKE_NAMESPACE, FAKE_SUB, http_error, login_client


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

GET_SESSION_USER_PATCH = "app.auth.get_session_user"
API_GET_PATCH = "app.hpc.api_get"
API_POST_PATCH = "app.hpc.api_post"
API_DELETE_PATCH = "app.hpc.api_delete"

FAKE_USER = {
    "sub": FAKE_SUB,
    "namespace": FAKE_NAMESPACE,
    "exp": 9999999999,
    "iss": "https://aai.egi.eu",
}
SITE_META = {
    "hostname": "test.local",
    "default_image": "ubuntu:22.04",
    "wstunnel": {"port": 80, "local_port": 4000},
    "oidc": {"enabled": False, "client_id": "", "issuer": "https://aai.egi.eu"},
}
FAKE_HPC_NODES = [
    {"name": "test-echo", "hostname": "161.9.255.206", "ssh_port": 3333, "plugin": "echo"},
    {"name": "test-docker", "hostname": "161.9.255.233", "ssh_port": 22, "plugin": "docker"},
]

_SUCCESS = {"success": True, "output": "ok", "error": ""}
_FAILURE = {"success": False, "output": "", "error": "connection refused"}


def make_api_get(site=SITE_META, nodes=FAKE_HPC_NODES, interlink=http_error(404)):
    """
    Build a path-keyed ``app.hpc.api_get`` fake.

    ``interlink`` is either the payload returned for ``GET /api/interlink``
    or an exception to raise (404 = not deployed — the normal state).
    """
    def fake(path, timeout=None, params=None):
        if path == "/api/site":
            return site
        if path == "/api/hpc/nodes":
            return {"nodes": nodes}
        if path == "/api/interlink":
            if isinstance(interlink, Exception):
                raise interlink
            return interlink
        raise AssertionError(f"unexpected api_get path: {path}")

    return fake


# ---------------------------------------------------------------------------
# GET /hpc/  (deprecated — redirects to /nodes)
# ---------------------------------------------------------------------------


class TestHpcPage:
    URL = "/hpc/"

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


# ---------------------------------------------------------------------------
# GET /nodes  ("Manage Nodes" page: HPC + InterLink)
# ---------------------------------------------------------------------------


class TestManageNodesPage:
    URL = "/hpc/nodes"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_hpc_nodes_rendered(self, client):
        """Every configured HPC node must be present in the page."""
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get()),
        ):
            resp = client.get(self.URL)

        assert b"test-echo" in resp.data
        assert b"test-docker" in resp.data

    def test_interlink_deployed_shows_stop_button(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get(interlink={"success": True})),
        ):
            resp = client.get(self.URL)

        assert "Stop Node" in resp.data.decode()

    def test_interlink_not_deployed_shows_start_button(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get()),
        ):
            resp = client.get(self.URL)

        assert "Start Node" in resp.data.decode()

    def test_wstunnel_defaults_rendered_from_site_meta(self, client):
        """The page shows the shared wstunnel hostname/port (GET /api/site)."""
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get()),
        ):
            resp = client.get(self.URL)

        assert "test.local" in resp.data.decode()


# ---------------------------------------------------------------------------
# POST /hpc/nodes/interlink/deploy  and  POST /hpc/nodes/interlink/delete
# ---------------------------------------------------------------------------


class TestInterlinkDeploy:
    URL = "/hpc/nodes/interlink/deploy"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_name_redirects(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={}, follow_redirects=False)
        assert resp.status_code == 302

    def test_success_redirects_to_manage_nodes(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post(
                self.URL, data={"hpc_name": "test-echo"}, follow_redirects=False
            )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/nodes")

    def test_api_failure_still_redirects(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_FAILURE),
        ):
            resp = client.post(
                self.URL, data={"hpc_name": "test-echo"}, follow_redirects=False
            )
        assert resp.status_code == 302


class TestInterlinkDelete:
    URL = "/hpc/nodes/interlink/delete"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_name_redirects(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={}, follow_redirects=False)
        assert resp.status_code == 302

    def test_success_redirects_to_manage_nodes(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_DELETE_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post(
                self.URL, data={"hpc_name": "test-echo"}, follow_redirects=False
            )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/nodes")

    def test_api_failure_still_redirects(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_DELETE_PATCH, return_value=_FAILURE),
        ):
            resp = client.post(
                self.URL, data={"hpc_name": "test-echo"}, follow_redirects=False
            )
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# POST /hpc/nodes/start  and  POST /hpc/nodes/stop  (GA8: two sequenced API
# calls, logs rendered on the result page — GA10)
# ---------------------------------------------------------------------------


class TestNodeStart:
    URL = "/hpc/nodes/start"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_name_redirects(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={}, follow_redirects=False)
        assert resp.status_code == 302

    def test_success_renders_result_with_logs(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
            patch(API_DELETE_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Start Node" in html
        assert "OK" in html  # log content

    def test_hpc_failure_renders_result_with_error(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_FAILURE),
            patch(API_DELETE_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 200
        assert "FAIL" in resp.data.decode()


class TestNodeStop:
    URL = "/hpc/nodes/stop"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_name_redirects(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={}, follow_redirects=False)
        assert resp.status_code == 302

    def test_success_renders_result_with_logs(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
            patch(API_DELETE_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Stop Node" in html
        assert "OK" in html

    def test_interlink_not_deployed_is_tolerated(self, client):
        """A 404 on the InterLink uninstall is a warning, not a failure."""
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
            patch(API_DELETE_PATCH, side_effect=http_error(404)),
        ):
            resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 200
        assert "WARN" in resp.data.decode()


# ---------------------------------------------------------------------------
# POST /hpc/deploy  (remote HPC stack deployment via mccli/SSH)
# ---------------------------------------------------------------------------


class TestHpcDeploy:
    URL = "/hpc/deploy"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data={"hpc_name": "test-echo"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_name_redirects(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, follow_redirects=False)
        assert resp.status_code == 302

    def test_success_renders_result_with_wstunnel_details(self, client):
        """The result page shows the shared wstunnel host from GET /api/site."""
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get()),
            patch(API_POST_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post(self.URL, data={"hpc_name": "test-docker"})

        assert resp.status_code == 200
        assert "test.local" in resp.data.decode()

    def test_deploy_uses_long_timeout(self, client):
        """HPC deploy is long-running; the API call must use LONG_TIMEOUT."""
        from app.api_client import LONG_TIMEOUT

        login_client(client)
        captured = {}

        def fake_api_post(path, payload=None, timeout=None):
            captured["path"] = path
            captured["hpc_name"] = payload.get("hpc_name")
            captured["timeout"] = timeout
            return _SUCCESS

        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get()),
            patch(API_POST_PATCH, side_effect=fake_api_post),
        ):
            client.post(self.URL, data={"hpc_name": "test-docker"})

        assert captured["path"] == "/api/hpc/deploy"
        assert captured["hpc_name"] == "test-docker"
        assert captured["timeout"] == LONG_TIMEOUT


# ---------------------------------------------------------------------------
# GET /hpc/status
# ---------------------------------------------------------------------------


class TestHpcStatus:
    URL = "/hpc/status"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_hpc_name_redirects(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.get(self.URL, follow_redirects=False)
        assert resp.status_code == 302

    def test_success_renders_result(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, return_value=_SUCCESS),
        ):
            resp = client.get(self.URL, query_string={"hpc_name": "test-echo"})
        assert resp.status_code == 200

    def test_failure_renders_result(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, return_value=_FAILURE),
        ):
            resp = client.get(self.URL, query_string={"hpc_name": "test-echo"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /hpc/start  and  POST /hpc/stop
# ---------------------------------------------------------------------------


class TestHpcStartStop:
    def test_start_redirects_when_not_logged_in(self, client):
        resp = client.post("/hpc/start", data={"hpc_name": "test-echo"})
        assert resp.status_code == 302

    def test_stop_redirects_when_not_logged_in(self, client):
        resp = client.post("/hpc/stop", data={"hpc_name": "test-echo"})
        assert resp.status_code == 302

    def test_start_success_renders_result(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post("/hpc/start", data={"hpc_name": "test-echo"})
        assert resp.status_code == 200

    def test_stop_success_renders_result(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, return_value=_SUCCESS),
        ):
            resp = client.post("/hpc/stop", data={"hpc_name": "test-echo"})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /hpc/nodes/prune  (GA6: remove InterLink + HPC deployments + namespace)
# ---------------------------------------------------------------------------


class TestUserspacePrune:
    URL = "/hpc/nodes/prune"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_success_redirects_to_manage_nodes(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(
                API_POST_PATCH,
                return_value={"namespace": FAKE_NAMESPACE, "namespace_deleted": True},
            ),
        ):
            resp = client.post(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/nodes")

    def test_api_failure_still_redirects(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, side_effect=http_error(500)),
        ):
            resp = client.post(self.URL, follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/nodes")
