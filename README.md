# Pilot App

The **web GUI** of HPC Pilot: a Flask application that renders the user
facing pages (Home, Submit Job, Jobs, Manage Nodes) and submits HPC jobs,
deploys per-user InterLink nodes and authenticates via
[EGI Check-in](https://www.egi.eu/service/check-in/).

It is a **pure HTTP client** of the [Pilot API](https://github.com/chbrandt/pilot-api):
every backend operation — including login verification and the EGI
Check-in OAuth code exchange — is delegated to the API's REST endpoints
(`API_BASE_URL`). No Kubernetes, Helm, or JWT logic lives in this service,
and the OIDC client secret never reaches it.

HPC Pilot builds on top of [interLink](https://interlink-project.dev) to
manage cloud-native jobs in HPC appliances for EGI communities.

## Repository layout

```
.
├── app/                # Flask GUI package
│   ├── auth.py          #   login/logout + OAuth callback (via the API)
│   ├── k8s.py           #   Home / Submit Job / Jobs routes
│   ├── hpc.py           #   "Manage Nodes" (HPC + InterLink) routes
│   ├── saved.py         #   saved-config routes
│   ├── helm.py          #   deprecated redirects to /nodes
│   ├── api_client.py    #   thin HTTP client for the Pilot API
│   ├── templates/       #   Jinja2 pages
│   └── static/style.css
├── main.py              # GUI entry point (Flask)
├── requirements.txt     # flask, requests, jinja2 (no k8s/mccli/JWT deps)
├── tests/               # pytest suite (GUI routes, mocked API)
├── Dockerfile           # image: ghcr.io/chbrandt/pilot-app
└── .github/workflows/   # CI: pr / release / test-branch
```

## Quick start

```bash
# 0. Have the Pilot API running (pilot-api repository):
#    cd pilot-api && python server/main.py        # → http://localhost:5000

# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the GUI against the API
API_BASE_URL=http://localhost:5000 python main.py
# → http://localhost:5001  (if you also set FLASK_PORT=5001)
```

Set a strong session secret in production:

```bash
export FLASK_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
```

## Configuration

| Environment variable | Default | Description |
|---|---|---|
| `API_BASE_URL` | `http://localhost:5000` | Base URL of the Pilot API |
| `FLASK_SECRET_KEY` | `dev-secret-change-in-production` | Session cookie key — **change in production** |
| `FLASK_PORT` | `5000` | TCP port (the deploy chart exposes the GUI and API on different Services) |
| `FLASK_DEBUG` | `0` | `1` for debug mode |

Everything else (site hostname, wstunnel ports, default image, OIDC
client, HPC node list) is discovered at runtime from the API's public
`GET /api/site` and authenticated endpoints.

## Login flow (after the split)

- **Token paste** → the GUI sends `{access_token}` to
  `POST /api/auth/verify`; the API validates it (JWKS, expiry, issuer,
  group access) and returns `{claims, namespace}` which the GUI stores in
  its session.
- **EGI Check-in button** → the GUI starts the authorization-code flow
  (PKCE `S256`, CSRF `state`, RFC 9207 `iss` check), then sends
  `{code, code_verifier, redirect_uri}` to `POST /api/auth/oidc/exchange`.
  The API performs the token exchange with the IdP — the client secret
  stays server-side.

## Related repositories

| Repository | Role |
|---|---|
| [pilot-api](https://github.com/chbrandt/pilot-api) | REST API server (all backend logic) |
| **pilot-app** (this repo) | Web GUI — a pure HTTP client of the API |
| [pilot-deploy](https://github.com/chbrandt/pilot-deploy) | Helm charts + docker/deployment recipes |

## Documentation

- [`docs/web_ui.md`](docs/web_ui.md) — every browser route (login, Home,
  Submit Job, Jobs, Manage Nodes, saved configs) with its parameters and
  session requirements.
- [pilot-api REST API reference](https://github.com/chbrandt/pilot-api/blob/main/docs/rest_api.md) — what `app/api_client.py` calls under the hood.

## Continuous Integration

- `release.yml` — publishes `ghcr.io/chbrandt/pilot-app` from version tags
- `test-branch.yml` — validates the `test` branch and pushes a
  `test-<sha>` image
- `pr.yml` — unit tests + Docker build validation for pull requests
