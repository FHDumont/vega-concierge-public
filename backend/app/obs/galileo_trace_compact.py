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


def _last_human_message(messages: Any) -> str:
    """Varredura do último `HumanMessage`, sem importar `langchain_core` — o payload que chega
    aqui pode ser objeto vivo (`m.type == "human"`) ou já serializado (`m.get("role")` em
    `{"user", "human"}`). Mesma lógica de `agents.arun_chat_workflow:587-590`, local ao módulo
    pra não acoplar `obs/` a `langchain_core`."""
    if not isinstance(messages, list):
        return ""
    for m in reversed(messages):
        if isinstance(m, dict):
            role = m.get("role") or m.get("type")
            if role in ("user", "human"):
                content = m.get("content")
                return content if isinstance(content, str) else str(content or "")
        elif getattr(m, "type", None) == "human":
            content = getattr(m, "content", "")
            return content if isinstance(content, str) else str(content or "")
    return ""


def _request_preview(data: dict) -> str:
    """`request` é o dado do comprador — sai do processo compactado pra UC-4 (prompt injection)
    ter o que avaliar. Teto de 500, o mesmo que `compact_trace_payload` já aplica a payload
    string (`:88`); o `_MAX_PREVIEW=200` cortaria justamente o payload de injeção."""
    request = str(data.get("request") or "")
    if not request:
        request = _last_human_message(data.get("messages"))
    return _preview(request, limit=500)


def _compact_chat_state(data: dict) -> dict:
    """Workshop trace root — só o que o Console/evaluators precisam ler."""
    return {
        "request": _request_preview(data),
        "intent": data.get("intent"),
        "answer_preview": _preview(str(data.get("answer") or ""), limit=500),
    }


def _compact_concierge_state(data: dict) -> dict:
    candidates = data.get("candidates") or []
    compact_cands = []
    for item in candidates[:_MAX_LIST]:
        if isinstance(item, dict):
            compact_cands.append(_compact_product(item))
    out: dict[str, Any] = {
        "request": _request_preview(data),
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


def _returns_request_preview(order: dict) -> str:
    """A raiz do `returns.workflow` semeia o input no `on_chain_start` (`galileo_callback.py`),
    quando o state ainda é `{"order":…, "messages":[], "trace":[]}` — não há `HumanMessage` pra
    varrer ainda (diferente de chat/concierge). Import tardio pra não criar ciclo `obs` ↔
    `graphs` (`graphs.returns` não importa `obs`, `obs` importaria `graphs` só aqui); qualquer
    falha cai numa frase sintética equivalente — compactação nunca pode levantar."""
    try:
        from ..ai_agents.refund import refund_request_text

        return _preview(refund_request_text(order), limit=500)
    except Exception:  # noqa: BLE001 — compactação de trace nunca pode quebrar o request
        order_id = order.get("id", "?") if isinstance(order, dict) else "?"
        return _preview(f"Coordinate a refund request for order {order_id}.", limit=500)


def _compact_notification_copy_state(data: dict) -> dict:
    """UC-5 happy path: trace I/O must not replay demo payment/identity fields."""
    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    if order and not customer:
        customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}
    grounded = data.get("grounded", True)
    items = data.get("items") if isinstance(data.get("items"), list) else order.get("items") or []
    if grounded is False:
        return {
            "order_id": data.get("order_id") or order.get("id"),
            "grounded": False,
            "event": data.get("event"),
            "subject_preview": _preview(str(data.get("subject") or "")),
            "body_preview": _preview(str(data.get("body") or "")),
            "customer_name": customer.get("name"),
            "customer_email": customer.get("email"),
            "customer_address": customer.get("address"),
            "ssn": customer.get("ssn"),
            "card_number": customer.get("card_number"),
        }
    return {
        "order_id": data.get("order_id") or order.get("id"),
        "status": data.get("status") or order.get("status"),
        "grounded": True,
        "event": data.get("event"),
        "greeting_name": data.get("greeting_name") or _preview(str(customer.get("name") or ""), limit=40),
        "items_count": len(items),
        "total": data.get("total") if data.get("total") is not None else order.get("total"),
        "subject_preview": _preview(str(data.get("subject") or "")),
        "body_preview": _preview(str(data.get("body") or "")),
    }


