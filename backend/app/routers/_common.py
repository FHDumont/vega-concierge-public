"""Helpers de request compartilhados entre routers — sessão de demo e gate de OWNER.

Auth de demo (F-008, ADR-011): sessão por bearer token em `Authorization` (sem cookie — CORS
é "*"). Token→user vive em memória (DT-010).
"""
from fastapi import HTTPException
from .. import orders
from .. import users


# --- Auth de demo (F-008, ADR-011) ------------------------------------------
# Sessão por bearer token em `Authorization` (sem cookie — CORS é "*"). Token→user
# vive em memória (DT-010). Helpers leem o header opcional e resolvem o usuário.

def _token_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _optional_user_id(authorization: str | None) -> str | None:
    """user_id da sessão se houver token válido; None caso contrário (convidado)."""
    token = _token_from_header(authorization)
    return users.session_user_id(token) if token else None


def _require_owner(authorization: str | None) -> str:
    """Gate dos endpoints de config de LLM (F-020): exige sessão de um usuário OWNER.
    401 sem sessão válida; 403 se logado mas sem papel OWNER. Retorna o user_id do owner."""
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    if not users.is_owner(user_id):
        raise HTTPException(status_code=403, detail="owner only")
    return user_id


def _me_payload(user_id: str) -> dict:
    """Usuário público + tier recomputado pelo gasto; materializa o tier na coluna."""
    user = users.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid session")
    spend = orders.spend_for_user(user_id)
    payload = users.public_user(user, spend)
    if payload["tier"] != user["tier"]:
        users.update_tier(user_id, payload["tier"])  # lazy materialization (espelha ADR-008)
    return payload
