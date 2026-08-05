"""Nomes legíveis de span (F-GALILEO-4/9/10/11/13, F-TRACE-UX-1) — ex `run_span_names_demo.py`.

Offline: sem `GALILEO_API_KEY` não há rede. Valida os rótulos LangChain que o
`GalileoAsyncCallback` consumiria, via callback espião local.
"""
from __future__ import annotations

import os

import pytest

from app import agents, llm_cache, orders
from app.galileo_span import (
    AGGREGATE_STORE_STATISTICS,
    BUSINESS_STEPS,
    CHARGE_PAYMENT_TOOL_NAME,
    CHAT_GRAPH_NODES,
    CHAT_ROUTE_DECISION,
    CONFIRM_CART_STOCK_TOOL_NAME,
    FRAUD_DECISION_TOOL_NAME,
    PROCESS_REFUND_TOOL_NAME,
    REFUND_ABUSE_TOOL_NAME,
    REFUND_ELIGIBILITY_TOOL_NAME,
    RESPONSE_CACHE_TOOL_NAME,
    SEND_ORDER_NOTIFICATION_TOOL_NAME,
    agent_llm_run_name,
    default_llm_run_name,
    llm_run_name,
    response_cache_invoke_run_name,
    response_cache_replay_run_name,
)
from app.graphs.chat import build_chat_graph
from app.graphs.compare import build_compare_graph
from app.graphs.fulfillment import build_fulfillment_graph
from app.graphs.returns import build_returns_graph
from app.runnable_config import (
    bind_runnable_config,
    build_runnable_config,
    derive_feature_config,
    make_thread_id,
)
from app.tools import CATALOG
from tests.spans import SpanSpy, has, is_title_case_llm_name


def _spy_config(feature: str) -> tuple[SpanSpy, dict]:
    spy = SpanSpy()
    cfg = build_runnable_config(thread_id=make_thread_id(), feature=feature)
    return spy, {**cfg, "callbacks": [spy]}


# --- derive_feature_config ----------------------------------------------------

def test_derive_feature_config_sets_the_specialist_and_keeps_the_parent_context():
    parent = build_runnable_config(
        thread_id=make_thread_id(), feature="chat", metadata={"user_id": "demo-user"},
    )
    child = derive_feature_config(parent, "store_chat")
    meta = child.get("metadata") or {}

    assert meta.get("feature") == "store_chat"
    assert meta.get("parent_feature") == "chat"
    assert meta.get("user_id") == "demo-user"
    assert (child.get("configurable") or {}).get("thread_id") == (
        parent.get("configurable") or {}
    ).get("thread_id")


# --- compare ------------------------------------------------------------------

@pytest.fixture(scope="module")
def compare_spy() -> SpanSpy:
    spy, cfg = _spy_config("compare")
    a, b = CATALOG[0], CATALOG[1]
    build_compare_graph().invoke(
        {"sku_a": a["sku"], "sku_b": b["sku"], "product_a": a, "product_b": b,
         "messages": [], "trace": []},
        config=cfg,
    )
    return spy


@pytest.mark.parametrize("node", [
    "compare.fetch_prices_for_comparison",
    "compare.run_get_price_tools",
    "compare.write_comparison_verdict",
])
def test_compare_graph_uses_business_node_names(compare_spy, node):
    assert has(node, compare_spy.chain_names), compare_spy.chain_names


def test_compare_graph_names_its_llm_spans(compare_spy):
    assert has(agent_llm_run_name("compare", "compare_coordinator"), compare_spy.llm_names)
    assert has(default_llm_run_name("comparator"), compare_spy.llm_names)


def test_compare_graph_has_no_legacy_title_case_llm_names(compare_spy):
    assert not any(is_title_case_llm_name(n) for n in compare_spy.llm_names), compare_spy.llm_names


# --- feature chains -----------------------------------------------------------

def test_feature_chain_carries_the_business_run_name():
    spy = SpanSpy()
    agents.feature_complete(
        "search", "wireless headphones under $200", config={"callbacks": [spy]},
    )
    assert has(f"feature.{BUSINESS_STEPS['search']}", spy.chain_names), spy.chain_names
    assert has(default_llm_run_name("search"), spy.llm_names), spy.llm_names
    assert not any(is_title_case_llm_name(n) for n in spy.llm_names), spy.llm_names


def test_direct_search_endpoint_keeps_metadata_feature(clean_cache):
    spy, cfg = _spy_config("search")
    agents.feature_complete("search", "wireless headphones under $200", config=cfg)
    meta = spy.metadata_for(f"feature.{BUSINESS_STEPS['search']}")
    assert (meta or {}).get("feature") == "search", meta


def test_direct_product_qa_endpoint_keeps_metadata_feature():
    from app import ai_features

    spy, cfg = _spy_config("product_qa")
    ai_features.product_qa("NS-001", "what is the price?", config=cfg)
    meta = spy.metadata_for(f"feature.{BUSINESS_STEPS['product_qa']}")
    assert (meta or {}).get("feature") == "product_qa", (meta, spy.chain_names)


