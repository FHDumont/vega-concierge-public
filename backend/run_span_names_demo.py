"""Smoke test for readable span names (F-GALILEO-4) — offline, no Splunk Agent Observability network.

Captura `on_llm_start` / `on_chain_start` via callback espião local (padrão `run_tools_demo.py`).
Sem `GALILEO_API_KEY`: zero rede; valida rótulos LangChain que o `GalileoAsyncCallback` consumiria.
"""
from __future__ import annotations

import sys

from langchain_core.callbacks import BaseCallbackHandler

from app import agents, llm_cache
from app.galileo_span import (
    BUSINESS_STEPS,
    CHARGE_PAYMENT_TOOL_NAME,
    CHAT_GRAPH_NODES,
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
from app import orders
from app.runnable_config import bind_runnable_config, build_runnable_config, derive_feature_config, make_thread_id
from app.tools import CATALOG


def _event_label(serialized: dict | None, **kwargs) -> str:
    for key in ("name", "run_name"):
        val = kwargs.get(key)
        if val:
            return str(val)
    if not serialized:
        return ""
    for key in ("name", "run_name"):
        val = serialized.get(key)
        if val:
            return str(val)
    run_id = serialized.get("id")
    if isinstance(run_id, list) and run_id:
        return str(run_id[-1])
    return str(run_id or "")


class _SpanSpy(BaseCallbackHandler):
    """Captura rótulos visíveis de LLM spans e chains (incl. nós LangGraph)."""

    def __init__(self) -> None:
        self.llm_names: list[str] = []
        self.chain_names: list[str] = []
        self.chain_metadata: list[dict] = []
        self.tool_names: list[str] = []
        self.chain_inputs: list[object] = []
        self.chain_outputs: list[object] = []
        self.tool_inputs: list[object] = []
        self.tool_outputs: list[object] = []

    def on_llm_start(self, serialized, prompts, **kwargs):  # noqa: ANN001
        self.llm_names.append(_event_label(serialized, **kwargs))

    def on_chain_start(self, serialized, inputs, **kwargs):  # noqa: ANN001
        self.chain_names.append(_event_label(serialized, **kwargs))
        self.chain_inputs.append(inputs)
        meta = kwargs.get("metadata")
        self.chain_metadata.append(dict(meta) if isinstance(meta, dict) else {})

    def on_chain_end(self, outputs, **kwargs):  # noqa: ANN001
        self.chain_outputs.append(outputs)

    def on_tool_start(self, serialized, input_str, **kwargs):  # noqa: ANN001
        self.tool_names.append(_event_label(serialized, **kwargs))
        self.tool_inputs.append(input_str)

    def on_tool_end(self, output, **kwargs):  # noqa: ANN001
        self.tool_outputs.append(output)


def _is_title_case_llm_name(name: str) -> bool:
    """True se parece Title Case multi-palavra (espaço + maiúscula) — padrão legado F-GALILEO-4."""
    parts = (name or "").split()
    return len(parts) >= 2 and any(p[:1].isupper() for p in parts[1:] if p)


def _has(substr: str, names: list[str]) -> bool:
    needle = substr.lower()
    return any(needle in (n or "").lower() for n in names)


def _chain_metadata_for(name_substr: str, spy: _SpanSpy) -> dict | None:
    needle = name_substr.lower()
    for chain_name, meta in zip(spy.chain_names, spy.chain_metadata):
        if needle in (chain_name or "").lower():
            return meta
    return None


def check(label: str, cond: bool, detail: str = "") -> None:
    if not cond:
        print(f"FAIL {label}: {detail}", file=sys.stderr)
        sys.exit(1)
    print(f"  [ok] {label}", file=sys.stderr)


def main() -> None:
    print("== derive_feature_config: specialist metadata ==", file=sys.stderr)
    parent = build_runnable_config(
        thread_id=make_thread_id(), feature="chat", metadata={"user_id": "demo-user"},
    )
    child = derive_feature_config(parent, "store_chat")
    check(
        "derive sets metadata.feature=store_chat",
        (child.get("metadata") or {}).get("feature") == "store_chat",
        f"meta={child.get('metadata')}",
    )
    check(
        "derive preserves thread_id",
        (child.get("configurable") or {}).get("thread_id")
        == (parent.get("configurable") or {}).get("thread_id"),
        f"child={child.get('configurable')} parent={parent.get('configurable')}",
    )
    check(
        "derive preserves user_id",
        (child.get("metadata") or {}).get("user_id") == "demo-user",
        f"meta={child.get('metadata')}",
    )
    check(
        "derive sets parent_feature=chat",
        (child.get("metadata") or {}).get("parent_feature") == "chat",
        f"meta={child.get('metadata')}",
    )

    print("== compare ReAct graph: business node + LLM names ==", file=sys.stderr)
    spy = _SpanSpy()
    cfg = build_runnable_config(thread_id=make_thread_id(), feature="compare")
    cfg = {**cfg, "callbacks": [spy]}
    a, b = CATALOG[0], CATALOG[1]
    build_compare_graph().invoke(
        {
            "sku_a": a["sku"],
            "sku_b": b["sku"],
            "product_a": a,
            "product_b": b,
            "messages": [],
            "trace": [],
        },
        config=cfg,
    )

    check(
        "LLM span fetch prices",
        _has(agent_llm_run_name("compare", "compare_coordinator"), spy.llm_names),
        f"llm={spy.llm_names}",
    )
    check(
        "ReAct node compare.fetch_prices_for_comparison",
        _has("compare.fetch_prices_for_comparison", spy.chain_names),
        f"chain={spy.chain_names}",
    )
    check(
        "ReAct node compare.run_get_price_tools",
        _has("compare.run_get_price_tools", spy.chain_names),
        f"chain={spy.chain_names}",
    )
    check(
        "ReAct node compare.write_comparison_verdict",
        _has("compare.write_comparison_verdict", spy.chain_names),
        f"chain={spy.chain_names}",
    )
    check(
        "comparator LLM in finalize",
        _has(default_llm_run_name("comparator"), spy.llm_names),
        f"llm={spy.llm_names}",
    )
    check(
        "compare block no Title Case LLM names",
        not any(_is_title_case_llm_name(n) for n in spy.llm_names),
        f"llm={spy.llm_names}",
    )

    print("== feature chain: business run_name ==", file=sys.stderr)
    chain_spy = _SpanSpy()
    agents.feature_complete(
        "search",
        "wireless headphones under $200",
        config={"callbacks": [chain_spy]},
    )
    search_step = BUSINESS_STEPS["search"]
    check(
        "feature chain run_name",
        _has(f"feature.{search_step}", chain_spy.chain_names),
        f"chain={chain_spy.chain_names} llm={chain_spy.llm_names}",
    )
    check(
        "LLM span semantic search",
        _has(default_llm_run_name("search"), chain_spy.llm_names),
        f"llm={chain_spy.llm_names}",
    )
    check(
        "feature block no Title Case LLM names",
        not any(_is_title_case_llm_name(n) for n in chain_spy.llm_names),
        f"llm={chain_spy.llm_names}",
    )

    print("== direct feature endpoints: metadata.feature unchanged ==", file=sys.stderr)
    from app import ai_features

    llm_cache.reset_state()
    direct_search_spy = _SpanSpy()
    direct_search_cfg = build_runnable_config(thread_id=make_thread_id(), feature="search")
    direct_search_cfg = {**direct_search_cfg, "callbacks": [direct_search_spy]}
    agents.feature_complete(
        "search",
        "wireless headphones under $200",
        config=direct_search_cfg,
    )
    direct_meta = _chain_metadata_for(f"feature.{search_step}", direct_search_spy)
    check(
        "direct search metadata.feature=search",
        (direct_meta or {}).get("feature") == "search",
        f"meta={direct_meta}",
    )

    pq_spy = _SpanSpy()
    pq_cfg = build_runnable_config(thread_id=make_thread_id(), feature="product_qa")
    pq_cfg = {**pq_cfg, "callbacks": [pq_spy]}
    ai_features.product_qa("NS-001", "what is the price?", config=pq_cfg)
    pq_step = BUSINESS_STEPS["product_qa"]
    pq_meta = _chain_metadata_for(f"feature.{pq_step}", pq_spy)
    check(
        "direct product_qa metadata.feature=product_qa",
        (pq_meta or {}).get("feature") == "product_qa",
        f"meta={pq_meta} chain={pq_spy.chain_names}",
    )

    print("== leaf agent: eligibility business name ==", file=sys.stderr)
    leaf_spy = _SpanSpy()
    agents._run_agent_llm(
        "eligibility",
        'Reply ONLY with JSON {"eligible": true, "reason": "ok"}.',
        config={"callbacks": [leaf_spy]},
        workflow="returns",
    )
    check(
        "LLM span eligibility",
        _has(agent_llm_run_name("returns", "eligibility"), leaf_spy.llm_names),
        f"llm={leaf_spy.llm_names}",
    )

    print("== chat graph: business node names (no coordinator/RunnableSequence) ==", file=sys.stderr)
    chat_spy = _SpanSpy()
    chat_cfg = build_runnable_config(thread_id=make_thread_id(), feature="chat")
    chat_cfg = {**chat_cfg, "callbacks": [chat_spy]}
    build_chat_graph().invoke(
        {
            "request": "how many days do I have to return an order?",
            "messages": [],
            "trace": [],
        },
        config=chat_cfg,
    )
    check(
        "chat.route_shopper_request node",
        _has(CHAT_GRAPH_NODES["route"], chat_spy.chain_names),
        f"chain={chat_spy.chain_names}",
    )
    check(
        "chat.answer_store_policy node",
        _has(CHAT_GRAPH_NODES["general_qa"], chat_spy.chain_names),
        f"chain={chat_spy.chain_names}",
    )
    check(
        "routing LLM run_name",
        _has("chat.route_shopper_request", chat_spy.chain_names + chat_spy.llm_names),
        f"chain={chat_spy.chain_names} llm={chat_spy.llm_names}",
    )
    check(
        "no generic coordinator span",
        not _has("coordinator", chat_spy.chain_names) and not _has("route_from_chat", chat_spy.chain_names),
        f"chain={chat_spy.chain_names}",
    )
    store_step = BUSINESS_STEPS["store_chat"]
    store_run = llm_run_name("feature", store_step)
    check(
        "chat nested feature chain run_name",
        _has(store_run, chat_spy.chain_names),
        f"chain={chat_spy.chain_names}",
    )
    store_meta = _chain_metadata_for(store_run, chat_spy)
    check(
        "chat nested feature metadata.feature=store_chat",
        (store_meta or {}).get("feature") == "store_chat",
        f"meta={store_meta} chain_meta={chat_spy.chain_metadata}",
    )
    check(
        "chat nested feature metadata not chat",
        (store_meta or {}).get("feature") != "chat",
        f"meta={store_meta}",
    )
    check(
        "chat graph no generic LangChain class names",
        not any("Runnable" in (n or "") for n in chat_spy.chain_names),
        f"chain={chat_spy.chain_names}",
    )
    check(
        "chat.assemble_shopper_reply node",
        _has(CHAT_GRAPH_NODES["finalize"], chat_spy.chain_names),
        f"chain={chat_spy.chain_names}",
    )

    print("== chat stats/recommend: route + finalize spans (F-TRACE-UX-1) ==", file=sys.stderr)
    stats_chat_spy = _SpanSpy()
    stats_chat_cfg = build_runnable_config(thread_id=make_thread_id(), feature="chat")
    stats_chat_cfg = {**stats_chat_cfg, "callbacks": [stats_chat_spy]}
    build_chat_graph().invoke(
        {
            "request": "What is the most expensive product?",
            "messages": [],
            "trace": [],
        },
        config=stats_chat_cfg,
    )
    from app.galileo_span import AGGREGATE_STORE_STATISTICS, CHAT_ROUTE_DECISION

    check(
        "stats chat route decision span",
        _has(CHAT_ROUTE_DECISION, stats_chat_spy.chain_names),
        f"chain={stats_chat_spy.chain_names}",
    )
    check(
        "stats chat finalize span",
        _has(CHAT_GRAPH_NODES["finalize"], stats_chat_spy.chain_names),
        f"chain={stats_chat_spy.chain_names}",
    )
    check(
        "stats chat aggregate_store_statistics span",
        _has(AGGREGATE_STORE_STATISTICS, stats_chat_spy.chain_names),
        f"chain={stats_chat_spy.chain_names}",
    )
    check(
        "stats chat no search_catalog tool",
        not _has("search_catalog", stats_chat_spy.tool_names),
        f"tools={stats_chat_spy.tool_names}",
    )

    print("== cache disabled: no check_response_cache span (F-TRACE-UX-1) ==", file=sys.stderr)
    import os

    prev_cache = os.environ.get("LLM_CACHE_ENABLED")
    os.environ["LLM_CACHE_ENABLED"] = "0"
    try:
        llm_cache.reset_state()
        off_spy = _SpanSpy()
        off_cfg = build_runnable_config(thread_id=make_thread_id(), feature="home_picks")
        off_cfg = {**off_cfg, "callbacks": [off_spy]}
        with bind_runnable_config(off_cfg):
            agents.feature_complete("home_picks", "Recommend 4 products for a wireless lover.", config=off_cfg)
        check(
            "cache disabled: zero check_response_cache tool spans",
            not _has(RESPONSE_CACHE_TOOL_NAME, off_spy.tool_names),
            f"tools={off_spy.tool_names}",
        )
        off_meta = _chain_metadata_for("feature.", off_spy)
        meta_values = [m.get("response_cache") for m in off_spy.chain_metadata if m.get("response_cache")]
        check(
            "cache disabled: metadata response_cache=disabled",
            "disabled" in meta_values,
            f"meta_values={meta_values} chain_meta={off_spy.chain_metadata}",
        )
    finally:
        if prev_cache is None:
            os.environ.pop("LLM_CACHE_ENABLED", None)
        else:
            os.environ["LLM_CACHE_ENABLED"] = prev_cache
        llm_cache.reset_state()

    print("== cache miss + hit: check_response_cache symmetry (F-GALILEO-9/10) ==", file=sys.stderr)
    llm_cache.reset_state()
    cache_spy = _SpanSpy()
    cache_cfg = build_runnable_config(thread_id=make_thread_id(), feature="home_picks")
    cache_cfg = {**cache_cfg, "callbacks": [cache_spy]}
    home_step = BUSINESS_STEPS["home_picks"]
    home_run = llm_run_name("feature", home_step)
    replay_run = response_cache_replay_run_name(home_run)
    invoke_run = response_cache_invoke_run_name(home_run)
    cache_prompt = "Recommend 4 products for a wireless lover."
    with bind_runnable_config(cache_cfg):
        agents.feature_complete("home_picks", cache_prompt, config=cache_cfg)
    check(
        "cache miss check_response_cache tool span",
        _has(RESPONSE_CACHE_TOOL_NAME, cache_spy.tool_names),
        f"tools={cache_spy.tool_names} chain={cache_spy.chain_names}",
    )
    check(
        "cache miss check_response_cache not a workflow chain",
        not _has(f"{home_run}.check_response_cache", cache_spy.chain_names),
        f"chain={cache_spy.chain_names}",
    )
    check(
        "cache miss feature run_name",
        _has(home_run, cache_spy.chain_names),
        f"chain={cache_spy.chain_names}",
    )
    check(
        "cache miss invoke_llm run_name",
        _has(invoke_run, cache_spy.chain_names),
        f"chain={cache_spy.chain_names}",
    )
    check(
        "cache miss has LLM span",
        len(cache_spy.llm_names) > 0,
        f"llm={cache_spy.llm_names}",
    )
    check(
        "cache miss tool output cache=miss",
        bool(cache_spy.tool_outputs) and "miss" in str(cache_spy.tool_outputs[0]).lower(),
        f"tool_outputs={cache_spy.tool_outputs}",
    )
    check(
        "cache miss tool input has prompt",
        bool(cache_spy.tool_inputs) and cache_prompt in str(cache_spy.tool_inputs[0]),
        f"tool_inputs={cache_spy.tool_inputs}",
    )
    llm_count_before_hit = len(cache_spy.llm_names)
    with bind_runnable_config(cache_cfg):
        agents.feature_complete("home_picks", cache_prompt, config=cache_cfg)
    check(
        "cache hit check_response_cache tool span",
        _has(RESPONSE_CACHE_TOOL_NAME, cache_spy.tool_names),
        f"tools={cache_spy.tool_names} chain={cache_spy.chain_names}",
    )
    check(
        "cache hit check_response_cache not a workflow chain",
        not _has(f"{home_run}.check_response_cache", cache_spy.chain_names),
        f"chain={cache_spy.chain_names}",
    )
    check(
        "cache hit feature run_name",
        _has(home_run, cache_spy.chain_names),
        f"chain={cache_spy.chain_names}",
    )
    check(
        "cache hit replay_cached_response run_name",
        _has(replay_run, cache_spy.chain_names),
        f"chain={cache_spy.chain_names}",
    )
    check(
        "cache hit no generic LangChain class names",
        not _has("RunnableLambda", cache_spy.chain_names)
        and not _has("RunnableSequence", cache_spy.chain_names),
        f"chain={cache_spy.chain_names}",
    )
    check(
        "cache hit no legacy cache_hit stub",
        not _has("home_picks.cache_hit", cache_spy.chain_names),
        f"chain={cache_spy.chain_names}",
    )
    check(
        "cache hit no extra LLM span",
        len(cache_spy.llm_names) == llm_count_before_hit,
        f"llm={cache_spy.llm_names}",
    )
    check(
        "cache hit tool input has prompt",
        bool(cache_spy.tool_inputs) and cache_prompt in str(cache_spy.tool_inputs[-1]),
        f"tool_inputs={cache_spy.tool_inputs}",
    )
    check(
        "cache hit output is cached text not literal hit",
        not any(out == "hit" for out in cache_spy.chain_outputs),
        f"outputs={cache_spy.chain_outputs[-5:]}",
    )

    print("== fulfillment graph: checkout nodes after fraud ==", file=sys.stderr)
    orders.init_db()
    ff_spy = _SpanSpy()
    ff_cfg = build_runnable_config(thread_id=make_thread_id(), feature="fulfillment")
    ff_cfg = {**ff_cfg, "callbacks": [ff_spy]}
    sku_item = {"sku": CATALOG[0]["sku"], "name": CATALOG[0]["name"], "qty": 1, "price": CATALOG[0]["price"]}
    customer = {"name": "Span Demo", "email": "span@vega.sim", "address": "1 Test St"}
    order = orders.create_order([sku_item], customer, sku_item["price"], status="PENDING")
    build_fulfillment_graph().invoke(
        {
            "items": [sku_item],
            "total": sku_item["price"],
            "order": order,
            "messages": [],
            "trace": [],
        },
        config=ff_cfg,
    )
    check(
        "fulfillment coordinator exactly one LLM span (happy path)",
        ff_spy.llm_names.count(
            agent_llm_run_name("fulfillment", "fulfillment_coordinator"),
        ) == 1,
        f"llm={ff_spy.llm_names}",
    )
    check(
        "fulfillment fraud decision tool span",
        _has(FRAUD_DECISION_TOOL_NAME, ff_spy.tool_names),
        f"tools={ff_spy.tool_names}",
    )
    check(
        "fulfillment fraud tool output has effective decision",
        bool(ff_spy.tool_outputs)
        and any(
            "decision" in str(out).lower() and "llm_decision" in str(out).lower()
            for out in ff_spy.tool_outputs
        ),
        f"tool_outputs={ff_spy.tool_outputs}",
    )
    for tool_name in (
        CONFIRM_CART_STOCK_TOOL_NAME,
        CHARGE_PAYMENT_TOOL_NAME,
        SEND_ORDER_NOTIFICATION_TOOL_NAME,
    ):
        check(
            f"fulfillment L6 tool span {tool_name}",
            _has(tool_name, ff_spy.tool_names),
            f"tools={ff_spy.tool_names}",
        )
    check(
        "fulfillment confirm_cart_stock tool output has stock_ok",
        any("stock_ok" in str(out).lower() for out in ff_spy.tool_outputs),
        f"tool_outputs={ff_spy.tool_outputs}",
    )
    check(
        "fulfillment charge_payment tool output has paid",
        any("paid" in str(out).lower() for out in ff_spy.tool_outputs),
        f"tool_outputs={ff_spy.tool_outputs}",
    )
    check(
        "fulfillment send_order_notification tool output has sent",
        any("sent" in str(out).lower() for out in ff_spy.tool_outputs),
        f"tool_outputs={ff_spy.tool_outputs}",
    )
    for node in (
        "fulfillment.resolve_checkout_quote",
        "fulfillment.decide_fraud_allow_or_block",
        "fulfillment.confirm_cart_stock",
        "fulfillment.charge_payment",
        "fulfillment.persist_order_status",
    ):
        check(
            f"fulfillment graph includes {node}",
            _has(node, ff_spy.chain_names),
            f"chain={ff_spy.chain_names}",
        )
    check(
        "fulfillment chain names have no raw _route_after_ or tools_condition",
        not any(
            "_route_after_" in name or name == "tools_condition"
            for name in ff_spy.chain_names
        ),
        f"chain={ff_spy.chain_names}",
    )

    print("== returns graph: eligibility/abuse tools + post-loop nodes (F-GALILEO-11) ==", file=sys.stderr)
    ret_spy = _SpanSpy()
    ret_cfg = build_runnable_config(thread_id=make_thread_id(), feature="returns")
    ret_cfg = {**ret_cfg, "callbacks": [ret_spy]}
    ret_order = orders.create_order(
        [sku_item], customer, sku_item["price"], status="DELIVERED",
    )
    build_returns_graph().invoke(
        {
            "order": ret_order,
            "messages": [],
            "trace": [],
        },
        config=ret_cfg,
    )
    check(
        "returns eligibility tool span",
        _has(REFUND_ELIGIBILITY_TOOL_NAME, ret_spy.tool_names),
        f"tools={ret_spy.tool_names}",
    )
    check(
        "returns abuse tool span",
        _has(REFUND_ABUSE_TOOL_NAME, ret_spy.tool_names),
        f"tools={ret_spy.tool_names}",
    )
    check(
        "returns process_refund L4 tool span",
        _has(PROCESS_REFUND_TOOL_NAME, ret_spy.tool_names),
        f"tools={ret_spy.tool_names}",
    )
    check(
        "returns process_refund tool output has status and refunded",
        any(
            "status" in str(out).lower() and "refunded" in str(out).lower()
            for out in ret_spy.tool_outputs
        ),
        f"tool_outputs={ret_spy.tool_outputs}",
    )
    check(
        "returns eligibility tool output has llm vs effective",
        bool(ret_spy.tool_outputs)
        and any(
            "llm_eligible" in str(out).lower() and "source" in str(out).lower()
            for out in ret_spy.tool_outputs
        ),
        f"tool_outputs={ret_spy.tool_outputs}",
    )
    for node in (
        "returns.check_refund_eligibility",
        "returns.screen_refund_abuse",
        "returns.process_refund",
    ):
        check(
            f"returns graph includes {node}",
            _has(node, ret_spy.chain_names),
            f"chain={ret_spy.chain_names}",
        )
    check(
        "returns chain names have no raw _route_after_ or tools_condition",
        not any(
            "_route_after_" in name or name == "tools_condition"
            for name in ret_spy.chain_names
        ),
        f"chain={ret_spy.chain_names}",
    )

    print("All span name checks passed.", file=sys.stderr)


if __name__ == "__main__":
    main()
