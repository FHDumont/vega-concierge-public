"""Pedidos: criação, histórico, detalhe e as features de IA presas a um pedido."""
from fastapi import APIRouter, Header, HTTPException
from .. import ai_features
from ..runnable_config import ai_request_scope
from ..graphs.returns import arun_refund
from .. import checkout
from .. import orders
from ..schemas import CreateOrderRequest
from ._common import _optional_user_id

# Sem `prefix`: cada rota carrega o path completo, igualzinho ao que estava em `api.py`.
router = APIRouter()


@router.post("/api/orders")
async def create_order(req: CreateOrderRequest, authorization: str | None = Header(default=None),
                       x_vega_session: str | None = Header(default=None)):
    # Liga ao usuário da sessão se logado (F-008); convidado segue com user_id=None.
    # O fechamento (pipeline/estoque/gateway → PAID/FAILED) vive em checkout.aplace_order
    # (extraído na F-017 p/ o simulador reusar o MESMO caminho).
    user_id = _optional_user_id(authorization)
    with ai_request_scope(feature="fulfillment", session_id=x_vega_session, user_id=user_id) as config:
        return await checkout.aplace_order(
            [i.model_dump() for i in req.items], req.customer.model_dump(), user_id, config=config
        )


@router.get("/api/orders")
def list_orders(authorization: str | None = Header(default=None)):
    # Histórico do usuário logado (F-008): só os próprios pedidos. Exige sessão.
    user_id = _optional_user_id(authorization)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    return orders.list_orders_for_user(user_id)


@router.get("/api/orders/{order_id}")
def get_order(order_id: str, authorization: str | None = Header(default=None)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    # F-019: com sessão (Loja/Conta), o usuário só vê a PRÓPRIA ordem — 404 p/ não vazar
    # existência de pedido alheio. Sem token segue público (Admin/convidado — mesma régua
    # dos controles de workshop, VM por participante).
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    return order


# --- IA-Pedido (F-024) ------------------------------------------------------
# Resumo de status em linguagem natural (confirmação + detalhe do histórico). Passa pelo controle
# de custo (F-022). Contexto enxuto = dados da própria ordem. Honra os toggles. Resolve a ordem no
# backend (grounding real) com a MESMA régua de autorização do GET /api/orders/{id} (F-019).

@router.post("/api/orders/{order_id}/summary")
def order_summary(order_id: str, authorization: str | None = Header(default=None),
                  x_vega_session: str | None = Header(default=None)):
    order = orders.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    user_id = _optional_user_id(authorization)
    if user_id is not None and orders.order_owner(order_id) != user_id:
        raise HTTPException(status_code=404, detail="order not found")
    with ai_request_scope(feature="order_status", session_id=x_vega_session, user_id=user_id,
                          metadata={"order_id": order_id}):
        return ai_features.order_status_summary(order)


# --- IA-Notificação (F-031) -------------------------------------------------
# Copy gerada de e-mail p/ o evento atual do pedido (confirmação/enviado) — reaproveita a
# notificação simulada (F-005). Exibida como "notification preview" na confirmação do checkout
# e no detalhe do pedido. Passa pelo controle de custo (F-022). Mesma autorização do
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
                          metadata={"order_id": order_id}):
        return ai_features.notification_copy(order)


@router.post("/api/orders/{order_id}/refund")
async def order_refund(order_id: str, authorization: str | None = Header(default=None),
                       x_vega_session: str | None = Header(default=None)):
    # Returns/Refund Coordinator (F-029): cadeia profunda agente→agente→tool a partir de um pedido
    # DELIVERED → marca REFUNDED quando aprovado. Mesma autorização do GET /api/orders/{id} (F-019).
    # 409 se o pedido não é DELIVERED.
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
                          metadata={"order_id": order_id}):
        return ai_features.fraud_explain(order)
