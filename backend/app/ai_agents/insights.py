"""Standalone admin and account insight workflows.

Both workflows read only store-backed data, build compact aggregates locally, and own
their provider cascade and optional Galileo Agent Control boundary.
"""
from __future__ import annotations

import contextvars
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from ..llm.agent_llm_invoke import LLMResult, invoke_feature_llm, is_stub_output
from ..obs import galileo_obs
from ..problems import FLAGS
from ..settings import settings
from ..store import db, orders
from ..store.catalog_format import _usd
from ..store.tools import CATALOG
from ..store.users import GOLD_THRESHOLD, PLATINUM_THRESHOLD

ADMIN_CONTROL_STEP_NAME = "admin_insights"
ACCOUNT_CONTROL_STEP_NAME = "account_insights"
ADMIN_LLM_RUN_NAME = "feature.admin_insights"
ACCOUNT_LLM_RUN_NAME = "feature.account_insights"
_SYSTEM_PROMPT = (
    "You write concise ecommerce insights grounded strictly in the supplied aggregate data. "
    "Do not invent figures or products. Reply in English with raw JSON only."
)
_PAID_STATUSES = ("PAID", "SHIPPED", "DELIVERED")
_TIER_BENEFITS = {
    "STANDARD": "free order tracking and AI concierge picks",
    "GOLD": "priority support, early access to deals, and AI concierge picks",
    "PLATINUM": "concierge-level support, first access to every drop, and exclusive perks",
}
_invoke_fn_var: contextvars.ContextVar[Callable[[str], tuple[LLMResult, str]] | None] = (
    contextvars.ContextVar("insights_invoke", default=None)
)
_result_sink_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "insights_result_sink", default=None,
)
_control_handlers: dict[str, Callable] = {}


def _is_unavailable_reply(text: str) -> bool:
    return is_stub_output(text) or (text or "").strip().startswith(("The AI provider", "The AI assistant"))


def _parse_json(text: str) -> dict | None:
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start == -1 or end < start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _is_stub(result: LLMResult) -> bool:
    return result.system == "stub" or _is_unavailable_reply(result.text)


def _maybe_latency() -> None:
    if FLAGS.latency_spike:
        time.sleep(1.2)


def _invoke_llm(
    control_step: str,
    run_name: str,
    prompt: str,
    *,
    config=None,
) -> tuple[LLMResult, str]:
    return invoke_feature_llm(
        control_step,
        _SYSTEM_PROMPT,
        prompt,
        run_name=run_name,
        max_tokens=240,
        config=config,
    ), "miss"


def _control_is_active() -> bool:
    if not galileo_obs.is_enabled():
        return False
    try:
        import agent_control  # noqa: F401
    except ImportError:
        return False
    return True


def _registered_control_handler(control_step: str):
    handler = _control_handlers.get(control_step)
    if handler is not None:
        return handler
    from agent_control import control

    @control(step_name=control_step)
    def controlled(prompt: str) -> str:
        invoke = _invoke_fn_var.get()
        if invoke is None:
            raise RuntimeError("missing insights invoke function")
        result = invoke(prompt)
        sink = _result_sink_var.get()
        if sink is not None:
            sink["result"] = result
        return result[0].text

    _control_handlers[control_step] = controlled
    return controlled


def _controlled_invoke(
    control_step: str,
    prompt: str,
    invoke: Callable[[str], tuple[LLMResult, str]],
) -> tuple[str, LLMResult, str]:
    if not _control_is_active():
        result, status = invoke(prompt)
        return result.text, result, status
    try:
        handler = _registered_control_handler(control_step)
    except Exception:  # noqa: BLE001 - Agent Control is optional
        result, status = invoke(prompt)
        return result.text, result, status
    sink: dict = {}
    invoke_token = _invoke_fn_var.set(invoke)
    sink_token = _result_sink_var.set(sink)
    try:
        text = handler(prompt)
        result, status = sink.get("result") or invoke(prompt)
        return text, result, status
    except Exception as exc:
        if type(exc).__name__ == "ControlViolationError":
            text = "I can only provide insights grounded in the available store data."
            return text, LLMResult(text, 0, 0, "control-block", system="control"), "control_block"
        raise
    finally:
        _invoke_fn_var.reset(invoke_token)
        _result_sink_var.reset(sink_token)


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _item_name(item: dict) -> str:
    name = item.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    product = next((entry for entry in CATALOG if entry["sku"] == item.get("sku")), None)
    return product["name"] if product else str(item.get("sku") or "Unknown item")


def _item_qty(item: dict) -> int:
    try:
        return max(0, int(item.get("qty") or 0))
    except (TypeError, ValueError):
        return 0


def _accumulate_units(units: dict[str, int], items: list[dict]) -> None:
    for item in items:
        quantity = _item_qty(item)
        if quantity:
            name = _item_name(item)
            units[name] = units.get(name, 0) + quantity


