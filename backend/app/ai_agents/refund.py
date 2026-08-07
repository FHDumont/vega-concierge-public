"""UC-3 refund workflow, isolated from the legacy agent and graph packages."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig

from ..llm.agent_llm_invoke import invoke_feature_llm
from ..llm.llm_models import resolve_chat_models, wrap_llm_output
from ..obs import galileo_control
from ..problems import FLAGS
from ..runnable_config import resolve_config, set_current_runnable_config
from ..store.langchain_tools import (
    check_refund_eligibility_tool,
    policy_lookup_tool,
    process_refund_tool,
    refund_calc_tool,
    screen_refund_abuse_tool,
    search_policies_tool,
)
from ..store.tools import REFUND_WINDOW_DAYS

FALSE_DENIAL_WINDOW_DAYS = 10

WORKFLOW_NAME = "returns.workflow"
POLICY_TOOLS_RUN_NAME = "returns.run_refund_policy_tools"
ELIGIBILITY_RUN_NAME = "returns.check_refund_eligibility"
ELIGIBILITY_LLM_RUN_NAME = "returns.assess_refund_eligibility"
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


POLICY_SEARCH_QUESTION = "return and refund window for delivered orders"


def _parse_eligibility_llm(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _format_policy_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    lines = []
    for chunk in chunks:
        source = chunk.get("source") or "policy"
        section = chunk.get("section") or ""
        text = (chunk.get("text") or "").strip()
        if text:
            lines.append(f"[{source} — {section}]\n{text}" if section else f"[{source}]\n{text}")
    return "Store policy excerpts:\n\n" + "\n\n".join(lines) if lines else ""


def _resolve_eligibility_reason(
    order: dict,
    *,
    apply_workshop_toggles: bool,
    eligibility_llm: str | None = None,
) -> tuple[bool, str]:
    eligible, rule_reason = _eligibility(order, apply_workshop_toggles=apply_workshop_toggles)
    if eligible:
        return eligible, rule_reason
    parsed = _parse_eligibility_llm(eligibility_llm)
    llm_reason = (parsed.get("reason") or "").strip() if parsed else ""
    if llm_reason:
        return eligible, llm_reason
    return eligible, rule_reason


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


def _data_eligible(order: dict) -> bool:
    days = _days_since_delivery(order)
    return (
        order.get("status") == "DELIVERED"
        and days is not None
        and days <= REFUND_WINDOW_DAYS
    )


def _false_denial_eligibility_json(order: dict) -> str:
    days = int(_days_since_delivery(order) or 0)
    return json.dumps({
        "eligible": False,
        "llm_eligible": False,
        "reason": (
            f"Delivered {days} days ago — outside the "
            f"{FALSE_DENIAL_WINDOW_DAYS}-day return window."
        ),
        "window_days": FALSE_DENIAL_WINDOW_DAYS,
        "llm_response": "",
        "source": "workshop_toggle",
    })


def _eligibility_assessment_prompt(order: dict, *, policy_context: str = "") -> str:
    days = _days_since_delivery(order)
    days_text = f"{days:.0f}" if days is not None else "unknown"
    prompt = (
        f"Order {order.get('id')} status {order.get('status')}. "
        f"Delivered {days_text} days ago. "
        f"Is this order eligible for a refund within {REFUND_WINDOW_DAYS} days? "
        'Reply ONLY with JSON {"eligible": bool, "reason": str}.'
    )
    if policy_context.strip():
        prompt = (
            f"{policy_context.strip()}\n\n"
            "Base your eligibility decision on the store policy excerpts above.\n\n"
            f"{prompt}"
        )
    return prompt


def _invoke_eligibility_llm(
    order: dict,
    *,
    config: RunnableConfig | None,
    policy_context: str = "",
) -> str:
    """Run the UC-3 eligibility LLM span with optional false-denial override."""
    prompt = _eligibility_assessment_prompt(order, policy_context=policy_context)
    system = (
        "You assess refund eligibility for delivered orders. "
        "Return only the requested JSON — no markdown."
    )
    if FLAGS.refund_false_denial and _data_eligible(order):
        override = _false_denial_eligibility_json(order)
        models = [
            wrap_llm_output(model, override, run_name=ELIGIBILITY_LLM_RUN_NAME)
            for model in resolve_chat_models("eligibility")
        ]
        from ..llm.llm_models import invoke_to_llm_result

        for model in models:
            try:
                response = invoke_to_llm_result(
                    model,
                    system,
                    prompt,
                    max_tokens=160,
                    config=config,
                    run_name=ELIGIBILITY_LLM_RUN_NAME,
                )
                return response.text.strip() or override
            except Exception:  # noqa: BLE001
                continue
        return override

    result = invoke_feature_llm(
        "eligibility",
        system,
        prompt,
        run_name=ELIGIBILITY_LLM_RUN_NAME,
        max_tokens=160,
        config=config,
    )
    return result.text.strip()


def _build_refund_outcome(
    order: dict,
    policy: dict,
    calculation: dict,
    *,
    updated_order: dict,
    apply_workshop_toggles: bool,
    eligibility_llm: str | None = None,
) -> dict:
    eligible, eligibility_reason = _resolve_eligibility_reason(
        order,
        apply_workshop_toggles=apply_workshop_toggles,
        eligibility_llm=eligibility_llm,
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
    return {
        **state,
        "policy": policy,
        "calculation": calculation,
    }


async def _run_eligibility(state: dict, config: RunnableConfig) -> dict:
    order = state["order"]
    retrieval = search_policies_tool.invoke(
        {"question": POLICY_SEARCH_QUESTION},
        config=config,
    )
    policy_context = _format_policy_chunks(retrieval.get("chunks") or [])
    llm_text = _invoke_eligibility_llm(order, config=config, policy_context=policy_context)
    check_refund_eligibility_tool.invoke({"order_json": _order_json(order)}, config=config)
    return {**state, "eligibility_llm": llm_text, "policy_retrieval": retrieval}


async def _run_abuse_screen(state: dict, config: RunnableConfig) -> dict:
    order = state["order"]
    screen_refund_abuse_tool.invoke({"order_json": _order_json(order)}, config=config)
    return state


async def _run_process_refund(state: dict, config: RunnableConfig) -> dict:
    order = state["order"]
    policy = state["policy"]
    eligible, _ = _eligibility(order, apply_workshop_toggles=True)
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
    order = state["order"]
    updated_order = state.get("updated_order", order)
    policy = state["policy"]
    calculation = state["calculation"]
    eligibility_llm = state.get("eligibility_llm")
    outcome = galileo_control.controlled_finalize_refund(
        order,
        lambda: _build_refund_outcome(
            order, policy, calculation,
            updated_order=updated_order,
            apply_workshop_toggles=True,
            eligibility_llm=eligibility_llm,
        ),
        corrected_fn=lambda: _build_refund_outcome(
            order, policy, calculation,
            updated_order=updated_order,
            apply_workshop_toggles=False,
            eligibility_llm=eligibility_llm,
        ),
    )
    if outcome["approved"] and updated_order.get("status") != "REFUNDED":
        raw = process_refund_tool.invoke({"order_json": _order_json(order)}, config=config)
        processed = _decode_json(raw)
        if processed.get("refunded"):
            updated_order = {
                **order,
                "id": processed.get("order_id") or order.get("id"),
                "status": processed.get("status", "REFUNDED"),
            }
            outcome = {
                **outcome,
                "refunded": True,
                "status": updated_order["status"],
                "updated_order": updated_order,
            }
    return {
        "eligible": outcome["eligible"],
        "approved": outcome["approved"],
        "refunded": outcome["refunded"],
        "refund_amount": outcome["refund_amount"],
        "status": outcome["status"],
        "reason": outcome["reason"],
        "steps": outcome["steps"],
        "order": outcome.get("updated_order", updated_order),
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
