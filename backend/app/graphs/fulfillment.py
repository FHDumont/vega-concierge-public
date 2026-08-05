"""Fulfillment ReAct graph — decision + checkout inside place_order (F-OBS-PREP-4 / F-OBS-PREP-7)."""
from __future__ import annotations

import json

from dataclasses import dataclass

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from ..tool_arg_normalize import format_tool_error
from .. import orders, tools
from ..problems import FLAGS
from ..langchain_tools import (
    FULFILLMENT_TOOLS,
    charge_payment_tool,
    check_inventory_tool,
    confirm_cart_stock_tool,
    get_price_tool,
    send_order_notification_tool,
)
from ..runnable_config import resolve_config, set_current_runnable_config
from .react import ReactState, invoke_react_agent, make_named_tools_route


@dataclass(frozen=True)
class FulfillmentNodeNames:
    """Nós LangGraph fulfillment — ids `{surface}.{business_step}`."""

    agent: str = "fulfillment.verify_cart_inventory_and_price"
    tools: str = "fulfillment.run_checkout_tools"
    resolve_quote: str = "fulfillment.resolve_checkout_quote"
    decide_fraud: str = "fulfillment.decide_fraud_allow_or_block"
    confirm_stock: str = "fulfillment.confirm_cart_stock"
    charge: str = "fulfillment.charge_payment"
    decrement: str = "fulfillment.decrement_catalog_stock"
    persist: str = "fulfillment.persist_order_status"
    notify: str = "fulfillment.send_order_notification"


N = FulfillmentNodeNames()


class FulfillmentState(ReactState, total=False):
    items: list[dict]
    total: float
    order: dict
    allow: bool
    quote: dict
    fraud: dict
    inventory: dict
    stock_ok: bool
    payment: dict
    notification: dict
    checkout_success: bool
    failure_reason: str | None


def _parse_tool_content(content) -> object:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return content
    return content


def _tool_result_named(messages: list[BaseMessage], name: str) -> object | None:
    for m in reversed(messages):
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == name:
            return _parse_tool_content(m.content)
    return None


def _tool_result_for_sku(
    messages: list[BaseMessage], sku: str, tool_name: str,
) -> dict | None:
    for m in reversed(messages):
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == tool_name:
            parsed = _parse_tool_content(m.content)
            if isinstance(parsed, dict) and str(parsed.get("sku")) == str(sku):
                return parsed
    return None


def _cart_tools_satisfied(messages: list[BaseMessage], items: list[dict]) -> bool:
    cart_skus = _cart_skus(items)
    if not cart_skus:
        return False
    for sku in cart_skus:
        inventory = _tool_result_for_sku(messages, sku, "check_inventory")
        quote = _tool_result_for_sku(messages, sku, "get_price")
        if not inventory or not quote:
            return False
        if not _sku_matches_cart(inventory, [sku]) or not _sku_matches_cart(quote, [sku]):
            return False
    return True


def _cart_skus(items: list[dict]) -> list[str]:
    return [str(it["sku"]) for it in items if it.get("sku")]


def _sku_matches_cart(result: dict | None, cart_skus: list[str]) -> bool:
    if not isinstance(result, dict) or not cart_skus:
        return False
    sku = result.get("sku")
    return bool(sku) and str(sku) in cart_skus


def _initial_human(state: FulfillmentState) -> HumanMessage:
    total = float(state.get("total", 0))
    items = list(state.get("items") or [])
    if items:
        lines = ", ".join(
            f"{it.get('sku')} x{it.get('qty', 1)} @ ${float(it.get('price', 0)):.0f}"
            for it in items
        )
        cart_block = f"Cart line items (use these exact SKUs for tools): {lines}. "
    else:
        cart_block = "Cart is empty. "
    return HumanMessage(
        content=(
            f"Coordinate checkout for an order totaling ${total:.0f}. {cart_block}"
            "Call check_inventory and get_price for each cart SKU (exact catalog SKUs only), "
            "then stop so fraud and payment can run. Do not invent SKUs. "
            "Do not ask for address or cart details."
        )
    )


def agent_node(state: FulfillmentState, config: RunnableConfig) -> dict:
    lc_messages = list(state.get("messages") or [])
    if not lc_messages:
        lc_messages = [_initial_human(state)]
    return invoke_react_agent(
        {**state, "messages": lc_messages},
        agent_name="fulfillment_coordinator",
        workflow="fulfillment",
        tools=FULFILLMENT_TOOLS,
        feature="fulfillment_coordinator",
        system_messages=[],
        trace_label="Fulfillment coordinator",
        config=config,
    )


