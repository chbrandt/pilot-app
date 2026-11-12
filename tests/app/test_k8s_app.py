"""
tests/app/test_k8s_app.py — GUI-layer tests for app.k8s routes.

Focuses on the Home page (``GET /``), the Submit Job form (``GET/POST
/submit``) and the Jobs page (``GET /jobs``) — which lists the user's
container jobs only; InterLink nodes/releases are managed on the "Manage
Nodes" page (see test_hpc_app.py).

Patches:
- ``app.auth.get_session_user`` so ``require_login`` passes without a real
  session.
- ``app.k8s.api_get`` / ``api_post`` / ``api_delete`` — the GUI's copies of
  the ``app.api_client`` helpers.  ``api_get`` is faked with a path-keyed
  function: ``/api/jobs``, ``/api/hpc/nodes``, ``/api/site``, ``/api/saved``
  and ``/api/interlink`` all go through it.

No real Kubernetes cluster, Helm binary, or HTTP access is required.
"""

from unittest.mock import patch

from tests.conftest import FAKE_NAMESPACE, FAKE_SUB, http_error, login_client


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

GET_SESSION_USER_PATCH = "app.auth.get_session_user"
API_GET_PATCH = "app.k8s.api_get"
API_POST_PATCH = "app.k8s.api_post"
API_DELETE_PATCH = "app.k8s.api_delete"

FAKE_USER = {
    "sub": FAKE_SUB,
    "namespace": FAKE_NAMESPACE,
    "exp": 9999999999,
    "iss": "https://aai.egi.eu",
}
SITE_META = {
    "hostname": "test.local",
    "default_image": "alpine:3.19",
    "wstunnel": {"port": 443, "local_port": 4000},
    "oidc": {"enabled": False, "client_id": "", "issuer": "https://aai.egi.eu"},
}
FAKE_HPC_NODES = [
    {"name": "test-echo", "hostname": "1.2.3.4", "ssh_port": 22, "plugin": "echo"},
]


def _job(name="my-job", image="ubuntu:22.04", node_name="vk-1", status="running"):
    """A minimal job dict as returned by ``GET /api/jobs``."""
    return {
        "name": name,
        "namespace": FAKE_NAMESPACE,
        "image": image,
        "node_name": node_name,
        "status": status,
        "created": "2025-01-01 00:00:00",
    }


def make_api_get(
    jobs=None,
    nodes=FAKE_HPC_NODES,
    interlink=http_error(404),
    interlink_nodes=None,
    saved=None,
    site=SITE_META,
):
    """Path-keyed ``app.k8s.api_get`` fake."""
    if jobs is None:
        jobs = []
    if interlink_nodes is None:
        interlink_nodes = []
    if saved is None:
        saved = []

    def fake(path, timeout=None, params=None):
        if path == "/api/site":
            return site
        if path == "/api/jobs":
            return jobs
        if path.startswith("/api/jobs/"):
            if path.endswith("/output"):
                return {
                    "name": path.split("/")[3],
                    "pod": "my-job-abc123",
                    "content": "Submitted to SLURM node vnode-1.\nHello world!\n",
                }
            if path.endswith("/status"):
                return {"status": "running"}
            return _job(name=path.split("/")[3])
        if path == "/api/hpc/nodes":
            return {"nodes": nodes}
        if path == "/api/interlink":
            if isinstance(interlink, Exception):
                raise interlink
            return interlink
        if path == "/api/interlink/nodes":
            return {"nodes": interlink_nodes}
        if path == "/api/saved":
            return saved
        raise AssertionError(f"unexpected api_get path: {path}")

    return fake


# ---------------------------------------------------------------------------
# GET /  (Home: overview of jobs + interlink/HPC nodes)
# ---------------------------------------------------------------------------


class TestHomePage:
    URL = "/"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_renders_jobs_and_nodes(self, client):
        """The home page lists both jobs and interlink/HPC nodes."""
        jobs = [_job(name="alpha"), _job(name="beta")]
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get(jobs=jobs)),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "alpha" in html
        assert "beta" in html
        assert "test-echo" in html

    def test_node_list_failure_renders_error(self, client):
        """When /api/hpc/nodes fails the page still renders, with the error."""
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(
                API_GET_PATCH,
                side_effect=make_api_get(jobs=[], interlink=http_error(500)),
            ),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        assert "alert-error" in resp.data.decode()