def _admin_aggregates() -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.admin_insights_window_days)
    recent = [
        order for order in orders.list_orders()
        if (created_at := _parse_iso(order["created_at"])) and created_at >= cutoff
    ]
    paid = [order for order in recent if order["status"] in _PAID_STATUSES]
    failed = [order for order in recent if order["status"] == "FAILED"]
    units: dict[str, int] = {}
    for order in paid:
        _accumulate_units(units, order["items"])
    revenue = round(sum(order["total"] for order in paid), 2)
    restock = sorted(
        [
            {"sku": product["sku"], "name": product["name"], "stock": product["stock"]}
            for product in CATALOG if product["stock"] <= settings.admin_restock_at
        ],
        key=lambda product: product["stock"],
    )
    return {
        "window_days": settings.admin_insights_window_days,
        "orders": len(recent),
        "paid": len(paid),
        "failed": len(failed),
        "failed_rate": round(len(failed) / len(recent), 2) if recent else 0.0,
        "revenue": revenue,
        "avg_ticket": round(revenue / len(paid), 2) if paid else 0.0,
        "top_products": sorted(units.items(), key=lambda item: item[1], reverse=True)[:3],
        "restock": restock,
    }


def _admin_context(aggregate: dict) -> str:
    top = ", ".join(f"{name} ({quantity})" for name, quantity in aggregate["top_products"]) or "—"
    low = ", ".join(f"{item['name']} ({item['stock']} left)" for item in aggregate["restock"]) or "none"
    return (
        f"Window: last {aggregate['window_days']} days\n"
        f"Orders: {aggregate['orders']} (paid {aggregate['paid']}, failed {aggregate['failed']}, "
        f"failed rate {aggregate['failed_rate']:.0%})\n"
        f"Revenue: {_usd(aggregate['revenue'])} · Avg ticket: {_usd(aggregate['avg_ticket'])}\n"
        f"Top sellers (units): {top}\nLow / out of stock: {low}"
    )


def _admin_fallback(aggregate: dict, grounded: bool) -> dict:
    if not grounded:
        return {
            "summary": "Sales are skyrocketing — revenue tripled this week across every category!",
            "anomalies": ["Unusual spike detected in an unspecified region."],
        }
    summary = (
        f"{aggregate['paid']} paid order(s) in the last {aggregate['window_days']} days for "
        f"{_usd(aggregate['revenue'])} (avg ticket {_usd(aggregate['avg_ticket'])})."
    )
    anomalies: list[str] = []
    if aggregate["orders"] and aggregate["failed_rate"] >= 0.3:
        anomalies.append(
            f"High failed-order rate ({aggregate['failed_rate']:.0%}) — check fraud/payment toggles."
        )
    out_of_stock = [item["name"] for item in aggregate["restock"] if item["stock"] == 0]
    if out_of_stock:
        anomalies.append(f"Out of stock: {', '.join(out_of_stock)}.")
    if not aggregate["orders"]:
        anomalies.append(f"No orders in the last {aggregate['window_days']} days.")
    return {"summary": summary, "anomalies": anomalies}


def admin_insights(*, config=None) -> dict:
    aggregate = _admin_aggregates()
    grounded = not FLAGS.price_hallucination
    _maybe_latency()
    prompt = (
        f"{_admin_context(aggregate)}\n\nWrite a brief sales summary and flag anomalies for the "
        "store owner. Use ONLY the numbers above; do not invent figures. Return ONLY JSON: "
        '{"summary": "<2-3 sentence executive summary>", "anomalies": ["<short alert>", ...]}. '
        "Use an empty array if nothing is unusual. Reply in English."
        if grounded else
        "Write a brief sales summary and flag anomalies for an online store this week. Return ONLY "
        'JSON: {"summary": "<2-3 sentences>", "anomalies": ["<alert>", ...]}. Reply in English.'
    )

    def invoke(current_prompt: str = prompt):
        return _invoke_llm(ADMIN_CONTROL_STEP_NAME, ADMIN_LLM_RUN_NAME, current_prompt, config=config)

    text, result, _ = _controlled_invoke(ADMIN_CONTROL_STEP_NAME, prompt, invoke)
    parsed = None if _is_stub(result) else _parse_json(text)
    fallback = _admin_fallback(aggregate, grounded)
    parsed = parsed or fallback
    anomalies = [
        entry.strip() for entry in (parsed.get("anomalies") or [])
        if isinstance(entry, str) and entry.strip()
    ]
    return {
        "period_days": aggregate["window_days"],
        "metrics": {
            "orders": aggregate["orders"], "paid": aggregate["paid"], "failed": aggregate["failed"],
            "revenue": aggregate["revenue"], "avg_ticket": aggregate["avg_ticket"],
        },
        "summary": (parsed.get("summary") or "").strip() or fallback["summary"],
        "anomalies": anomalies,
        "restock": aggregate["restock"],
    }


