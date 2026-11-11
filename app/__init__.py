"""
app — Web GUI layer for HPC Pilot (Pilot App).

A pure HTTP client of the Pilot API (``pilot-api`` repository): renders
Jinja2 templates for interactive use in a browser and delegates every
backend operation — including login verification and the OIDC code
exchange — to the API's REST endpoints (``API_BASE_URL``).  No
Kubernetes, Helm or JWT logic lives in this repository.

Authentication uses an EGI Check-in token entered via a login form or the
**EGI Check-in** button; the validated token and user metadata are stored
in the Flask session.

Blueprints
----------
auth_bp    /login, /logout, /oauth/callback — session management
k8s_bp     /, /deploy, /deployments, /deployments/…
helm_bp    /helm, /releases — deprecated redirects to /nodes
hpc_bp     /nodes, /hpc/… — "Manage Nodes" page (HPC + InterLink)
saved_bp   /saved/… — saved configuration management
"""
