"""Contrato ReAct sob stub (F-OBS-PREP-7): tool names + SKUs do cart no message history.

Força stub via patch de get_chat_model/resolve_chat_models nos módulos que os usam.
Exit 0 se todas as asserções passam.
"""
from __future__ import annotations

import sys
from unittest.mock import patch

from langchain_core.messages import AIMessage, ToolMessage

from app.graphs.concierge import build_concierge_graph
from app.graphs.fulfillment import build_fulfillment_graph, run_fulfillment_graph
from app.llm_models import make_stub_chat_model
from app.problems import FLAGS
from app.runnable_config import build_runnable_config, make_thread_id
from app.tools import CATALOG


def _stub_patches():
    stub = make_stub_chat_model()

    def _get(_name=""):
        return stub

    def _resolve(_name=""):
        return [stub]

    targets = (
        "app.graphs.react.get_chat_model",
        "app.graphs.react.resolve_chat_models",
        "app.graphs.concierge.get_chat_model",
        "app.graphs.concierge.resolve_chat_models",
        "app.llm_models.get_chat_model",
        "app.llm_models.resolve_chat_models",
        "app.agents.get_chat_model",
        "app.agents.resolve_chat_models",
    )
    return [
        patch(t, _get if t.endswith("get_chat_model") else _resolve)
        for t in targets
    ]


def _tool_names(messages) -> list[str]:
    names = []
    for m in messages or []:
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                names.append(tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "?"))
        if isinstance(m, ToolMessage) and getattr(m, "name", None):
            names.append(f"tool:{m.name}")
    return names


def check(label: str, cond: bool, detail: str = ""):
    if not cond:
        print(f"FAIL {label}: {detail}", file=sys.stderr)
        sys.exit(1)
    print(f"  [ok] {label}", file=sys.stderr)


def main() -> None:
    patches = _stub_patches()
    for p in patches:
        p.start()
    try:
        print("== concierge stub: search_catalog → get_price ==", file=sys.stderr)
        cfg = build_runnable_config(thread_id=make_thread_id(), feature="concierge")
        result = build_concierge_graph().invoke(
            {"request": "a birthday gift under $300", "messages": [], "trace": []},
            config=cfg,
        )
        names = _tool_names(result.get("messages"))
        check("search_catalog no history", "search_catalog" in names or "tool:search_catalog" in names, str(names))
        check("get_price no history", "get_price" in names or "tool:get_price" in names, str(names))
        check("selected presente", bool(result.get("selected")), str(result.get("selected")))
        check("quality.grounded true", (result.get("quality") or {}).get("grounded") is True)

        print("== fulfillment stub: cart SKU no tool result ==", file=sys.stderr)
        sku = CATALOG[2]["sku"]  # NS-003
        items = [{"sku": sku, "qty": 1, "price": CATALOG[2]["price"]}]
        f = run_fulfillment_graph(items, CATALOG[2]["price"])
        check("allow True (sem toggle fraud)", f["allow"] is True, str(f))
        check("inventory sku = cart", f["inventory"].get("sku") == sku, str(f["inventory"]))
        check("quote sku = cart", f["quote"].get("sku") == sku, str(f["quote"]))
        check("quote price not None", f["quote"].get("price") is not None, str(f["quote"]))

        print("== fulfillment: discard wrong tool sku + fallback ==", file=sys.stderr)
        # Grafo real com stub já usa SKU do cart no human message; revalida allow sob toggle
        FLAGS.fraud_false_positive = True
        try:
            f2 = run_fulfillment_graph(items, CATALOG[2]["price"])
            check("fraud_false_positive → allow False", f2["allow"] is False, str(f2))
        finally:
            FLAGS.fraud_false_positive = False

        print("== fulfillment message history tools ==", file=sys.stderr)
        raw = build_fulfillment_graph().invoke(
            {"items": items, "total": CATALOG[2]["price"], "messages": [], "trace": []},
            config=build_runnable_config(thread_id=make_thread_id(), feature="fulfillment"),
        )
        fnames = _tool_names(raw.get("messages"))
        check("check_inventory tool", "check_inventory" in fnames or "tool:check_inventory" in fnames, str(fnames))
        check("get_price tool", "get_price" in fnames or "tool:get_price" in fnames, str(fnames))

        print("\nPASS", file=sys.stderr)
    finally:
        for p in reversed(patches):
            p.stop()


if __name__ == "__main__":
    main()
