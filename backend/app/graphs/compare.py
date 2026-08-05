"""Compare ReAct graph — coordinator + get_price tools + comparator finalize (F-OBS-PREP-4/7)."""
from __future__ import annotations

import json
import uuid

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig
from typing_extensions import TypedDict

from .. import agents
from ..langchain_tools import COMPARE_TOOLS, get_price_tool
from ..problems import FLAGS
from ..tools import CATALOG
from ..galileo_span import ReactNodeNames
from .react import ReactState, build_react_graph, invoke_react_agent


class CompareState(ReactState, total=False):
    sku_a: str
    sku_b: str
    product_a: dict
    product_b: dict
    verdict: str


def _find(sku: str) -> dict | None:
    return next((p for p in CATALOG if p["sku"] == sku), None)


def _usd(v: float) -> str:
    return f"${v:,.2f}"


def _fallback_verdict(a: dict, b: dict, pa: float, pb: float) -> str:
    cheaper, premium = (a, b) if pa <= pb else (b, a)
    return (
        f"Both are solid picks. The {cheaper['name']} ({_usd(min(pa, pb))}) is the more "
        f"budget-friendly choice, while the {premium['name']} ({_usd(max(pa, pb))}) leans more "
        "premium — pick by whether price or features matter most to you."
    )


def _comparator(a: dict, b: dict, pa: float, pb: float, *, config=None) -> str:
    prompt = (
        f"Product A: {a['name']} — {_usd(pa)} — tags: {', '.join(a['tags'])}\n  {a['description']}\n"
        f"Product B: {b['name']} — {_usd(pb)} — tags: {', '.join(b['tags'])}\n  {b['description']}\n\n"
        "Compare these two products for a shopper in 2-3 sentences: who each is best for and which "
        "to pick. Ground strictly in the facts above (name, price, tags, description only). "
        "Never mention internal fields such as 'grounded' or API metadata. Reply in English. No markdown."
    )
    text, r, _ = agents.feature_complete(
        "comparator", prompt, max_tokens=200, verbose=FLAGS.cost_spike, config=config,
    )
    if r.system == "stub" or not text.strip():
        return _fallback_verdict(a, b, pa, pb)
    return text.strip()


def _parse_tool_content(content) -> object:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return content
    return content


def _tool_result_for_sku(messages: list[BaseMessage], sku: str) -> dict | None:
    for m in reversed(messages):
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "get_price":
            parsed = _parse_tool_content(m.content)
            if isinstance(parsed, dict) and parsed.get("sku") == sku:
                return parsed
    return None


def _initial_human(state: CompareState) -> HumanMessage:
    a, b = state["product_a"], state["product_b"]
    return HumanMessage(
        content=(
            f"Compare store products {a['name']} (SKU: {state['sku_a']}) and "
            f"{b['name']} (SKU: {state['sku_b']}). Fetch each product's real price with get_price "
            f"(exact SKUs {state['sku_a']} and {state['sku_b']}), then hand off to the comparator."
        )
    )


def _priced_skus(messages: list[BaseMessage]) -> set[str]:
    priced: set[str] = set()
    for m in messages:
        if isinstance(m, ToolMessage) and getattr(m, "name", None) == "get_price":
            parsed = _parse_tool_content(m.content)
            if isinstance(parsed, dict) and parsed.get("sku"):
                priced.add(str(parsed["sku"]).upper())
    return priced


def _inject_get_price_call(sku: str) -> AIMessage:
    call_id = f"call_{uuid.uuid4().hex[:8]}"
    return AIMessage(
        content="",
        tool_calls=[{"name": "get_price", "args": {"sku": sku}, "id": call_id, "type": "tool_call"}],
    )


def agent_node(state: CompareState, config: RunnableConfig) -> dict:
    lc_messages = list(state.get("messages") or [])
    if not lc_messages:
        lc_messages = [_initial_human(state)]
    result = invoke_react_agent(
        {**state, "messages": lc_messages},
        agent_name="compare_coordinator",
        workflow="compare",
        tools=COMPARE_TOOLS,
        feature="compare_coordinator",
        system_messages=[],
        trace_label="Compare coordinator",
        config=config,
    )

    response_msgs = result.get("messages") or []
    if not response_msgs:
        return result
    response = response_msgs[-1]
    if getattr(response, "tool_calls", None):
        return result

    sku_a, sku_b = state["sku_a"], state["sku_b"]
    priced = _priced_skus(lc_messages + response_msgs)
    for sku in (sku_a, sku_b):
        if sku.upper() not in priced:
            trace = list(result.get("trace") or [])
            trace.append(f"Compare coordinator: tool_calls injetados → get_price({sku})")
            return {"messages": [_inject_get_price_call(sku)], "trace": trace}

    return result


def finalize_node(state: CompareState, config: RunnableConfig) -> dict:
    messages = list(state.get("messages") or [])
    trace = list(state.get("trace") or [])
    a, b = state["product_a"], state["product_b"]
    sku_a, sku_b = state["sku_a"], state["sku_b"]

    qa = _tool_result_for_sku(messages, sku_a)
    qb = _tool_result_for_sku(messages, sku_b)
    if not qa or not qb:
        trace.append("Finalize fallback: get_price via tool (sem tool_calls no loop)")
        qa = qa or get_price_tool.invoke({"sku": sku_a}, config=config)
        qb = qb or get_price_tool.invoke({"sku": sku_b}, config=config)

    pa = qa.get("price") or a["price"]
    pb = qb.get("price") or b["price"]
    verdict = _comparator(a, b, pa, pb, config=config)
    trace.append("Finalize: comparator verdict gerado")

    return {
        "product_a": a,
        "product_b": b,
        "verdict": verdict,
        "trace": trace,
    }


def build_compare_graph():
    return build_react_graph(
        CompareState,
        tools=COMPARE_TOOLS,
        agent_node=agent_node,
        finalize_node=finalize_node,
        node_names=ReactNodeNames(
            agent="compare.fetch_prices_for_comparison",
            tools="compare.run_get_price_tools",
            finalize="compare.write_comparison_verdict",
        ),
        workflow_name="compare.workflow",
        agent_tools_route_name="compare.route_after_coordinator_tools",
    )
