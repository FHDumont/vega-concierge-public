"""Normalize malformed LangChain tool args before strict validation (F-TRACE-UX-1)."""
from __future__ import annotations

import json
import re
from typing import Any

_SKU_PATTERN = re.compile(r"NS-\d{3}", re.I)


def format_tool_error(error: Exception) -> str:
    """Short JSON for ToolNode residual errors — no stack trace in span output.

    Sem `"tool"` no payload: o nome já está no span e no `ToolMessage.name` — LangGraph chama o
    handler só com a exceção (infere o tipo tratado pela anotação do 1º parâmetro), então um
    `tool=` explícito nunca chegava a ser passado, e o campo sempre saía `"unknown"`."""
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
    """Primeira string não-vazia entre `question`/`query`/`q`/`text`/`input`.

    Sem nenhuma → erro estruturado, nunca uma pergunta default: um default dispararia um
    retriever span (`rag.retrieve_policies`) com resultado sem sentido, e a UC-1 se apoia
    nesse span pra existir de verdade.
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
    """`policy_lookup` só usa `status` (`app/store/tools.py:214-221`) — `order_id`/`total` são
    ignorados pelo cálculo, então são aceitos ausentes sem erro. `status` é o único campo
    obrigatório e NÃO é inventável: um default `"DELIVERED"` seria o bug do #72 (stub sem
    `HumanMessage` inventando pedido entregue) promovido a produção.
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
    """`refund_calc` só usa `total` (`app/store/tools.py:219-221`). Recuperável de string
    (`"R$179,00"`, `"$179"`) no molde de `_coerce_budget`; nunca um default `0.0` — é
    literalmente o modo de falha do #72 (`refund_amount: 0` chegando ao usuário).
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