def resolve_quote_node(state: FulfillmentState, config: RunnableConfig) -> dict:
    """Normaliza inventory/quote a partir do message history (fallback direto se SKU errado)."""
    messages = list(state.get("messages") or [])
    trace = list(state.get("trace") or [])
    items = list(state.get("items") or [])
    cart_skus = _cart_skus(items)
    sku = cart_skus[0] if cart_skus else None

    inventory_raw = _tool_result_named(messages, "check_inventory")
    quote_raw = _tool_result_named(messages, "get_price")
    inventory: dict = inventory_raw if isinstance(inventory_raw, dict) else {}
    quote: dict = quote_raw if isinstance(quote_raw, dict) else {}

    if sku and not _sku_matches_cart(inventory, cart_skus):
        if inventory:
            trace.append(
                f"Resolve quote: discard check_inventory sku={inventory.get('sku')!r} (not in cart)"
            )
        trace.append("Resolve quote fallback: check_inventory tool")
        try:
            inventory = check_inventory_tool.invoke({"sku": sku}, config=config)
        except RuntimeError as exc:
            inventory = {"ok": False, "sku": sku, "error": str(exc)}
            trace.append(f"Resolve quote: inventory error — {exc}")
    if sku and not _sku_matches_cart(quote, cart_skus):
        if quote:
            trace.append(
                f"Resolve quote: discard get_price sku={quote.get('sku')!r} (not in cart)"
            )
        trace.append("Resolve quote fallback: get_price tool")
        quote = get_price_tool.invoke({"sku": sku}, config=config)

    trace.append(f"Resolve quote: sku={quote.get('sku')} price={quote.get('price')}")
    return {"quote": quote, "inventory": inventory, "trace": trace}


def decide_fraud_node(state: FulfillmentState, config: RunnableConfig) -> dict:
    """Tool span `decide_fraud_allow_or_block` — input quote/total, output JSON llm + efetivo."""
    from ..langchain_tools import decide_fraud_allow_or_block_tool

    quote = dict(state.get("quote") or {})
    total = float(state.get("total", 0))
    trace = list(state.get("trace") or [])

    raw = decide_fraud_allow_or_block_tool.invoke(
        {"quote_json": json.dumps(quote), "total": total},
        config=config,
    )
    fraud = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    allow = fraud.get("decision") == "ALLOW"
    trace.append(
        "Fraud decision: "
        f"llm={fraud.get('llm_decision')} score={fraud.get('llm_score')} → "
        f"effective={fraud.get('decision')} ({fraud.get('source')}) allow={allow}"
    )

    return {
        "allow": allow,
        "fraud": fraud,
        "trace": trace,
    }


def confirm_stock_node(state: FulfillmentState, config: RunnableConfig) -> dict:
    items = list(state.get("items") or [])
    trace = list(state.get("trace") or [])

    raw = confirm_cart_stock_tool.invoke(
        {"items_json": json.dumps(items)},
        config=config,
    )
    parsed = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    stock_ok = bool(parsed.get("stock_ok"))
    trace.append(
        f"Confirm stock: ok={stock_ok} item_count={parsed.get('item_count', len(items))}"
    )
    return {"stock_ok": stock_ok, "trace": trace}


def charge_payment_node(state: FulfillmentState, config: RunnableConfig) -> dict:
    order = state["order"]
    trace = list(state.get("trace") or [])

    raw = charge_payment_tool.invoke(
        {"order_json": json.dumps(order)},
        config=config,
    )
    payment = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    trace.append(
        f"Charge payment: paid={payment.get('paid')} reason={payment.get('reason')}"
    )
    return {"payment": payment, "trace": trace}


def decrement_stock_node(state: FulfillmentState, config: RunnableConfig) -> dict:
    items = list(state.get("items") or [])
    tools.decrement_stock(items)
    trace = list(state.get("trace") or [])
    trace.append("Decrement catalog stock")
    return {"trace": trace}


def _inventory_service_failed(state: FulfillmentState, messages: list[BaseMessage] | None = None) -> bool:
    if FLAGS.inventory_outage:
        return True
    inventory = state.get("inventory") or {}
    if isinstance(inventory, dict) and inventory.get("error"):
        return True
    for m in messages or []:
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "check_inventory":
            parsed = _parse_tool_content(m.content)
            if isinstance(parsed, dict) and parsed.get("error"):
                return True
            raw = str(m.content or "").lower()
            if "inventory service unavailable" in raw or "503" in raw:
                return True
    return False


def _derive_checkout_failure_reason(state: FulfillmentState, messages: list[BaseMessage] | None = None) -> str:
    if _inventory_service_failed(state, messages):
        return "inventory_unavailable"
    if not state.get("allow", True):
        return "fraud_blocked"
    if state.get("stock_ok") is False:
        return "out_of_stock"
    payment = state.get("payment") or {}
    if payment and not payment.get("paid"):
        return "payment_failed"
    return "unknown"


def persist_order_node(state: FulfillmentState, config: RunnableConfig) -> dict:
    order = dict(state["order"])
    messages = list(state.get("messages") or [])
    success = (
        not _inventory_service_failed(state, messages)
        and state.get("allow", False)
        and state.get("stock_ok", False)
        and state.get("payment", {}).get("paid", False)
    )
    status = "PAID" if success else "FAILED"
    failure_reason = None if success else _derive_checkout_failure_reason(state, messages)
    order = orders.transition(order["id"], status, failure_reason=failure_reason)
    trace = list(state.get("trace") or [])
    trace.append(f"Persist order: status={status} reason={failure_reason or '—'}")
    return {
        "order": order,
        "checkout_success": success,
        "failure_reason": failure_reason,
        "trace": trace,
    }