# ---------------------------------------------------------------------------
# GET /submit  (job submission form)
# ---------------------------------------------------------------------------


class TestSubmitForm:
    URL = "/submit"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_renders_saved_configs_and_default_image(self, client):
        """Saved container presets and the default image come from the API."""
        saved = [{
            "id": "cfg1",
            "kind": "container",
            "name": "my-saved-job",
            "image": "ubuntu:22.04",
            "saved_at": "2025-01-01T00:00:00+00:00",
        }]
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(
                API_GET_PATCH,
                side_effect=make_api_get(
                    saved=saved,
                    interlink_nodes=[{"name": "vk-node-a"}],
                ),
            ),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "my-saved-job" in html
        # GA7: the default image from the site metadata is the placeholder
        assert "alpine:3.19" in html
        # InterLink virtual-kubelet nodes feed the node dropdown
        assert "vk-node-a" in html

    def test_saved_config_failure_is_degraded_not_fatal(self, client):
        """/api/saved failures still render the form (no saved presets)."""
        def fake(path, timeout=None, params=None):
            if path == "/api/saved":
                raise http_error(500)
            return make_api_get()(path, timeout=timeout, params=params)

        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=fake),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        assert "my-saved-job" not in resp.data.decode()


# ---------------------------------------------------------------------------
# POST /submit  (job submission)
# ---------------------------------------------------------------------------


class TestSubmitJob:
    URL = "/submit"

    def _valid_data(self):
        return {
            "name": "my-job",
            "image": "ubuntu:22.04",
            "node_name": "vk-node-1",
            "command": "echo hi",
            "cpu": "1",
            "memory": "1Gi",
        }

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self.URL, data=self._valid_data())
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_missing_name_redirects(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={"image": "x", "node_name": "y"})
        assert resp.status_code == 302

    def test_invalid_name_redirects(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post(self.URL, data={**self._valid_data(), "name": "Bad_Name"})
        assert resp.status_code == 302

    def test_success_renders_status_page(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(
                API_POST_PATCH,
                return_value={"success": True, "name": "my-job", "namespace": FAKE_NAMESPACE},
            ),
        ):
            resp = client.post(self.URL, data=self._valid_data())
        assert resp.status_code == 200

    def test_sends_preset_payload_to_api(self, client):
        login_client(client)
        captured = {}

        def fake(path, body=None, timeout=None):
            captured["path"] = path
            captured["body"] = body
            return {"success": True, "name": "my-job"}

        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, side_effect=fake),
        ):
            client.post(self.URL, data=self._valid_data())

        assert captured["path"] == "/api/jobs/preset"
        body = captured["body"]
        assert body["name"] == "my-job"
        assert body["image"] == "ubuntu:22.04"
        assert body["node_name"] == "vk-node-1"
        assert body["cpu"] == "1"
        assert body["memory"] == "1Gi"

    def test_api_failure_redirects(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, side_effect=http_error(400, "Invalid node")),
        ):
            resp = client.post(self.URL, data=self._valid_data(), follow_redirects=False)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# GET /jobs  (Jobs page — jobs only; GA12)
# ---------------------------------------------------------------------------


class TestJobsPage:
    URL = "/jobs"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(self.URL)
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_no_jobs_renders_empty_state_without_error(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get(jobs=[])),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "No jobs" in html
        assert "alert-error" not in html

    def test_jobs_listed_without_error(self, client):
        """Jobs are listed; interlink nodes/releases are NOT shown as jobs."""
        jobs = [_job(name="alpha"), _job(name="beta")]
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get(jobs=jobs)),
        ):
            resp = client.get(self.URL)

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "alpha" in html
        assert "beta" in html
        assert "alert-error" not in html


# ---------------------------------------------------------------------------
# POST /jobs/<ns>/<name>/delete
# ---------------------------------------------------------------------------