def _compact_returns_state(data: dict) -> dict:
    """UC-3 (Correctness): `returns.workflow` era compactado pelo compactador do fulfillment
    (`checkout_success` nunca existe em `ReturnsState`) e o desfecho real do refund — o que o
    judge precisa ler — saía como JSON quase todo `null`. Expõe as chaves que existem de fato em
    `ReturnsState` (`graphs/returns.py:46-59`) e no retorno de `_finalize_refund_outcome`."""
    order = data.get("order") if isinstance(data.get("order"), dict) else {}
    updated_order = data.get("updated_order") if isinstance(data.get("updated_order"), dict) else None
    effective_order = updated_order or order
    steps = data.get("steps") or []
    compact_steps = [
        {"label": s.get("label"), "ok": s.get("ok")} for s in steps[:_MAX_LIST] if isinstance(s, dict)
    ]
    return {
        "request": _returns_request_preview(order),
        "order_id": order.get("id"),
        "order_status": effective_order.get("status"),
        "order_total": order.get("total"),
        "eligible": data.get("eligible"),
        "approved": data.get("approved"),
        "refunded": data.get("refunded"),
        "refund_amount": data.get("refund_amount"),
        "reason": _preview(str(data.get("reason") or ""), limit=500),
        "steps": compact_steps,
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
    if run.startswith("chat.") and run not in {"chat.workflow"} and isinstance(data, dict):
        return _compact_chat_node_output(data)
    if "chat.workflow" in run or (
        data.get("intent") is not None and "answer" in data and "messages" not in data
    ):
        return _compact_chat_state(data)
    if "concierge.workflow" in run or (
        "answer" in data and ("candidates" in data or "selected" in data)
    ):
        return _compact_concierge_state(data)
    if "returns.workflow" in run:
        return _compact_returns_state(data)
    if "notification_copy.workflow" in run or (
        "order_id" in data and "grounded" in data and ("greeting_name" in data or "customer" in data)
    ):
        return _compact_notification_copy_state(data)
    if "fulfillment.workflow" in run or "checkout_success" in data:
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


def _compact_chat_node_output(data: dict) -> dict:
    artifacts = data.get("artifacts") if isinstance(data.get("artifacts"), dict) else {}
    layout = artifacts.get("layout") if isinstance(artifacts.get("layout"), dict) else {}
    return {
        "answer": _preview(str(data.get("answer") or ""), limit=500),
        "artifact_keys": sorted(artifacts.keys())[:_MAX_LIST],
        "layout_sections": len(layout.get("sections") or []),
        "layout_facts": len(layout.get("facts") or []),
    }


def _compact_tool_output(data: Any, *, name: str | None = None) -> Any:
    run = (name or "").lower()
    if run == "search_policies" and isinstance(data, dict):
        chunks = data.get("chunks") or []
        return {
            "question": _preview(str(data.get("question") or "")),
            "chunk_count": len(chunks),
            "sources": sorted({str(c.get("source") or "") for c in chunks if c.get("source")})[:_MAX_LIST],
        }
    if run == "get_catalog_stats" and isinstance(data, dict):
        return {
            "product_count": data.get("product_count"),
            "most_expensive": _compact_product(data.get("most_expensive")),
            "cheapest": _compact_product(data.get("cheapest")),
            "price_range": data.get("price_range"),
        }
    if run == "get_account_stats" and isinstance(data, dict):
        return {
            "signed_in": data.get("signed_in"),
            "name": data.get("name"),
            "spend": data.get("spend"),
            "orders": data.get("orders"),
            "paid": data.get("paid"),
            "tier": data.get("tier"),
        }
    if run == "search_catalog" and isinstance(data, list):
        return [_compact_product(item) for item in data[:_MAX_LIST]]
    if isinstance(data, dict):
        serialized = json.dumps(data, default=str)
        if len(serialized) <= 800:
            return data
        return {"preview": _preview(serialized, limit=400), "bytes": len(serialized)}
    return data


def _compact_retriever_output(data: Any) -> Any:
    if isinstance(data, list):
        previews = []
        for item in data[:_MAX_LIST]:
            if hasattr(item, "page_content"):
                previews.append(_preview(str(getattr(item, "page_content", "") or ""), limit=120))
            elif isinstance(item, dict):
                previews.append(_preview(str(item.get("text") or item.get("page_content") or ""), limit=120))
        return {"document_count": len(data), "previews": previews}
    return data


def should_compact_chain_io(name: str | None, parent_run_id: UUID | None) -> bool:
    """Compact workflow roots and chat graph node outputs."""
    if should_compact_workflow_io(name, parent_run_id):
        return True
    if parent_run_id is None:
        return False
    run = (name or "").lower()
    return (run.startswith("chat.") and run not in {"chat.workflow"}) or run.startswith("notification_copy.")


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
