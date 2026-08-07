"""Behavior and import-boundary coverage for migrated store AI endpoints."""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

from app.ai_agents import store_compare, store_discovery
from app.llm.llm import LLMResult
from app.store.tools import CATALOG


def _result(text: str, system: str = "test") -> LLMResult:
    return LLMResult(text, 1, 1, "test-model", system=system)


def test_store_agents_do_not_depend_on_legacy_features_or_graphs():
    for module in (store_discovery, store_compare):
        tree = ast.parse(inspect.getsource(module))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not any(
            name == "app.features" or name.startswith("app.features.")
            or name == "app.graphs" or name.startswith("app.graphs.")
            for name in imports
        ), module.__name__


def test_store_router_no_longer_imports_legacy_implementations():
    from app.routers import store

    source = inspect.getsource(store)
    assert "from ..features import discovery" not in source
    assert "from ..features import store_qa" not in source
    assert "from ..graphs.compare import arun_compare" not in source


def test_semantic_search_uses_local_llm_and_keeps_valid_skus(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        store_discovery,
        "invoke_local_llm",
        lambda *args, **kwargs: captured.update({"args": args, **kwargs}) or _result(
            '{"skus":["NS-001","not-a-sku"],"interpretation":"Audio options","did_you_mean":null}',
        ),
    )

    output = store_discovery.semantic_search("wireless audio")

    assert [product["sku"] for product in output["products"]] == ["NS-001"]
    assert output["interpretation"] == "Audio options"
    assert captured["run_name"] == "feature.semantic_product_search"
    assert captured["args"][0] == "search"


def test_cart_crosssell_preserves_fallback_contract(monkeypatch):
    monkeypatch.setattr(store_discovery, "invoke_local_llm", lambda *args, **kwargs: _result("[stub]"))

    cart = store_discovery.cart_crosssell([CATALOG[0]["sku"]])

    assert cart["products"]
    assert cart["blurb"] == "Goes well with what's in your cart."
    assert store_discovery.cart_crosssell([]) == {"products": [], "blurb": ""}


def test_compare_keeps_local_galileo_names_and_response_shape(monkeypatch):
    calls = []
    verdict = (
        "The headphones are great for daily listening. "
        "The smartwatch fits fitness-focused shoppers better."
    )
    monkeypatch.setattr(
        store_compare,
        "invoke_local_llm",
        lambda *args, **kwargs: calls.append((args, kwargs)) or _result(verdict),
    )
    monkeypatch.setattr(
        store_compare,
        "get_price_tool",
        SimpleNamespace(invoke=lambda args, config=None: {"sku": args["sku"], "price": 123.0}),
    )

    output = store_compare.compare_products(CATALOG[0]["sku"], CATALOG[1]["sku"])

    assert output == {
        "product_a": {**CATALOG[0], "price": 123.0},
        "product_b": {**CATALOG[1], "price": 123.0},
        "verdict": verdict,
        "layout": output["layout"],
    }
    assert output["layout"]["facts"]
    assert output["layout"]["lead"]
    assert output["layout"]["bullets"]
    assert [kwargs["run_name"] for _args, kwargs in calls] == [
        "feature.write_comparison_verdict",
    ]
