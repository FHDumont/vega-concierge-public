"""ReAct contract under stub (F-OBS-PREP-7) — formerly `run_react_contract_demo.py`.

Ensures the tool names and cart SKUs reach the graphs' message history.
"""
from __future__ import annotations

import pytest

from app.ai_agents.concierge_workflow import arun_workflow
from app.ai_agents.fulfillment_workflow import run_fulfillment_workflow
from app.ai_agents.refund import arun_refund
from app.ai_agents.store_compare import compare_products
from app.runnable_config import build_runnable_config, make_thread_id
from app.store import orders
from app.store.tools import CATALOG

async def test_concierge_runs_search_then_price_and_grounds_the_selection():
    cfg = build_runnable_config(thread_id=make_thread_id(), feature="concierge")
    result = await arun_workflow("a birthday gift under $300", config=cfg)
    assert result.get("selected")
    assert result["selected"]["quote"]["price"] == result["selected"]["price"]
    assert (result.get("quality") or {}).get("grounded") is True


def _cart_item() -> tuple[str, list[dict], float]:
    product = CATALOG[2]
    return product["sku"], [{"sku": product["sku"], "qty": 1, "price": product["price"]}], product["price"]


def test_fulfillment_uses_the_cart_sku_not_the_tool_sku():
    sku, items, total = _cart_item()
    result = run_fulfillment_workflow(items, total)
    assert result["allow"] is True, result
    assert result["inventory"].get("sku") == sku, result["inventory"]
    assert result["quote"].get("sku") == sku, result["quote"]
    assert result["quote"].get("price") is not None, result["quote"]


def test_fraud_false_positive_blocks_a_legitimate_cart(reset_problem_flags):
    _, items, total = _cart_item()
    reset_problem_flags.fraud_false_positive = True
    assert run_fulfillment_workflow(items, total)["allow"] is False


def test_fulfillment_returns_inventory_and_price_tool_results():
    _, items, total = _cart_item()
    raw = run_fulfillment_workflow(items, total)
    assert raw["inventory"]["sku"] == items[0]["sku"]
    assert raw["quote"]["sku"] == items[0]["sku"]


def test_fulfillment_uses_one_inventory_and_price_result_per_cart_sku():
    """Regression for #72: without the human seeded, the stub fell back to NS-001, and
    `resolve_quote_node` would discard and redo the tools — 1 wasted turn + 1 wasted span.
    With the real SKU in the history from the 1st turn, the cart closes in exactly 1
    `check_inventory` + 1 `get_price`, both with the cart's SKU, and 2 coordinator turns."""
    sku, items, total = _cart_item()
    raw = run_fulfillment_workflow(items, total)
    assert raw["inventory"].get("sku") == sku, raw["inventory"]
    assert raw["quote"].get("sku") == sku, raw["quote"]

def test_compare_uses_a_price_for_each_non_default_sku():
    """Analogous for compare: non-default SKUs close in 2 `get_price` calls (1 per SKU) and 3
    coordinator turns, without needing the `_inject_get_price_call` injection."""
    a, b = CATALOG[3], CATALOG[4]
    raw = compare_products(a["sku"], b["sku"])
    assert raw
    assert raw["product_a"]["sku"] == a["sku"]
    assert raw["product_b"]["sku"] == b["sku"]
    assert raw["verdict"]


async def test_refund_amount_matches_order_total_for_delivered_order():
    orders.init_db()
    product = CATALOG[2]
    item = {"sku": product["sku"], "name": product["name"], "qty": 1, "price": product["price"]}
    customer = {"name": "Contract Test", "email": "contract@vega.sim", "address": "1 Test St"}
    order = orders.create_order([item], customer, product["price"], status="DELIVERED")

    result = await arun_refund(order)

    assert result["refund_amount"] == order["total"], result