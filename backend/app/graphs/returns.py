"""Returns graph — ReAct coordinator + post-loop eligibility/abuse (F-GALILEO-11)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from ..tool_arg_normalize import format_tool_error
from .. import orders
from ..langchain_tools import (
    RETURNS_TOOLS,
    check_refund_eligibility_tool,
    policy_lookup_tool,
    process_refund_tool,
    refund_calc_tool,
    screen_refund_abuse_tool,
)
from ..problems import FLAGS
from ..tools import REFUND_WINDOW_DAYS
from .react import ReactState, invoke_react_agent, make_named_tools_route


@dataclass(frozen=True)
class ReturnsNodeNames:
    """Nós LangGraph returns — ids `{surface}.{business_step}`."""

    agent: str = "returns.coordinate_refund_request"
    tools: str = "returns.run_refund_policy_tools"
    resolve_policy: str = "returns.resolve_policy_and_calc"
    check_eligibility: str = "returns.check_refund_eligibility"
    screen_abuse: str = "returns.screen_refund_abuse"
    process_refund: str = "returns.process_refund"
    decide_and_process: str = "returns.decide_and_process_refund"


N = ReturnsNodeNames()


class ReturnsState(ReactState, total=False):
    order: dict
    policy: dict
    calc: dict
    elig: dict
    abuse: dict
    eligible: bool
    approved: bool
    refunded: bool
    refund_amount: float
    status: str
    reason: str
    steps: list[dict]
    updated_order: dict


def _delivered_at(order: dict) -> datetime | None:
    for h in order.get("history", []):
        if h["status"] == "DELIVERED":
            try:
                return datetime.fromisoformat(h["at"])
            except (ValueError, TypeError):
                return None
    return None


def _days_since_delivery(order: dict) -> float | None:
    at = _delivered_at(order)
    if at is None:
        return None
    return (datetime.now(timezone.utc) - at).total_seconds() / 86400.0


def _invoke_process_refund(order: dict, config: RunnableConfig | None) -> dict:
    """Choke point único — tool span L4 + mutação orders.transition."""
    raw = process_refund_tool.invoke(
        {"order_json": json.dumps(order)},
        config=config,
    )
    parsed = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    if parsed.get("refunded"):
        return orders.get_order(order["id"], advance=False) or order
    return order


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


def _initial_human(state: ReturnsState) -> HumanMessage:
    order = state["order"]
    return HumanMessage(
        content=(
            f"Coordinate a refund request for order {order['id']} (status {order['status']}, "
            f"total ${order.get('total', 0):.0f}): confirm eligibility, look up policy, "
            "compute the refund, screen for abuse, then process it if approved."
        )
    )


def agent_node(state: ReturnsState, config: RunnableConfig) -> dict:
    lc_messages = list(state.get("messages") or [])
    if not lc_messages:
        lc_messages = [_initial_human(state)]
    return invoke_react_agent(
        {**state, "messages": lc_messages},
        agent_name="returns_coordinator",
        workflow="returns",
        tools=RETURNS_TOOLS,
        feature="returns_coordinator",
        system_messages=[],
        trace_label="Returns coordinator",
        config=config,
    )


def resolve_policy_and_calc_node(state: ReturnsState, config: RunnableConfig) -> dict:
    """Normaliza policy/calc a partir do message history (fallback direto se ausente)."""
    messages = list(state.get("messages") or [])
    trace = list(state.get("trace") or [])
    order = state["order"]

    policy_raw = _tool_result_named(messages, "policy_lookup")
    calc_raw = _tool_result_named(messages, "refund_calc")
    policy: dict = policy_raw if isinstance(policy_raw, dict) else {}
    calc: dict = calc_raw if isinstance(calc_raw, dict) else {}

    if not policy:
        trace.append("Resolve policy: fallback policy_lookup tool")
        policy = policy_lookup_tool.invoke(
            {
                "order_id": order.get("id") or order.get("order_id") or "",
                "status": order.get("status", ""),
                "total": float(order.get("total", 0)),
            },
            config=config,
        )
    if not calc:
        trace.append("Resolve policy: fallback refund_calc tool")
        calc = refund_calc_tool.invoke(
            {
                "order_id": order.get("id") or order.get("order_id") or "",
                "status": order.get("status", ""),
                "total": float(order.get("total", 0)),
            },
            config=config,
        )

    trace.append(
        f"Resolve policy: refundable={policy.get('refundable')} amount={calc.get('amount')}"
    )
    return {"policy": policy, "calc": calc, "trace": trace}


def check_refund_eligibility_node(state: ReturnsState, config: RunnableConfig) -> dict:
    """Tool span `check_refund_eligibility` — input order, output JSON llm + efetivo."""
    order = state["order"]
    trace = list(state.get("trace") or [])

    raw = check_refund_eligibility_tool.invoke(
        {"order_json": json.dumps(order)},
        config=config,
    )
    elig = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    trace.append(
        "Eligibility: "
        f"llm={elig.get('llm_eligible')} → effective={elig.get('eligible')} ({elig.get('source')})"
    )
    return {"elig": elig, "trace": trace}


def screen_refund_abuse_node(state: ReturnsState, config: RunnableConfig) -> dict:
    """Tool span `screen_refund_abuse` — input order, output JSON llm + efetivo."""
    order = state["order"]
    trace = list(state.get("trace") or [])

    raw = screen_refund_abuse_tool.invoke(
        {"order_json": json.dumps(order)},
        config=config,
    )
    abuse = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    trace.append(
        "Abuse screen: "
        f"llm={abuse.get('llm_decision')} score={abuse.get('llm_score')} → "
        f"effective={abuse.get('decision')} ({abuse.get('source')}) allow={abuse.get('allow')}"
    )
    return {"abuse": abuse, "trace": trace}


def process_refund_node(state: ReturnsState, config: RunnableConfig) -> dict:
    order = state["order"]
    trace = list(state.get("trace") or [])
    updated = _invoke_process_refund(order, config)
    trace.append("Process refund: order marked REFUNDED")
    return {"updated_order": updated, "trace": trace}


def _effective_eligible(order: dict, *, apply_workshop_toggles: bool) -> tuple[bool, str]:
    days = _days_since_delivery(order)
    eligible_data = (
        order.get("status") == "DELIVERED"
        and days is not None
        and days <= REFUND_WINDOW_DAYS
    )
    false_denial = apply_workshop_toggles and FLAGS.refund_false_denial and eligible_data
    eligible = eligible_data and not false_denial
    if eligible:
        reason = f"Delivered {days:.0f} day(s) ago — within the {REFUND_WINDOW_DAYS}-day window."
    elif false_denial:
        reason = "Eligibility agent denied the request."
    elif order.get("status") != "DELIVERED":
        reason = "Only delivered orders can be refunded."
    else:
        reason = f"Outside the {REFUND_WINDOW_DAYS}-day return window."
    return eligible, reason


def _finalize_refund_outcome(
    state: ReturnsState,
    *,
    apply_workshop_toggles: bool = True,
    config: RunnableConfig | None = None,
) -> dict:
    order = state["order"]
    policy = dict(state.get("policy") or {})
    calc = dict(state.get("calc") or {})
    abuse = dict(state.get("abuse") or {})
    trace = list(state.get("trace") or [])

    eligible, elig_reason = _effective_eligible(order, apply_workshop_toggles=apply_workshop_toggles)
    approved = eligible and policy.get("refundable", False) and abuse.get("allow", False)
    updated = state.get("updated_order") or order

    if approved and updated.get("status") != "REFUNDED":
        updated = _invoke_process_refund(order, config)
        trace.append("Finalize: process_refund → REFUNDED")
    elif not approved:
        trace.append("Finalize: refund não processado")

    if approved:
        reason = f"Refund of ${calc.get('amount', 0):.0f} approved and processed."
    elif not abuse.get("allow", False):
        reason = "This request was flagged by our abuse screen — please contact support."
    else:
        reason = elig_reason

    steps = [
        {"label": "Eligibility check", "ok": eligible, "detail": elig_reason},
        {
            "label": "Policy lookup",
            "ok": policy.get("refundable", False),
            "detail": f"Delivered orders are refundable within {policy.get('window_days', REFUND_WINDOW_DAYS)} days.",
        },
        {"label": "Refund calculated", "ok": True, "detail": f"${calc.get('amount', 0):.0f} (full order total)."},
        {
            "label": "Abuse screen",
            "ok": abuse.get("allow", False),
            "detail": "Cleared." if abuse.get("allow", False) else "Flagged for review.",
        },
        {
            "label": "Refund processed",
            "ok": approved,
            "detail": "Order marked REFUNDED." if approved else "Not processed.",
        },
    ]

    return {
        "eligible": eligible,
        "approved": approved,
        "refunded": approved,
        "refund_amount": calc.get("amount", 0),
        "status": updated["status"],
        "reason": reason,
        "steps": steps,
        "updated_order": updated,
        "trace": trace,
    }


def decide_and_process_refund_node(state: ReturnsState, config: RunnableConfig) -> dict:
    from .. import galileo_control

    order = state["order"]
    return galileo_control.controlled_finalize_refund(
        order,
        lambda: _finalize_refund_outcome(state, apply_workshop_toggles=True, config=config),
        corrected_fn=lambda: _finalize_refund_outcome(state, apply_workshop_toggles=False, config=config),
    )


def _route_after_abuse_screen(state: ReturnsState) -> str:
    policy = state.get("policy") or {}
    elig = state.get("elig") or {}
    abuse = state.get("abuse") or {}
    if elig.get("eligible") and policy.get("refundable", False) and abuse.get("allow"):
        return N.process_refund
    return N.decide_and_process


_route_after_abuse_screen.__name__ = "returns.route_after_abuse_screen"


def build_returns_graph():
    """ReAct coordinator → resolve policy → eligibility → abuse → process/decide."""
    g = StateGraph(ReturnsState)
    g.add_node(N.agent, agent_node)
    g.add_node(N.tools, ToolNode(RETURNS_TOOLS, handle_tool_errors=format_tool_error))
    g.add_node(N.resolve_policy, resolve_policy_and_calc_node)
    g.add_node(N.check_eligibility, check_refund_eligibility_node)
    g.add_node(N.screen_abuse, screen_refund_abuse_node)
    g.add_node(N.process_refund, process_refund_node)
    g.add_node(N.decide_and_process, decide_and_process_refund_node)

    g.add_edge(START, N.agent)
    g.add_conditional_edges(
        N.agent,
        make_named_tools_route("returns.route_after_coordinator_tools"),
        {"tools": N.tools, END: N.resolve_policy},
    )
    g.add_edge(N.tools, N.agent)
    g.add_edge(N.resolve_policy, N.check_eligibility)
    g.add_edge(N.check_eligibility, N.screen_abuse)
    g.add_conditional_edges(N.screen_abuse, _route_after_abuse_screen)
    g.add_edge(N.process_refund, N.decide_and_process)
    g.add_edge(N.decide_and_process, END)

    compiled = g.compile()
    return compiled.with_config({
        "metadata": {"workflow_name": "returns.workflow"},
        "run_name": "returns.workflow",
    })