def send_notification_node(state: FulfillmentState, config: RunnableConfig) -> dict:
    order = state["order"]
    trace = list(state.get("trace") or [])

    raw = send_order_notification_tool.invoke(
        {"order_json": json.dumps(order)},
        config=config,
    )
    notification = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    trace.append(
        f"Send notification: sent={notification.get('sent')} "
        f"latency_ms={notification.get('latency_ms')}"
    )
    return {"notification": notification, "trace": trace}


def _route_after_checkout_tools(state: FulfillmentState) -> str:
    messages = list(state.get("messages") or [])
    items = list(state.get("items") or [])
    if _cart_tools_satisfied(messages, items):
        return N.resolve_quote
    return N.agent


_route_after_checkout_tools.__name__ = "fulfillment.route_after_checkout_tools"


def _route_after_fraud_decision(state: FulfillmentState) -> str:
    if not state.get("order"):
        return END
    if state.get("allow"):
        return N.confirm_stock
    return N.persist


_route_after_fraud_decision.__name__ = "fulfillment.route_after_fraud_decision"


def _route_after_stock_check(state: FulfillmentState) -> str:
    if state.get("stock_ok"):
        return N.charge
    return N.persist


_route_after_stock_check.__name__ = "fulfillment.route_after_stock_check"


def _route_after_payment(state: FulfillmentState) -> str:
    if state.get("payment", {}).get("paid"):
        return N.decrement
    return N.persist


_route_after_payment.__name__ = "fulfillment.route_after_payment"


def _route_after_persist_order(state: FulfillmentState) -> str:
    if state.get("checkout_success"):
        return N.notify
    return END


_route_after_persist_order.__name__ = "fulfillment.route_after_persist_order"


def build_fulfillment_graph():
    """ReAct coordinator → fraud → stock → payment → persist → notify (tudo no mesmo trace)."""
    g = StateGraph(FulfillmentState)
    g.add_node(N.agent, agent_node)
    g.add_node(N.tools, ToolNode(FULFILLMENT_TOOLS, handle_tool_errors=format_tool_error))
    g.add_node(N.resolve_quote, resolve_quote_node)
    g.add_node(N.decide_fraud, decide_fraud_node)
    g.add_node(N.confirm_stock, confirm_stock_node)
    g.add_node(N.charge, charge_payment_node)
    g.add_node(N.decrement, decrement_stock_node)
    g.add_node(N.persist, persist_order_node)
    g.add_node(N.notify, send_notification_node)

    g.add_edge(START, N.agent)
    g.add_conditional_edges(
        N.agent,
        make_named_tools_route("fulfillment.route_after_coordinator_tools"),
        {"tools": N.tools, END: N.resolve_quote},
    )
    g.add_conditional_edges(N.tools, _route_after_checkout_tools)
    g.add_edge(N.resolve_quote, N.decide_fraud)
    g.add_conditional_edges(N.decide_fraud, _route_after_fraud_decision)
    g.add_conditional_edges(N.confirm_stock, _route_after_stock_check)
    g.add_conditional_edges(N.charge, _route_after_payment)
    g.add_edge(N.decrement, N.persist)
    g.add_conditional_edges(N.persist, _route_after_persist_order)
    g.add_edge(N.notify, END)

    compiled = g.compile()
    return compiled.with_config({
        "metadata": {"workflow_name": "fulfillment.workflow"},
        "run_name": "fulfillment.workflow",
    })


def _graph_result(result: dict, *, order: dict | None) -> dict:
    out = {
        "allow": result.get("allow", False),
        "quote": result.get("quote") or {},
        "fraud": result.get("fraud") or {},
        "inventory": result.get("inventory") or {},
        "failure_reason": result.get("failure_reason"),
    }
    if order is not None:
        out["order"] = result.get("order") or order
    return out


def run_fulfillment_graph(
    items: list[dict],
    total: float,
    *,
    order: dict | None = None,
    config=None,
) -> dict:
    """ReAct fulfillment + checkout pós-fraude quando `order` é passado."""
    resolved = resolve_config(config, feature="fulfillment")
    token = set_current_runnable_config(resolved)
    payload: dict = {"items": items, "total": total, "messages": [], "trace": []}
    if order is not None:
        payload["order"] = order
    try:
        result = build_fulfillment_graph().invoke(payload, config=resolved)
    finally:
        set_current_runnable_config(None, token)
    return _graph_result(result, order=order)


async def arun_fulfillment_graph(
    items: list[dict],
    total: float,
    *,
    order: dict | None = None,
    config=None,
) -> dict:
    resolved = resolve_config(config, feature="fulfillment")
    token = set_current_runnable_config(resolved)
    payload: dict = {"items": items, "total": total, "messages": [], "trace": []}
    if order is not None:
        payload["order"] = order
    try:
        result = await build_fulfillment_graph().ainvoke(payload, config=resolved)
    finally:
        set_current_runnable_config(None, token)
    return _graph_result(result, order=order)
