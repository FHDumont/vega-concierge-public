"""Orders: creation, history, detail, and AI features tied to an order."""
from fastapi import APIRouter, Header, HTTPException
from ..ai_agents.fraud_explanation import explain_fraud_hold
from ..ai_agents.notification_copy import compose_notification_text
from ..ai_agents.refund import arun_refund
from ..runnable_config import ai_request_scope
from ..store import checkout, orders
from ..schemas import CreateOrderRequest
from ._common import _optional_user_id

# No `prefix`: each route carries the full path, just like it was in `api.py`.
router = APIRouter()


@router.post("/api/orders")
async def create_order(req: CreateOrderRequest, authorization: str | None = Header(default=None),
                       x_vega_session: str | None = Header(default=None)):
    # Links to session user if logged in (F-008); guest continues with user_id=None.
    # Checkout (pipeline/stock/gateway → PAID/FAILED) lives in checkout.aplace_order
    # (extracted in F-017 for simulator to reuse the SAME path).
    user_id = _optional_user_id(authorization)
    with ai_request_scope(feature="fulfillment", session_id=x_vega_session, user_id=user_id) as config:
        return await checkout.aplace_order(
            [i.model_dump() for i in req.items], req.customer.model_dump(), user_id, config=config
        )


@router.get("/api/orders")
def list_orders(authorization: str | None = Header(default=None)):
    # Logged-in user's history (F-008): only own orders. Requires session.
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return orders.list_orders_for_user(user_id)


@router.get("/api/orders/{order_id}")
def get_order(order_id: str, authorization: str | None = Header(default=None)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    # F-019: with session (Store/Account), user only sees OWN order — 404 to not leak
    # existence of others' orders. Without token stays public (Admin/guest — same rules
    # as workshop controls, VM per participant).
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    return order


# --- AI-Notification (F-031) -------------------------------------------------
# Generated email copy for the current order event (confirmed/shipped) — reuses
# simulated notification (F-005). Displayed as "notification preview" on checkout confirmation
# and order detail. Passes through cost control (F-022). Same authorization as
# GET /api/orders/{id}.

@router.post("/api/orders/{order_id}/notification")
def order_notification(order_id: str, authorization: str | None = Header(default=None),
                       x_vega_session: str | None = Header(default=None)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    with ai_request_scope(feature="notification_copy", session_id=x_vega_session, user_id=user_id,
                          metadata={"order_id": order_id}) as config:
        return compose_notification_text(order, config=config)


@router.post("/api/orders/{order_id}/refund")
async def order_refund(order_id: str, authorization: str | None = Header(default=None),
                       x_vega_session: str | None = Header(default=None)):
    # Returns/Refund Coordinator (F-029): deep agent→agent→tool chain from a DELIVERED order
    # → marks REFUNDED when approved. Same authorization as GET /api/orders/{id} (F-019).
    # 409 if order is not DELIVERED.
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    if order["status"] != "DELIVERED":
        raise HTTPException(status_code=409, detail="only delivered orders can be refunded")
    with ai_request_scope(feature="returns", session_id=x_vega_session, user_id=user_id,
                          metadata={"order_id": order_id}) as config:
        return await arun_refund(order, config=config)


@router.post("/api/orders/{order_id}/fraud-explain")
def order_fraud_explain(order_id: str, authorization: str | None = Header(default=None),
                        x_vega_session: str | None = Header(default=None)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    with ai_request_scope(feature="fraud_explain", session_id=x_vega_session, user_id=user_id,
                          metadata={"order_id": order_id}) as config:
        return explain_fraud_hold(order, config=config)
