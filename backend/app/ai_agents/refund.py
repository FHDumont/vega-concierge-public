"""UC-3 refund workflow, isolated from the legacy agent and graph packages."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig

from ..obs import galileo_control
from ..problems import FLAGS
from ..runnable_config import resolve_config, set_current_runnable_config
from ..store.langchain_tools import (
    check_refund_eligibility_tool,
    policy_lookup_tool,
    process_refund_tool,
    refund_calc_tool,
    screen_refund_abuse_tool,
)
from ..store.tools import REFUND_WINDOW_DAYS

WORKFLOW_NAME = "returns.workflow"
POLICY_TOOLS_RUN_NAME = "returns.run_refund_policy_tools"
ELIGIBILITY_RUN_NAME = "returns.check_refund_eligibility"
ABUSE_RUN_NAME = "returns.screen_refund_abuse"
PROCESS_RUN_NAME = "returns.process_refund"
DECIDE_RUN_NAME = "returns.decide_and_process_refund"
FINALIZE_STEP_NAME = "returns.finalize"


def _decode_json(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return dict(value) if isinstance(value, dict) else {}


def _order_json(order: dict) -> str:
    return json.dumps(order)


def _delivered_at(order: dict) -> datetime | None:
    for event in order.get("history", []):
        if event.get("status") != "DELIVERED":
            continue
        try:
            return datetime.fromisoformat(event["at"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def _days_since_delivery(order: dict) -> float | None:
    delivered_at = _delivered_at(order)
    if delivered_at is None:
        return None
    return (datetime.now(timezone.utc) - delivered_at).total_seconds() / 86400.0


def refund_request_text(order: dict) -> str:
    """Return the stable, human-readable root input for the UC-3 workflow."""
    return (
        f"Coordinate a refund request for order {order['id']} (status {order['status']}, "
        f"total ${order.get('total', 0):.2f}): look up policy, compute the refund, screen for "
        "abuse, then process it if approved. Eligibility is already decided by a separate step."
    )


def _eligibility(order: dict, *, apply_workshop_toggles: bool) -> tuple[bool, str]:
    days = _days_since_delivery(order)
    data_eligible = (
        order.get("status") == "DELIVERED"
        and days is not None
        and days <= REFUND_WINDOW_DAYS
    )
    false_denial = apply_workshop_toggles and FLAGS.refund_false_denial and data_eligible
    if data_eligible and not false_denial:
        return True, f"Delivered {days:.0f} day(s) ago — within the {REFUND_WINDOW_DAYS}-day window."
    if false_denial:
        return False, "Refund denied by the eligibility review."
    if order.get("status") != "DELIVERED":
        return False, "Only delivered orders can be refunded."
    return False, f"Outside the {REFUND_WINDOW_DAYS}-day return window."


def _build_refund_outcome(
    order: dict,
    policy: dict,
    calculation: dict,
    *,
    updated_order: dict,
    apply_workshop_toggles: bool,
) -> dict:
    eligible, eligibility_reason = _eligibility(
        order, apply_workshop_toggles=apply_workshop_toggles
    )
    approved = eligible and bool(policy.get("refundable"))
    amount = calculation.get("amount", 0)
    if approved:
        reason = f"Refund of ${amount:.0f} approved and processed."
    else:
        reason = (
            f"We're sorry — your refund was denied. {eligibility_reason} "
            "If you believe this is a mistake, contact support with your order number."
        )

    steps = [
        {"label": "Eligibility check", "ok": eligible, "detail": eligibility_reason},
        {
            "label": "Policy lookup",
            "ok": bool(policy.get("refundable")),
            "detail": f"Delivered orders are refundable within {policy.get('window_days', REFUND_WINDOW_DAYS)} days.",
        },
        {"label": "Refund calculated", "ok": True, "detail": f"${amount:.0f} (full order total)."},
        {"label": "Abuse screen", "ok": True, "detail": "Cleared."},
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
        "refund_amount": amount,
        "status": updated_order["status"],
        "reason": reason,
        "steps": steps,
        "updated_order": updated_order,
    }


async def _run_policy_tools(state: dict, config: RunnableConfig) -> dict:
    order = state["order"]
    order_args = {
        "order_id": order.get("id"),
        "status": order.get("status"),
        "total": order.get("total", 0),
    }
    policy = policy_lookup_tool.invoke(order_args, config=config)
    calculation = refund_calc_tool.invoke(order_args, config=config)
    return {**state, "policy": policy, "calculation": calculation}


async def _run_eligibility(state: dict, config: RunnableConfig) -> dict:
    order = state["order"]
    check_refund_eligibility_tool.invoke({"order_json": _order_json(order)}, config=config)
    return state


async def _run_abuse_screen(state: dict, config: RunnableConfig) -> dict:
    order = state["order"]
    screen_refund_abuse_tool.invoke({"order_json": _order_json(order)}, config=config)
    return state


async def _run_process_refund(state: dict, config: RunnableConfig) -> dict:
    order = state["order"]
    policy = state["policy"]
    eligible, _ = _eligibility(order, apply_workshop_toggles=False)
    pre_approved = eligible and bool(policy.get("refundable"))
    updated_order = order
    if pre_approved:
        raw = process_refund_tool.invoke({"order_json": _order_json(order)}, config=config)
        processed = _decode_json(raw)
        if processed.get("refunded"):
            updated_order = {
                **order,
                "id": processed.get("order_id") or order.get("id"),
                "status": processed.get("status", "REFUNDED"),
            }
    return {**state, "updated_order": updated_order}


async def _decide_and_process(state: dict, config: RunnableConfig) -> dict:
    del config
    order = state["order"]
    updated_order = state.get("updated_order", order)
    policy = state["policy"]
    calculation = state["calculation"]
    outcome = galileo_control.controlled_finalize_refund(
        order,
        lambda: _build_refund_outcome(
            order, policy, calculation,
            updated_order=updated_order,
            apply_workshop_toggles=True,
        ),
        corrected_fn=lambda: _build_refund_outcome(
            order, policy, calculation,
            updated_order=updated_order,
            apply_workshop_toggles=False,
        ),
    )
    return {
        "eligible": outcome["eligible"],
        "approved": outcome["approved"],
        "refunded": outcome["refunded"],
        "refund_amount": outcome["refund_amount"],
        "status": outcome["status"],
        "reason": outcome["reason"],
        "steps": outcome["steps"],
        "order": outcome["updated_order"],
    }


def build_returns_workflow() -> RunnableLambda:
    """Build the isolated root runnable with the established UC-3 trace name."""
    policy = RunnableLambda(_run_policy_tools, name=POLICY_TOOLS_RUN_NAME).with_config(
        {"run_name": POLICY_TOOLS_RUN_NAME, "name": POLICY_TOOLS_RUN_NAME},
    )
    eligibility = RunnableLambda(_run_eligibility, name=ELIGIBILITY_RUN_NAME).with_config(
        {"run_name": ELIGIBILITY_RUN_NAME, "name": ELIGIBILITY_RUN_NAME},
    )
    abuse = RunnableLambda(_run_abuse_screen, name=ABUSE_RUN_NAME).with_config(
        {"run_name": ABUSE_RUN_NAME, "name": ABUSE_RUN_NAME},
    )
    process = RunnableLambda(_run_process_refund, name=PROCESS_RUN_NAME).with_config(
        {"run_name": PROCESS_RUN_NAME, "name": PROCESS_RUN_NAME},
    )
    decide = RunnableLambda(_decide_and_process, name=DECIDE_RUN_NAME).with_config(
        {"run_name": DECIDE_RUN_NAME, "name": DECIDE_RUN_NAME},
    )
    return (policy | eligibility | abuse | process | decide).with_config({
        "run_name": WORKFLOW_NAME,
        "name": WORKFLOW_NAME,
        "metadata": {"workflow_name": WORKFLOW_NAME},
    })


async def arun_refund(order: dict, *, config=None) -> dict:
    """Run UC-3 and preserve the ``returns.workflow`` / ``returns.finalize`` control contract."""
    resolved = resolve_config(config, feature="returns", order_id=order.get("id"))
    token = set_current_runnable_config(resolved)
    try:
        return await build_returns_workflow().ainvoke({"order": order}, config=resolved)
    finally:
        set_current_runnable_config(None, token)