def _account_summary(user: dict, user_orders: list[dict]) -> dict:
    paid = [order for order in user_orders if order["status"] in _PAID_STATUSES]
    units: dict[str, int] = {}
    for order in paid:
        _accumulate_units(units, order["items"])
    last = user_orders[0] if user_orders else None
    last_line = None
    if last is not None:
        items = ", ".join(f"{_item_qty(item)}× {_item_name(item)}" for item in last["items"]) or "—"
        last_line = f"{items} ({last['status']})"
    return {
        "name": user["name"],
        "tier": user["tier"],
        "spend": round(user["spend"], 2),
        "orders": len(user_orders),
        "paid": len(paid),
        "top_products": sorted(units.items(), key=lambda item: item[1], reverse=True)[:3],
        "last": last_line,
    }


def _account_context(summary: dict) -> str:
    top = ", ".join(f"{name} (×{quantity})" for name, quantity in summary["top_products"]) or "—"
    lines = [
        f"Customer: {summary['name']}",
        f"Membership tier: {summary['tier']} — benefits: {_TIER_BENEFITS.get(summary['tier'], '')}",
        f"Total spent: {_usd(summary['spend'])}",
        f"Orders placed: {summary['orders']} (paid {summary['paid']})",
        f"Most bought: {top}",
    ]
    if summary["last"]:
        lines.append(f"Latest order: {summary['last']}")
    if summary["tier"] == "STANDARD":
        gap = max(GOLD_THRESHOLD - summary["spend"], 0)
        lines.append(f"Next tier: GOLD at {_usd(GOLD_THRESHOLD)} total spend ({_usd(gap)} more needed to reach it).")
    elif summary["tier"] == "GOLD":
        gap = max(PLATINUM_THRESHOLD - summary["spend"], 0)
        lines.append(f"Next tier: PLATINUM at {_usd(PLATINUM_THRESHOLD)} total spend ({_usd(gap)} more needed to reach it).")
    return "\n".join(lines)


def _account_fallback(summary: dict, grounded: bool) -> dict:
    if not grounded:
        return {
            "summary": "You've spent over $50,000 with us this month across 200+ orders — incredible!",
            "tier_benefits": "As a Diamond Elite member you get a free car with every purchase.",
            "repurchase": "Time to reorder your usual case of 500 gift cards.",
        }
    if summary["orders"] == 0:
        return {
            "summary": "You haven't placed any orders yet — your history will appear here.",
            "tier_benefits": f"As a {summary['tier']} member you enjoy {_TIER_BENEFITS.get(summary['tier'], '')}.",
            "repurchase": "Browse the store to find your first favorite.",
        }
    favorite = summary["top_products"][0][0] if summary["top_products"] else None
    return {
        "summary": (
            f"You've placed {summary['orders']} order(s) with {summary['paid']} completed, "
            f"totaling {_usd(summary['spend'])}."
        ),
        "tier_benefits": f"As a {summary['tier']} member you enjoy {_TIER_BENEFITS.get(summary['tier'], '')}.",
        "repurchase": (
            f"Loved your {favorite}? It might be time for another."
            if favorite else "Check out what's new since your last visit."
        ),
    }


def account_insights(user: dict, user_orders: list[dict], *, config=None) -> dict:
    summary = _account_summary(user, user_orders)
    grounded = not FLAGS.price_hallucination
    _maybe_latency()
    prompt = (
        f"{_account_context(summary)}\n\nWrite a short, warm account overview for this shopper. "
        "Use ONLY the data above; do not invent figures. Mention only products listed above under "
        "'Most bought' — never a product not listed there. All amounts are in US dollars — use the "
        "$ sign, never £ or any other currency. Return ONLY JSON: "
        '{"summary": "<1-2 sentence recap of their buying patterns>", '
        '"tier_benefits": "<1 sentence explaining their current tier perks and, if not PLATINUM, '
        'how close they are to the next tier — use the precomputed gap above, do not recompute it>", '
        '"repurchase": "<1 sentence suggesting something to buy again, grounded in their history>"}. '
        "Reply in English."
        if grounded else
        "Write a short, warm account overview for a returning shopper. Return ONLY JSON: "
        '{"summary": "<1-2 sentences>", "tier_benefits": "<1 sentence>", '
        '"repurchase": "<1 sentence>"}. Reply in English.'
    )

    def invoke(current_prompt: str = prompt):
        return _invoke_llm(ACCOUNT_CONTROL_STEP_NAME, ACCOUNT_LLM_RUN_NAME, current_prompt, config=config)

    text, result, _ = _controlled_invoke(ACCOUNT_CONTROL_STEP_NAME, prompt, invoke)
    parsed = None if _is_stub(result) else _parse_json(text)
    fallback = _account_fallback(summary, grounded)
    parsed = parsed or fallback
    return {
        "summary": (parsed.get("summary") or "").strip() or fallback["summary"],
        "tier_benefits": (parsed.get("tier_benefits") or "").strip() or fallback["tier_benefits"],
        "repurchase": (parsed.get("repurchase") or "").strip() or fallback["repurchase"],
        "grounded": grounded,
    }
