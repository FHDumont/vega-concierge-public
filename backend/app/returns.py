"""Returns/Refund Coordinator (F-029) — ReAct coordinator + post-loop eligibility/abuse (F-GALILEO-11).

O **Returns Coordinator** roda loop ReAct (`policy_lookup` → `refund_calc` via ToolNode);
elegibilidade, abuse_check e `process_refund` ficam em nós LangGraph dedicados pós-loop.
"""
from .graphs.returns import build_returns_graph
from .runnable_config import resolve_config, set_current_runnable_config


def run_refund(order: dict, *, config=None) -> dict:
    """Roda a cadeia de reembolso a partir de um pedido real. Idempotente na prática."""
    resolved = resolve_config(config, feature="returns", order_id=order.get("id"))
    token = set_current_runnable_config(resolved)
    try:
        result = build_returns_graph().invoke(
            {"order": order, "messages": [], "trace": []},
            config=resolved,
        )
    finally:
        set_current_runnable_config(None, token)
    updated = result.get("updated_order") or order
    return {
        "eligible": result["eligible"],
        "approved": result["approved"],
        "refunded": result["refunded"],
        "refund_amount": result["refund_amount"],
        "status": result["status"],
        "reason": result["reason"],
        "steps": result["steps"],
        "order": updated,
    }


async def arun_refund(order: dict, *, config=None) -> dict:
    resolved = resolve_config(config, feature="returns", order_id=order.get("id"))
    token = set_current_runnable_config(resolved)
    try:
        result = await build_returns_graph().ainvoke(
            {"order": order, "messages": [], "trace": []},
            config=resolved,
        )
    finally:
        set_current_runnable_config(None, token)
    updated = result.get("updated_order") or order
    return {
        "eligible": result["eligible"],
        "approved": result["approved"],
        "refunded": result["refunded"],
        "refund_amount": result["refund_amount"],
        "status": result["status"],
        "reason": result["reason"],
        "steps": result["steps"],
        "order": updated,
    }
