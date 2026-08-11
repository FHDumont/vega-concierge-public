"""Machine-to-machine surfaces — o11y public config, hub, and enrollment."""
from fastapi import APIRouter, Header, HTTPException, Request
from ..hub import enroll, hub
from ..obs import galileo_obs
from ..schemas import EnrollIn, EnrollPushIn
from ._common import _token_from_header, _require_owner

# No `prefix`: each route carries the full path, just like it was in `api.py`.
router = APIRouter()


@router.get("/api/galileo/config")
def galileo_public_config():
    return galileo_obs.public_config()


# --- HUB side: serve config to clients (token-gated; F-026) -----------------
# MACHINE-TO-MACHINE endpoint (NOT owner-gated — clients don't have owner session):
# authenticates by ENROLLMENT TOKEN (`serve_token`), tracks the client, and delivers cascade
# config. WARNING: payload includes LLM KEYS (DT-013 — keys travel over network);
# require token + HTTPS in lab. Anti-loop via `X-Hub-Chain` header.

@router.get("/api/hub/config")
def hub_serve(request: Request,
              authorization: str | None = Header(default=None),
              x_hub_chain: str | None = Header(default=None),
              x_hub_env: str | None = Header(default=None),
              user_agent: str | None = Header(default=None)):
    token = _token_from_header(authorization)
    ip = request.client.host if request.client else None
    try:
        return hub.serve_config(token, x_hub_chain, x_hub_env, ip, user_agent)
    except hub.HubError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail)


@router.get("/api/admin/hub/status")
def hub_status(authorization: str | None = Header(default=None)):
    # Connection status screen: mode/target/health/last-sync + clients (on hub).
    _require_owner(authorization)
    return hub.status()


@router.post("/api/admin/hub/test-connection")
def hub_test_connection(authorization: str | None = Header(default=None)):
    # On-demand pull (if remote) + masked effective cascade — owner validation.
    _require_owner(authorization)
    return hub.test_connection()


# --- Enrollment push by IP (F-027, ADR-020) --------------------------------
# CLIENT: endpoint that ACCEPTS enrollment by the hub (machine-to-machine). Gated by
# ENROLL_TOKEN (lab secret, env baked) — NOT by owner session. Sets source=remote
# pointing to hub and pulls now. Without ENROLL_TOKEN → 401 (standalone-first: loose store not
# reconfigurable over network). HUB: owner-only endpoint that pushes enrollment to IP list.

@router.post("/api/admin/enroll")
def admin_enroll(body: EnrollIn, authorization: str | None = Header(default=None)):
    token = _token_from_header(authorization)
    if not enroll.verify_enroll_token(token):
        raise HTTPException(status_code=401, detail="invalid enroll token")
    return enroll.apply_enroll(body.hub_url, body.enrollment_token, body.pull_interval_s)


@router.post("/api/admin/hub/enroll-push")
def hub_enroll_push(body: EnrollPushIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return enroll.push(body.ips, body.hub_url, body.enroll_token,
                       body.enrollment_token, body.pull_interval_s)