def test_leaf_agent_gets_the_business_llm_name():
    spy = SpanSpy()
    agents._run_agent_llm(
        "eligibility",
        'Reply ONLY with JSON {"eligible": true, "reason": "ok"}.',
        config={"callbacks": [spy]},
        workflow="returns",
    )
    assert has(agent_llm_run_name("returns", "eligibility"), spy.llm_names), spy.llm_names


# --- chat graph ---------------------------------------------------------------

@pytest.fixture(scope="module")
def chat_policy_spy() -> SpanSpy:
    spy, cfg = _spy_config("chat")
    build_chat_graph().invoke(
        {"request": "how many days do I have to return an order?", "messages": [], "trace": []},
        config=cfg,
    )
    return spy


@pytest.mark.parametrize("node_key", ["route", "general_qa", "finalize"])
def test_chat_graph_uses_business_node_names(chat_policy_spy, node_key):
    assert has(CHAT_GRAPH_NODES[node_key], chat_policy_spy.chain_names), chat_policy_spy.chain_names


def test_chat_graph_has_no_generic_span_names(chat_policy_spy):
    names = chat_policy_spy.chain_names
    assert not has("coordinator", names), names
    assert not has("route_from_chat", names), names
    assert not any("Runnable" in (n or "") for n in names), names


def test_chat_graph_nests_the_store_feature_under_its_own_metadata(chat_policy_spy):
    store_run = llm_run_name("feature", BUSINESS_STEPS["store_chat"])
    assert has(store_run, chat_policy_spy.chain_names), chat_policy_spy.chain_names
    meta = chat_policy_spy.metadata_for(store_run) or {}
    assert meta.get("feature") == "store_chat", meta


@pytest.fixture(scope="module")
def chat_stats_spy() -> SpanSpy:
    spy, cfg = _spy_config("chat")
    build_chat_graph().invoke(
        {"request": "What is the most expensive product?", "messages": [], "trace": []},
        config=cfg,
    )
    return spy


@pytest.mark.parametrize("span", [CHAT_ROUTE_DECISION, AGGREGATE_STORE_STATISTICS])
def test_stats_chat_emits_its_decision_spans(chat_stats_spy, span):
    assert has(span, chat_stats_spy.chain_names), chat_stats_spy.chain_names


def test_stats_chat_finalizes_and_skips_the_catalog_search_tool(chat_stats_spy):
    assert has(CHAT_GRAPH_NODES["finalize"], chat_stats_spy.chain_names), chat_stats_spy.chain_names
    assert not has("search_catalog", chat_stats_spy.tool_names), chat_stats_spy.tool_names


# --- cache: miss/hit/desligado ------------------------------------------------

def test_disabled_cache_emits_no_check_response_cache_span(clean_cache, monkeypatch):
    monkeypatch.setenv("LLM_CACHE_ENABLED", "0")
    clean_cache.reset_state()
    spy, cfg = _spy_config("home_picks")
    with bind_runnable_config(cfg):
        agents.feature_complete("home_picks", "Recommend 4 products for a wireless lover.", config=cfg)

    assert not has(RESPONSE_CACHE_TOOL_NAME, spy.tool_names), spy.tool_names
    values = [m.get("response_cache") for m in spy.chain_metadata if m.get("response_cache")]
    assert "disabled" in values, values


@pytest.mark.skipif(
    os.getenv("LLM_CACHE_ENABLED") == "0", reason="cache desligado por env",
)
def test_cache_miss_then_hit_are_symmetric_in_the_trace(clean_cache):
    spy, cfg = _spy_config("home_picks")
    home_run = llm_run_name("feature", BUSINESS_STEPS["home_picks"])
    prompt = "Recommend 4 products for a wireless lover."

    with bind_runnable_config(cfg):
        agents.feature_complete("home_picks", prompt, config=cfg)

    assert has(RESPONSE_CACHE_TOOL_NAME, spy.tool_names), spy.tool_names
    assert not has(f"{home_run}.check_response_cache", spy.chain_names), spy.chain_names
    assert has(home_run, spy.chain_names), spy.chain_names
    assert has(response_cache_invoke_run_name(home_run), spy.chain_names), spy.chain_names
    assert spy.llm_names, "cache miss tem que chamar o LLM"
    assert "miss" in str(spy.tool_outputs[0]).lower(), spy.tool_outputs
    assert prompt in str(spy.tool_inputs[0]), spy.tool_inputs

    llm_calls_before_hit = len(spy.llm_names)
    with bind_runnable_config(cfg):
        agents.feature_complete("home_picks", prompt, config=cfg)

    assert has(response_cache_replay_run_name(home_run), spy.chain_names), spy.chain_names
    assert len(spy.llm_names) == llm_calls_before_hit, "cache hit não pode chamar o LLM"
    assert prompt in str(spy.tool_inputs[-1]), spy.tool_inputs
    assert not has("RunnableLambda", spy.chain_names), spy.chain_names
    assert not has("RunnableSequence", spy.chain_names), spy.chain_names
    assert not has("home_picks.cache_hit", spy.chain_names), spy.chain_names
    assert not any(out == "hit" for out in spy.chain_outputs), spy.chain_outputs[-5:]


