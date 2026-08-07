"""Standalone deterministic checkout workflow.

The graph is deliberately independent from ``app.agents`` and ``app.graphs``.  It uses
store tools and store operations only, and treats an inventory-tool failure as an
immediate terminal outcome.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from ..llm.llm_models import invoke_chat_cascade
from ..store import orders, tools
from ..store.langchain_tools import (
    charge_payment_tool,
    check_inventory_tool,
    confirm_cart_stock_tool,
    get_price_tool,
    send_order_notification_tool,
)
from ..problems import FLAGS

FRAUD_DECISION_TOOL_NAME = "decide_fraud_allow_or_block"
_FRAUD_LLM_RUN_NAME = "fulfillment.decide_fraud_allow_or_block"
_FRAUD_SYSTEM_PROMPT = (
    "You assess checkout fraud risk. Return only the requested JSON. "
    "Do not invent customer, payment, or catalog information."
)


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str = "stub"
    system: str = "stub"


def _invoke_fraud_llm(prompt: str, *, config=None) -> LLMResult:
    """Fraud LLM via LangChain cascade — real provider model name in Galileo spans."""
    result = invoke_chat_cascade(
        "fraude",
        _FRAUD_SYSTEM_PROMPT,
        prompt,
        run_name=_FRAUD_LLM_RUN_NAME,
        max_tokens=256,
        config=config,
    )
    return LLMResult(
        result.text,
        result.input_tokens,
        result.output_tokens,
        result.model,
        result.provider,
        result.system,
    )


class FraudDecisionInput(BaseModel):
    """Arguments for the isolated fulfillment fraud decision."""

    quote_json: str = Field(description="JSON price quote from get_price for the cart SKU.")
    total: float = Field(description="Order total in BRL.")


class FulfillmentWorkflowState(TypedDict, total=False):
    """State for the isolated checkout pipeline."""

    items: list[dict[str, Any]]
    total: float
    order: dict[str, Any]
    inventory: list[dict[str, Any]]
    item_index: int
    quote: dict[str, Any]
    fraud: dict[str, Any]
    allow: bool
    stock_ok: bool
    payment: dict[str, Any]
    notification: dict[str, Any]
    checkout_success: bool
    status: str
    failure_reason: str | None


def _decode_result(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _fraud_prompt(quote: dict[str, Any], total: float) -> str:
    return (
        f"Order total ${total:.0f}; price quote {json.dumps(quote)}. Assess fraud risk. "
        'Reply ONLY with JSON {"decision": "ALLOW|BLOCK", "score": <0..1>}. '
        "Reply with raw JSON only — no markdown code fences."
    )


def _decide_fraud_allow_or_block_tool(
    quote_json: str,
    total: float,
    config: RunnableConfig,
) -> str:
    """Make the fulfillment-owned, traced fraud assessment.

    The LLM response is retained for observability, but the workshop toggle remains the
    deterministic effective checkout decision.
    """
    quote = _decode_result(quote_json)
    prompt = _fraud_prompt(quote, total)
    result = _invoke_fraud_llm(prompt, config=config)
    parsed = _decode_result(result.text)
    llm_decision = str(parsed.get("decision", "")).strip().upper() or None
    if FLAGS.fraud_false_positive:
        decision, score, source = "BLOCK", 0.95, "workshop_toggle"
    else:
        decision, score, source = "ALLOW", 0.08, "workshop_default"
    return json.dumps(
        {
            "decision": decision,
            "score": score,
            "allow": decision == "ALLOW",
            "llm_decision": llm_decision,
            "llm_score": parsed.get("score"),
            "llm_response": (result.text or "").strip(),
            "source": source,
        }
    )


decide_fraud_allow_or_block_tool = StructuredTool.from_function(
    func=_decide_fraud_allow_or_block_tool,
    name=FRAUD_DECISION_TOOL_NAME,
    description=(
        "Assess fraud risk for a checkout using the order total and catalog price quote. "
        "Returns JSON with llm_decision, effective decision, score, and source."
    ),
    args_schema=FraudDecisionInput,
)


def check_next_inventory(
    state: FulfillmentWorkflowState,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Check one cart item, retaining the caller's callbacks and metadata."""
    items = list(state.get("items") or [])
    index = state.get("item_index", 0)
    if index >= len(items):
        return {}

    item = items[index]
    sku = item.get("sku")
    inventory = list(state.get("inventory") or [])
    try:
        result = check_inventory_tool.invoke({"sku": sku}, config=config)
    except Exception as exc:
        inventory.append({"sku": sku, "error": str(exc)})
        return {
            "inventory": inventory,
            "status": "FAILED",
            "failure_reason": "inventory_unavailable",
        }

    inventory.append({"sku": sku, "result": result})
    if isinstance(result, dict) and (result.get("error") or result.get("ok") is False):
        return {
            "inventory": inventory,
            "status": "FAILED",
            "failure_reason": "inventory_unavailable",
        }
    return {"inventory": inventory, "item_index": index + 1}


