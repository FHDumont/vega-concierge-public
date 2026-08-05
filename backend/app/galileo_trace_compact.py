"""Compact LangChain chain I/O before Splunk Agent Observability export (F-TRACE-UX-1 follow-up).

The Galileo callback serializes full LangGraph state at the trace root — including message
history, catalog candidates, and retriever payloads. Workshop traces should show the shopper
reply preview instead; child spans keep their own compact outputs.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

_MAX_PREVIEW = 200
_MAX_LIST = 8


def _preview(text: str, *, limit: int = _MAX_PREVIEW) -> str:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 1].rstrip() + "…"


def _compact_product(p: dict | None) -> dict | None:
    if not isinstance(p, dict):
        return None
    return {
        "sku": p.get("sku"),
        "name": p.get("name"),
        "price": p.get("price"),
    }


def _compact_chat_state(data: dict) -> dict:
    artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), dict) else {}
    return {
        "intent": data.get("intent"),
        "answer_preview": _preview(str(data.get("answer") or "")),
        "language": data.get("language"),
        "artifact_keys": sorted(artifacts.keys())[:_MAX_LIST],
        "quality": data.get("quality"),
        "trace_steps": len(data.get("trace") or []),
    }


def _compact_concierge_state(data: dict) -> dict:
    candidates = data.get("candidates") or []
    compact_cands = []
    for item in candidates[:_MAX_LIST]:
        if isinstance(item, dict):
            compact_cands.append(_compact_product(item))
    out: dict[str, Any] = {
        "answer_preview": _preview(str(data.get("answer") or "")),
        "language": data.get("language"),
        "selected": _compact_product(data.get("selected")),
        "candidates_count": len(candidates),
        "candidate_skus": [c.get("sku") for c in compact_cands if c and c.get("sku")],
        "quality": data.get("quality"),
        "trace_steps": len(data.get("trace") or []),
    }
    return out


def _compact_fulfillment_state(data: dict) -> dict:
    inventory = data.get("inventory") if isinstance(data.get("inventory"), dict) else {}
    return {
        "allow": data.get("allow"),
        "checkout_success": data.get("checkout_success"),
        "stock_ok": data.get("stock_ok"),
        "order_id": (data.get("order") or {}).get("id") if isinstance(data.get("order"), dict) else None,
        "order_status": (data.get("order") or {}).get("status") if isinstance(data.get("order"), dict) else None,
        "payment_paid": (data.get("payment") or {}).get("paid") if isinstance(data.get("payment"), dict) else None,
        # UC-2 (Tool Errors evaluator) reads the root output — without `failure_reason`/
        # `inventory.error` here, "stock_ok=true" reads as a clean success and the check_inventory
        # 503 (buried in a child span) never surfaces at trace-root level.
        "failure_reason": data.get("failure_reason"),
        "inventory_error": inventory.get("error"),
        "trace_steps": len(data.get("trace") or []),
    }


def compact_trace_payload(data: Any, *, name: str | None = None) -> Any:
    """Shrink chain inputs/outputs for workflow root spans only."""
    if data is None:
        return data
    if isinstance(data, str):
        return _preview(data, limit=500)
    if not isinstance(data, dict):
        text = str(data)
        return _preview(text, limit=500) if len(text) > 500 else data

    run = (name or "").lower()
    if "chat.workflow" in run or (
        data.get("intent") is not None and "answer" in data and "messages" not in data
    ):
        return _compact_chat_state(data)
    if "concierge.workflow" in run or (
        "answer" in data and ("candidates" in data or "selected" in data)
    ):
        return _compact_concierge_state(data)
    if "fulfillment.workflow" in run or "returns.workflow" in run or "checkout_success" in data:
        return _compact_fulfillment_state(data)

    if "messages" in data and len(json.dumps(data, default=str)) > 1500:
        compact = dict(data)
        compact.pop("messages", None)
        if "candidates" in compact and isinstance(compact["candidates"], list):
            compact["candidates"] = [
                _compact_product(c) for c in compact["candidates"][:_MAX_LIST] if isinstance(c, dict)
            ]
            compact["candidates_count"] = len(data.get("candidates") or [])
        if "artifacts" in compact and isinstance(compact["artifacts"], dict):
            compact["artifact_keys"] = sorted(compact["artifacts"].keys())[:_MAX_LIST]
            compact.pop("artifacts", None)
        if "answer" in compact:
            compact["answer_preview"] = _preview(str(compact.pop("answer", "") or ""))
        return compact

    serialized = json.dumps(data, default=str)
    if len(serialized) <= 1500:
        return data
    return {"preview": _preview(serialized, limit=500), "bytes": len(serialized)}


def should_compact_workflow_io(name: str | None, parent_run_id: UUID | None) -> bool:
    """Compact only LangGraph workflow roots — avoids breaking nested chain spans."""
    if parent_run_id is not None:
        return False
    run = (name or "").lower()
    return run.endswith(".workflow") or run in {
        "chat.workflow",
        "concierge.workflow",
        "fulfillment.workflow",
        "returns.workflow",
        "compare.workflow",
    }
