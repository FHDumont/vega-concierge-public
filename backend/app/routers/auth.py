"""Demo session (bearer token) and the buyer's Account area."""
from fastapi import APIRouter, Header, HTTPException
from ..ai_agents import insights
from ..runnable_config import ai_request_scope
from ..store import orders, users
from ..schemas import LoginRequest, RegisterRequest, UpdateMeRequest
from ._common import _token_from_header, _optional_user_id, _me_payload

# No `prefix`: each route carries the full path, just like it was in `api.py`.
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
    # Saves/edits the profile address (F-011); pre-fills checkout. Requires session.
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    users.update_address(user_id, req.address)
    return {"user": _me_payload(user_id)}


# --- AI-Account (F-031) -------------------------------------------------------
# History insights + tier benefits + repurchase from REAL data of logged-in user.
# Passes through cost control (F-022). Lean context = summary of own
# orders/tier. Requires session.

@router.get("/api/account/insights")
def account_insights(authorization: str | None = Header(default=None),
                     x_vega_session: str | None = Header(default=None)):
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = _me_payload(user_id)  # tier/spend recomputed (lazy materialization)
    user_orders = orders.list_orders_for_user(user_id)
    with ai_request_scope(
        feature="account_insights", session_id=x_vega_session, user_id=user_id,
    ) as config:
        return insights.account_insights(user, user_orders, config=config)
