"""Request helpers shared between routers — demo session and OWNER gate.

Demo auth (F-008, ADR-011): session by bearer token in `Authorization` (no cookie — CORS
is "*"). Token→user lives in memory (DT-010).
"""
from fastapi import HTTPException
from ..store import orders, users


# --- Demo auth (F-008, ADR-011) ------------------------------------------
# Session by bearer token in `Authorization` (no cookie — CORS is "*"). Token→user
# lives in memory (DT-010). Helpers read the optional header and resolve the user.

def _token_from_header(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _optional_user_id(authorization: str | None) -> str | None:
    """Session user_id if valid token; None otherwise (guest)."""
    token = _token_from_header(authorization)
    return users.session_user_id(token) if token else None


def _require_owner(authorization: str | None) -> str:
    """Gate for LLM config endpoints (F-020): requires session from an OWNER user.
    401 without valid session; 403 if logged in but without OWNER role. Returns the owner's user_id."""
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    if not users.is_owner(user_id):
        raise HTTPException(status_code=403, detail="owner only")
    return user_id


def is_gift_recommend_demo_question(text: str) -> bool:
    """Gift/recommend shopping questions belong in gift_recommend when cost_spike is ON."""
    q = (text or "").lower().strip()
    if not q:
        return False
    if any(m in q for m in ("recommend", "curate picks", "gift under", "birthday gift under", "birthday gift")):
        return True
    if "gift" in q and any(word in q for word in ("under", "below", "budget", "$")):
        return True
    return False


def _me_payload(user_id: str) -> dict:
    """Public user + tier recomputed by spending; materializes the tier in the column."""
    user = users.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid session")
    spend = orders.spend_for_user(user_id)
    payload = users.public_user(user, spend)
    if payload["tier"] != user["tier"]:
        users.update_tier(user_id, payload["tier"])  # lazy materialization (espelha ADR-008)
    return payload