class TestDeleteJob:
    def _url(self, name="my-job"):
        return f"/jobs/{FAKE_NAMESPACE}/{name}/delete"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self._url())
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_wrong_namespace_redirects(self, client):
        """Cross-user namespace attempts must redirect to /jobs."""
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post("/jobs/other-ns/my-job/delete", follow_redirects=False)
        assert resp.status_code == 302
        assert "/jobs" in resp.headers["Location"]

    def test_success_redirects_to_jobs(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_DELETE_PATCH, return_value={"deleted": True}),
        ):
            resp = client.post(self._url(), follow_redirects=False)
        assert resp.status_code == 302
        assert "/jobs" in resp.headers["Location"]

    def test_api_failure_still_redirects(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_DELETE_PATCH, side_effect=http_error(404)),
        ):
            resp = client.post(self._url(), follow_redirects=False)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# GET /jobs/<ns>/<name>/status  (AJAX JSON poll)
# ---------------------------------------------------------------------------


class TestJobStatus:
    def test_json_passthrough(self, client):
        login_client(client)
        payload = {"status": "running", "replicas": 1}
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, return_value=payload),
        ):
            resp = client.get(f"/jobs/{FAKE_NAMESPACE}/my-job/status")

        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith("application/json")
        assert resp.get_json(force=True) == payload

    def test_api_error_maps_to_status_code(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=http_error(404)),
        ):
            resp = client.get(f"/jobs/{FAKE_NAMESPACE}/my-job/status")

        assert resp.status_code == 404
        assert resp.get_json(force=True)["error"] == "HTTP 404"


# ---------------------------------------------------------------------------
# POST /jobs/<ns>/<name>/save
# ---------------------------------------------------------------------------


class TestSaveJob:
    def _url(self, name="my-job"):
        return f"/jobs/{FAKE_NAMESPACE}/{name}/save"

    def test_redirects_when_not_logged_in(self, client):
        resp = client.post(self._url())
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_wrong_namespace_redirects(self, client):
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.post("/jobs/other-ns/my-job/save", follow_redirects=False)
        assert resp.status_code == 302
        assert "/jobs" in resp.headers["Location"]

    def test_success_posts_saved_config_to_api(self, client):
        """The job spec read from /api/jobs/<name> is saved via POST /api/saved."""
        login_client(client)
        captured = {}

        def fake(path, body=None, timeout=None):
            captured[path] = body
            if path == "/api/saved":
                return {"id": "abc123", "kind": "container"}
            return {"name": "my-job", "image": "ubuntu:22.04"}

        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_POST_PATCH, side_effect=fake),
            patch(API_GET_PATCH, return_value={"name": "my-job", "image": "ubuntu:22.04"}),
        ):
            resp = client.post(self._url(), follow_redirects=False)

        assert resp.status_code == 302
        assert captured["/api/saved"] == {
            "kind": "container",
            "config": {"name": "my-job", "image": "ubuntu:22.04"},
        }

    def test_api_failure_redirects(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=http_error(404)),
        ):
            resp = client.post(self._url(), follow_redirects=False)
        assert resp.status_code == 302


# ---------------------------------------------------------------------------
# GET /jobs/<ns>/<name>/output
# ---------------------------------------------------------------------------


class TestJobOutputPage:
    def test_redirects_when_not_logged_in(self, client):
        resp = client.get(f"/jobs/{FAKE_NAMESPACE}/my-job/output")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_wrong_namespace_redirects(self, client):
        """Cross-user namespace attempts must redirect to /jobs."""
        login_client(client)
        with patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER):
            resp = client.get("/jobs/other-namespace/my-job/output")
        assert resp.status_code == 302
        assert "/jobs" in resp.headers["Location"]

    def test_renders_output_content(self, client):
        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=make_api_get()),
        ):
            resp = client.get(f"/jobs/{FAKE_NAMESPACE}/my-job/output")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Hello world!" in html
        assert "my-job-abc123" in html
        assert "Submitted to SLURM" in html

    def test_api_error_renders_error_card(self, client):
        """An API failure (e.g. 404) renders an error card, not a crash."""
        def fake(path, timeout=None, params=None):
            if path.endswith("/output"):
                raise http_error(404)
            return make_api_get()(path, timeout=timeout, params=params)

        login_client(client)
        with (
            patch(GET_SESSION_USER_PATCH, return_value=FAKE_USER),
            patch(API_GET_PATCH, side_effect=fake),
        ):
            resp = client.get(f"/jobs/{FAKE_NAMESPACE}/my-job/output")

        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Could not retrieve job output" in html
        assert "HTTP 404" in html


