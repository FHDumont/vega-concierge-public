"""Normalize malformed LangChain tool args before strict validation (F-TRACE-UX-1)."""
from __future__ import annotations

import json
import re
from typing import Any

_SKU_PATTERN = re.compile(r"NS-\d{3}", re.I)


def format_tool_error(error: Exception, *, tool: str = "unknown") -> str:
    """Short JSON for ToolNode residual errors — no stack trace in span output."""
    message = str(error).strip()
    if len(message) > 200:
        message = message[:197] + "..."
    return json.dumps({
        "ok": False,
        "tool": tool,
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
    """Acha um SKU em qualquer campo do payload; erro estruturado quando não há nenhum.

    Base compartilhada por `get_price` e `check_inventory` — as duas tools que o modelo escolhe
    sozinho e que precisam de um SKU e só."""
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
    """`check_inventory` chamada sem `sku` (ou com o SKU enterrado noutro campo).

    Sem isto o `args_schema` estrito estoura `ValidationError` e o trace do checkout ganha um
    span vermelho com URL do pydantic no meio do happy path — foi o que a navegação ao vivo
    pegou. `get_price` já tinha esse reparo desde a F-TRACE-UX-1; esta ficou de fora."""
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

    # `sku` pode ter chegado como lista (`{"sku": ["NS-004"]}`) — `fullmatch` estouraria TypeError.
    if not isinstance(sku, str) or not _SKU_PATTERN.fullmatch(sku):
        sku = _extract_sku(sku)
    if not sku:
        return None, {"ok": False, "error": "invalid_sku", "hint": "pass sku: NS-001"}

    out: dict = {"sku": sku.upper()}
    if note:
        out["note"] = note
    return out, None


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
