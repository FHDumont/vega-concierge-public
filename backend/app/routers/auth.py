"""Sessão de demo (bearer token) e a área da Conta do comprador."""
from fastapi import APIRouter, Header, HTTPException
from ..ai_agents import insights
from ..runnable_config import ai_request_scope
from ..store import orders, users
from ..schemas import LoginRequest, RegisterRequest, UpdateMeRequest
from ._common import _token_from_header, _optional_user_id, _me_payload

# Sem `prefix`: cada rota carrega o path completo, igualzinho ao que estava em `api.py`.
router = APIRouter()


@router.post("/api/auth/register")
def register(req: RegisterRequest):
    try:
        user = users.register(req.name, req.email, req.password)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    token = users.create_session(user["id"])
    return {"token": token, "user": _me_payload(user["id"])}


@router.post("/api/auth/login")
def login(req: LoginRequest):
    user = users.authenticate(req.email, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    token = users.create_session(user["id"])
    return {"token": token, "user": _me_payload(user["id"])}


@router.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None)):
    token = _token_from_header(authorization)
    if token:
        users.drop_session(token)
    return {"ok": True}


@router.get("/api/auth/me")
def me(authorization: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return {"user": _me_payload(user_id)}


@router.put("/api/auth/me")
def update_me(req: UpdateMeRequest, authorization: str | None = Header(default=None)):
    # Salva/edita o endereço do perfil (F-011); pré-preenche o checkout. Exige sessão.
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    users.update_address(user_id, req.address)
    return {"user": _me_payload(user_id)}


# --- IA-Conta (F-031) -------------------------------------------------------
# Insights do histórico + benefícios do tier + recompra a partir dos dados REAIS do usuário
# logado. Passa pelo controle de custo (F-022). Contexto enxuto = resumo dos próprios
# pedidos/tier. Exige sessão.

@router.get("/api/account/insights")
def account_insights(authorization: str | None = Header(default=None),
                     x_vega_session: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = _me_payload(user_id)  # tier/gasto recomputados (materialização lazy)
    user_orders = orders.list_orders_for_user(user_id)
    with ai_request_scope(
        feature="account_insights", session_id=x_vega_session, user_id=user_id,
    ) as config:
        return insights.account_insights(user, user_orders, config=config)
