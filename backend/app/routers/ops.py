"""Superfícies máquina-a-máquina — config pública de o11y, hub e enrollment."""
from fastapi import APIRouter, Header, HTTPException, Request
from ..hub import enroll, hub
from ..obs import galileo_obs
from ..schemas import EnrollIn, EnrollPushIn
from ._common import _token_from_header, _require_owner

# Sem `prefix`: cada rota carrega o path completo, igualzinho ao que estava em `api.py`.
router = APIRouter()


@router.get("/api/galileo/config")
def galileo_public_config():
    return galileo_obs.public_config()


# --- Lado HUB: servir config a clientes (token-gated; F-026) -----------------
# Endpoint MÁQUINA-A-MÁQUINA (NÃO owner-gated — clientes não têm sessão de owner):
# autentica pelo TOKEN DE ENROLLMENT (`serve_token`), rastreia o cliente e entrega a config
# da cascata. ATENÇÃO: o payload inclui as CHAVES de LLM (DT-013 — chaves trafegam na rede);
# exigir token + HTTPS no lab. Anti-loop pela cadeia `X-Hub-Chain`.

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
    # Tela de status de conexão: modo/alvo/saúde/last-sync + clientes (no hub).
    _require_owner(authorization)
    return hub.status()


@router.post("/api/admin/hub/test-connection")
def hub_test_connection(authorization: str | None = Header(default=None)):
    # Pull sob demanda (se remote) + cascata efetiva mascarada — validação owner.
    _require_owner(authorization)
    return hub.test_connection()


# --- Enrollment push por IP (F-027, ADR-020) --------------------------------
# CLIENTE: endpoint que ACEITA ser enrolado pelo hub (máquina-a-máquina). Gateado por
# ENROLL_TOKEN (segredo do lab, env baked) — NÃO pela sessão de owner. Seta source=remote
# apontando p/ o hub e puxa já. Sem ENROLL_TOKEN → 401 (standalone-first: loja solta não é
# reconfigurável por rede). HUB: endpoint owner-only que empurra o enroll p/ uma lista de IPs.

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