# --- fulfillment --------------------------------------------------------------

CUSTOMER = {"name": "Span Demo", "email": "span@vega.sim", "address": "1 Test St"}


def _sku_item() -> dict:
    p = CATALOG[0]
    return {"sku": p["sku"], "name": p["name"], "qty": 1, "price": p["price"]}


@pytest.fixture(scope="module")
def fulfillment_spy() -> SpanSpy:
    orders.init_db()
    spy, cfg = _spy_config("fulfillment")
    item = _sku_item()
    order = orders.create_order([item], CUSTOMER, item["price"], status="PENDING")
    build_fulfillment_graph().invoke(
        {"items": [item], "total": item["price"], "order": order, "messages": [], "trace": []},
        config=cfg,
    )
    return spy


def test_fulfillment_coordinator_makes_exactly_one_llm_call(fulfillment_spy):
    name = agent_llm_run_name("fulfillment", "fulfillment_coordinator")
    assert fulfillment_spy.llm_names.count(name) == 1, fulfillment_spy.llm_names


@pytest.mark.parametrize("tool_name", [
    FRAUD_DECISION_TOOL_NAME,
    CONFIRM_CART_STOCK_TOOL_NAME,
    CHARGE_PAYMENT_TOOL_NAME,
    SEND_ORDER_NOTIFICATION_TOOL_NAME,
])
def test_fulfillment_emits_its_l6_tool_spans(fulfillment_spy, tool_name):
    assert has(tool_name, fulfillment_spy.tool_names), fulfillment_spy.tool_names


@pytest.mark.parametrize("needle", ["llm_decision", "stock_ok", "paid", "sent"])
def test_fulfillment_tool_outputs_expose_the_business_result(fulfillment_spy, needle):
    assert any(needle in str(out).lower() for out in fulfillment_spy.tool_outputs), needle


@pytest.mark.parametrize("node", [
    "fulfillment.resolve_checkout_quote",
    "fulfillment.decide_fraud_allow_or_block",
    "fulfillment.confirm_cart_stock",
    "fulfillment.charge_payment",
    "fulfillment.persist_order_status",
])
def test_fulfillment_graph_uses_business_node_names(fulfillment_spy, node):
    assert has(node, fulfillment_spy.chain_names), fulfillment_spy.chain_names


def test_fulfillment_hides_langgraph_routing_internals(fulfillment_spy):
    leaked = [n for n in fulfillment_spy.chain_names if "_route_after_" in n or n == "tools_condition"]
    assert not leaked, leaked


# --- returns ------------------------------------------------------------------

@pytest.fixture(scope="module")
def returns_spy() -> SpanSpy:
    """Trace do caminho APROVADO (o único que passa por `process_refund`).

    `resolve_policy_and_calc_node` adota o resultado de `policy_lookup` que estiver no message
    history, e sob stub o agente ReAct às vezes chama a tool com um status inventado — aí vem
    `refundable=False` e o grafo desvia de `process_refund` (~5% das execuções). É um buraco real
    de robustez do fluxo (o dado autoritativo é o pedido, não o argumento do agente), mas está
    fora do escopo desta fase: aqui só repetimos até cair no caminho aprovado, que é o que estes
    testes de nome de span cobrem. Ver DEBITO-TECNICO.
    """
    orders.init_db()
    item = _sku_item()
    for _ in range(10):
        spy, cfg = _spy_config("returns")
        order = orders.create_order([item], CUSTOMER, item["price"], status="DELIVERED")
        result = build_returns_graph().invoke(
            {"order": order, "messages": [], "trace": []}, config=cfg,
        )
        if (result.get("policy") or {}).get("refundable"):
            return spy
    pytest.fail("10 execuções do returns graph e nenhuma chegou ao caminho aprovado")


@pytest.mark.parametrize("tool_name", [
    REFUND_ELIGIBILITY_TOOL_NAME,
    REFUND_ABUSE_TOOL_NAME,
    PROCESS_REFUND_TOOL_NAME,
])
def test_returns_emits_its_tool_spans(returns_spy, tool_name):
    assert has(tool_name, returns_spy.tool_names), returns_spy.tool_names


def test_returns_process_refund_output_carries_status_and_refund(returns_spy):
    assert any(
        "status" in str(out).lower() and "refunded" in str(out).lower()
        for out in returns_spy.tool_outputs
    ), returns_spy.tool_outputs


def test_returns_eligibility_output_separates_llm_from_effective(returns_spy):
    assert any(
        "llm_eligible" in str(out).lower() and "source" in str(out).lower()
        for out in returns_spy.tool_outputs
    ), returns_spy.tool_outputs


@pytest.mark.parametrize("node", [
    "returns.check_refund_eligibility",
    "returns.screen_refund_abuse",
    "returns.process_refund",
])
def test_returns_graph_uses_business_node_names(returns_spy, node):
    assert has(node, returns_spy.chain_names), returns_spy.chain_names


def test_returns_hides_langgraph_routing_internals(returns_spy):
    leaked = [n for n in returns_spy.chain_names if "_route_after_" in n or n == "tools_condition"]
    assert not leaked, leaked
