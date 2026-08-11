"""Normalize malformed LangChain tool args before strict validation (F-TRACE-UX-1)."""
from __future__ import annotations

import json
import re
from typing import Any

_SKU_PATTERN = re.compile(r"NS-\d{3}", re.I)


def format_tool_error(error: Exception) -> str:
    """Short JSON for ToolNode residual errors — no stack trace in span output.

    Without `"tool"` in payload: the name is already in the span and `ToolMessage.name` — LangGraph calls the
    handler with just the exception (infers the handled type by the annotation of the 1st parameter), so an
    explicit `tool=` never got passed, and the field always came out as `"unknown"`."""
    message = str(error).strip()
    if len(message) > 200:
        message = message[:197] + "..."
    return json.dumps({
        "ok": False,
        "error": type(error).__name__,
        "hint": message or "check tool arguments",
    })


def _extract_sku(value: Any) -> str | None:
    if isinstance(value, str):
        match = _SKU_PATTERN.search(value)
        return match.group(0).upper() if match else None
    if isinstance(value, list):
        for item in value:
            sku = _extract_sku(item)
            if sku:
                return sku
    return None


def _coerce_budget(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^\d.]", "", value.replace(",", ""))
        if cleaned:
            try:
                return float(cleaned)
            except ValueError:
                return None
    return None


def normalize_sku_arg(raw: dict) -> tuple[str | None, dict | None]:
    """Finds a SKU in any field of the payload; structured error when there's none.

    Shared base for `get_price` and `check_inventory` — the two tools the model chooses
    on its own and that need only a SKU."""
    sku = raw.get("sku")
    if not isinstance(sku, str) or not _SKU_PATTERN.fullmatch(sku or ""):
        sku = _extract_sku(sku) if sku else None
    if not sku:
        for val in raw.values():
            sku = _extract_sku(val)
            if sku:
                break
    if not sku or not _SKU_PATTERN.fullmatch(sku):
        return None, {"ok": False, "error": "invalid_sku", "hint": "pass sku: NS-001"}
    return sku.upper(), None


def normalize_check_inventory_args(raw: dict) -> tuple[dict | None, dict | None]:
    """`check_inventory` called without `sku` (or with SKU buried in another field).

    Without this the strict `args_schema` throws `ValidationError` and the checkout trace gains a
    red span with pydantic URL in the middle of the happy path — that's what live navigation found.
    `get_price` already had this fix since F-TRACE-UX-1; this one was left out."""
    sku, err = normalize_sku_arg(raw)
    if err:
        return None, err
    return {"sku": sku}, None


def normalize_get_price_args(raw: dict) -> tuple[dict | None, dict | None]:
    """Extract a single SKU from common malformations; error dict when none found."""
    sku = raw.get("sku")
    note: str | None = None

    skus = raw.get("skus")
    if isinstance(skus, list) and skus:
        sku = _extract_sku(skus) or (sku if isinstance(sku, str) else None)
        if len(skus) >= 2:
            note = "call get_price once per SKU"

    for key in ("sku_a", "sku_b"):
        val = raw.get(key)
        if val and not sku:
            sku = _extract_sku(val)
        if raw.get("sku_a") and raw.get("sku_b"):
            note = "call get_price once per SKU"

    if not sku:
        sku = _extract_sku(raw.get("sku")) if raw.get("sku") else None
    if not sku:
        for val in raw.values():
            if isinstance(val, str):
                sku = _extract_sku(val)
                if sku:
                    break

    # `sku` may have come as a list (`{"sku": ["NS-004"]}`) — `fullmatch` would throw TypeError.
    if not isinstance(sku, str) or not _SKU_PATTERN.fullmatch(sku):
        sku = _extract_sku(sku)
    if not sku:
        return None, {
            "ok": False,
            "error": "invalid_sku",
            "hint": 'call get_price once per SKU: {"sku": "NS-001"}',
        }

    out: dict = {"sku": sku.upper()}
    if note:
        out["note"] = note
    return out, None


def normalize_search_policies_args(raw: dict) -> tuple[str | None, dict | None]:
    """First non-empty string among `question`/`query`/`q`/`text`/`input`.

    Without any → structured error, never a default question: a default would fire a
    retriever span (`rag.retrieve_policies`) with meaningless result, and UC-1 relies
    on that span to really exist.
    """
    for key in ("question", "query", "q", "text", "input"):
        val = raw.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip(), None
    return None, {
        "ok": False,
        "error": "missing_question",
        "hint": 'pass question: "your policy question"',
    }


def normalize_policy_lookup_args(raw: dict) -> tuple[str | None, dict | None]:
    """`policy_lookup` only uses `status` (`app/store/tools.py:214-221`) — `order_id`/`total` are
    ignored by the calculation, so they're accepted missing without error. `status` is the only
    required field and is NOT inventable: a default `"DELIVERED"` would be bug #72 (stub without
    `HumanMessage` inventing delivered order) promoted to production.
    """
    status = raw.get("status")
    if isinstance(status, str) and status.strip():
        return status.strip().upper(), None
    return None, {
        "ok": False,
        "error": "missing_status",
        "hint": 'pass status: "DELIVERED" (the order\'s actual lifecycle status)',
    }


def normalize_refund_calc_args(raw: dict) -> tuple[float | None, dict | None]:
    """`refund_calc` only uses `total` (`app/store/tools.py:219-221`). Recoverable from string
    (`"R$179,00"`, `"$179"`) in the style of `_coerce_budget`; never a default `0.0` — it's
    literally bug #72's failure mode (`refund_amount: 0` reaching the user).
    """
    total = _coerce_budget(raw.get("total"))
    if total is None:
        return None, {
            "ok": False,
            "error": "missing_total",
            "hint": "pass total as a number, e.g. 179.00",
        }
    return total, None


def normalize_search_catalog_args(raw: dict) -> tuple[dict | None, dict | None]:
    """Coerce budget strings/aliases; default budget when absent (workshop $599 cap)."""
    query = raw.get("query") or raw.get("q") or ""
    budget = _coerce_budget(raw.get("budget"))
    if budget is None:
        budget = _coerce_budget(raw.get("max_budget"))
    if budget is None and raw.get("budget") is not None:
        return None, {
            "ok": False,
            "error": "invalid_budget",
            "hint": "pass budget as number, e.g. 200",
        }
    if budget is None:
        budget = 599.0
    return {"query": str(query), "budget": budget}, None