def route_after_inventory(state: FulfillmentWorkflowState) -> str:
    """Stop on the first tool failure; otherwise check every cart item."""
    if state.get("status") == "FAILED":
        return END
    if state.get("item_index", 0) >= len(state.get("items") or []):
        return "fulfillment.resolve_quote"
    return "fulfillment.check_inventory"


route_after_inventory.__name__ = "fulfillment.route_after_inventory"


def resolve_quote(state: FulfillmentWorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """Obtain the catalog quote used by the fraud decision."""
    items = list(state.get("items") or [])
    sku = items[0].get("sku") if items else None
    try:
        quote = _decode_result(get_price_tool.invoke({"sku": sku}, config=config))
    except Exception:
        quote = {}
    return {"quote": quote}


def decide_fraud(state: FulfillmentWorkflowState, config: RunnableConfig) -> dict[str, Any]:
    """Run the store-owned fraud decision tool."""
    raw = decide_fraud_allow_or_block_tool.invoke(
        {
            "quote_json": json.dumps(state.get("quote") or {}),
            "total": float(state.get("total", 0)),
        },
        config=config,
    )
    fraud = _decode_result(raw)
    return {"fraud": fraud, "allow": fraud.get("decision") == "ALLOW"}


def route_after_fraud(state: FulfillmentWorkflowState) -> str:
    if not state.get("order"):
        return END
    return "fulfillment.confirm_cart_stock" if state.get("allow") else "fulfillment.persist_order_status"


route_after_fraud.__name__ = "fulfillment.route_after_fraud"


def confirm_cart_stock(
    state: FulfillmentWorkflowState, config: RunnableConfig,
) -> dict[str, Any]:
    raw = confirm_cart_stock_tool.invoke(
        {"items_json": json.dumps(list(state.get("items") or []))},
        config=config,
    )
    return {"stock_ok": bool(_decode_result(raw).get("stock_ok"))}


def route_after_stock(state: FulfillmentWorkflowState) -> str:
    return "fulfillment.charge_payment" if state.get("stock_ok") else "fulfillment.persist_order_status"


route_after_stock.__name__ = "fulfillment.route_after_stock"


def charge_payment(state: FulfillmentWorkflowState, config: RunnableConfig) -> dict[str, Any]:
    raw = charge_payment_tool.invoke(
        {"order_json": json.dumps(state["order"])},
        config=config,
    )
    return {"payment": _decode_result(raw)}


def route_after_payment(state: FulfillmentWorkflowState) -> str:
    return "fulfillment.decrement_catalog_stock" if state.get("payment", {}).get("paid") else "fulfillment.persist_order_status"


route_after_payment.__name__ = "fulfillment.route_after_payment"


def decrement_catalog_stock(state: FulfillmentWorkflowState) -> dict[str, Any]:
    tools.decrement_stock(list(state.get("items") or []))
    return {}


def _failure_reason(state: FulfillmentWorkflowState) -> str:
    if not state.get("allow", True):
        return "fraud_blocked"
    if state.get("stock_ok") is False:
        return "out_of_stock"
    if state.get("payment") and not state["payment"].get("paid"):
        return "payment_failed"
    return "unknown"


def persist_order_status(state: FulfillmentWorkflowState) -> dict[str, Any]:
    success = (
        state.get("allow", False)
        and state.get("stock_ok", False)
        and state.get("payment", {}).get("paid", False)
    )
    failure_reason = None if success else _failure_reason(state)
    order = orders.transition(
        state["order"]["id"],
        "PAID" if success else "FAILED",
        failure_reason=failure_reason,
    )
    return {
        "order": order or state["order"],
        "checkout_success": success,
        "failure_reason": failure_reason,
    }


def route_after_persist(state: FulfillmentWorkflowState) -> str:
    return "fulfillment.send_order_notification" if state.get("checkout_success") else END


route_after_persist.__name__ = "fulfillment.route_after_persist"


def send_order_notification(
    state: FulfillmentWorkflowState, config: RunnableConfig,
) -> dict[str, Any]:
    raw = send_order_notification_tool.invoke(
        {"order_json": json.dumps(state["order"])},
        config=config,
    )
    return {"notification": _decode_result(raw)}


def build_fulfillment_workflow():
    """Build the standalone `fulfillment.workflow` LangGraph."""
    graph = StateGraph(FulfillmentWorkflowState)
    graph.add_node("fulfillment.check_inventory", check_next_inventory)
    graph.add_node("fulfillment.resolve_quote", resolve_quote)
    graph.add_node("fulfillment.decide_fraud_allow_or_block", decide_fraud)
    graph.add_node("fulfillment.confirm_cart_stock", confirm_cart_stock)
    graph.add_node("fulfillment.charge_payment", charge_payment)
    graph.add_node("fulfillment.decrement_catalog_stock", decrement_catalog_stock)
    graph.add_node("fulfillment.persist_order_status", persist_order_status)
    graph.add_node("fulfillment.send_order_notification", send_order_notification)
    graph.add_edge(START, "fulfillment.check_inventory")
    graph.add_conditional_edges("fulfillment.check_inventory", route_after_inventory)
    graph.add_edge("fulfillment.resolve_quote", "fulfillment.decide_fraud_allow_or_block")
    graph.add_conditional_edges("fulfillment.decide_fraud_allow_or_block", route_after_fraud)
    graph.add_conditional_edges("fulfillment.confirm_cart_stock", route_after_stock)
    graph.add_conditional_edges("fulfillment.charge_payment", route_after_payment)
    graph.add_edge("fulfillment.decrement_catalog_stock", "fulfillment.persist_order_status")
    graph.add_conditional_edges("fulfillment.persist_order_status", route_after_persist)
    graph.add_edge("fulfillment.send_order_notification", END)
    return graph.compile(name="fulfillment.workflow").with_config(
        {
            "run_name": "fulfillment.workflow",
            "metadata": {"workflow_name": "fulfillment.workflow"},
        }
    )


workflow = build_fulfillment_workflow()


def run_fulfillment_workflow(
    items: list[dict[str, Any]],
    total: float | None = None,
    *,
    order: dict[str, Any] | None = None,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Run checkout with the same public result contract as ``run_fulfillment_graph``."""
    result = workflow.invoke(
        {
            "items": items,
            "total": total if total is not None else sum(
                float(item.get("qty", 1)) * float(item.get("price", 0)) for item in items
            ),
            "order": order,
            "inventory": [],
            "item_index": 0,
        },
        config=config,
    )
    inventory_entries = result.get("inventory") or [{}]
    last_inventory = inventory_entries[-1]
    inventory = last_inventory.get("result") or (
        {"sku": last_inventory.get("sku"), "error": last_inventory.get("error")}
        if last_inventory.get("error")
        else {}
    )
    out = {
        "allow": result.get("allow", False),
        "quote": result.get("quote") or {},
        "fraud": result.get("fraud") or {},
        "inventory": inventory,
        "failure_reason": result.get("failure_reason"),
    }
    if order is not None:
        out["order"] = result.get("order") or order
    if result.get("status") == "FAILED":
        if order is not None:
            failed_order = orders.transition(
                order["id"], "FAILED", failure_reason="inventory_unavailable"
            )
            out["order"] = failed_order or order
        out["status"] = "FAILED"
    return out
