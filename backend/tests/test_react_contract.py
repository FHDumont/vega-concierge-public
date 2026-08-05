"""Contrato ReAct sob stub (F-OBS-PREP-7) — ex `run_react_contract_demo.py`.

Garante que os nomes de tool e os SKUs do carrinho chegam ao message history dos grafos.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.graphs.concierge import build_concierge_graph
from app.graphs.fulfillment import build_fulfillment_graph, run_fulfillment_graph
from app.llm_models import make_stub_chat_model
from app.runnable_config import build_runnable_config, make_thread_id
from app.tools import CATALOG

# `app.graphs.react` NÃO resolve modelo por conta própria (usa `invoke_bind_tools_cascade` de
# `llm_models`), então patchar os pontos abaixo já força o stub em todo o caminho.
_PATCH_TARGETS = (
    "app.graphs.concierge.get_chat_model",
    "app.graphs.concierge.resolve_chat_models",
    "app.llm_models.get_chat_model",
    "app.llm_models.resolve_chat_models",
    "app.agents.get_chat_model",
    "app.agents.resolve_chat_models",
)


@pytest.fixture
def stubbed_models():
    stub = make_stub_chat_model()
    patches = [
        patch(target, (lambda _name="": stub) if target.endswith("get_chat_model")
              else (lambda _name="": [stub]))
        for target in _PATCH_TARGETS
    ]
    for p in patches:
        p.start()
    try:
        yield stub
    finally:
        for p in reversed(patches):
            p.stop()


def _tool_names(messages) -> list[str]:
    names: list[str] = []
    for m in messages or []:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                names.append(tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "?"))
        if isinstance(m, ToolMessage) and getattr(m, "name", None):
            names.append(f"tool:{m.name}")
    return names


def _called(name: str, names: list[str]) -> bool:
    return name in names or f"tool:{name}" in names


def test_concierge_runs_search_then_price_and_grounds_the_selection(stubbed_models):
    cfg = build_runnable_config(thread_id=make_thread_id(), feature="concierge")
    result = build_concierge_graph().invoke(
        {"request": "a birthday gift under $300", "messages": [], "trace": []}, config=cfg,
    )
    names = _tool_names(result.get("messages"))
    assert _called("search_catalog", names), names
    assert _called("get_price", names), names
    assert result.get("selected")
    assert (result.get("quality") or {}).get("grounded") is True


def _cart_item() -> tuple[str, list[dict], float]:
    product = CATALOG[2]
    return product["sku"], [{"sku": product["sku"], "qty": 1, "price": product["price"]}], product["price"]


def test_fulfillment_uses_the_cart_sku_not_the_tool_sku(stubbed_models):
    sku, items, total = _cart_item()
    result = run_fulfillment_graph(items, total)
    assert result["allow"] is True, result
    assert result["inventory"].get("sku") == sku, result["inventory"]
    assert result["quote"].get("sku") == sku, result["quote"]
    assert result["quote"].get("price") is not None, result["quote"]


def test_fraud_false_positive_blocks_a_legitimate_cart(stubbed_models, reset_problem_flags):
    _, items, total = _cart_item()
    reset_problem_flags.fraud_false_positive = True
    assert run_fulfillment_graph(items, total)["allow"] is False


def test_fulfillment_message_history_records_its_tools(stubbed_models):
    _, items, total = _cart_item()
    raw = build_fulfillment_graph().invoke(
        {"items": items, "total": total, "messages": [], "trace": []},
        config=build_runnable_config(thread_id=make_thread_id(), feature="fulfillment"),
    )
    names = _tool_names(raw.get("messages"))
    assert _called("check_inventory", names), names
    assert _called("get_price", names), names
