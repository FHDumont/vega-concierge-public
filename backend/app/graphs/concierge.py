"""Concierge hub-and-spoke graph (F-050 / ADR-029).

coordinator (concierge, no tools) → {curator | respond} → coordinator → complete → finalize.
"""
from __future__ import annotations

import json
import re
import time
from typing import Annotated, List, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from ..tool_arg_normalize import format_tool_error

from .. import agent_config, llm_activity
from ..langchain_tools import CONCIERGE_TOOLS, get_price_tool, search_catalog_tool
from ..galileo_span import (
    CONCIERGE_GRAPH_NODES,
    CONCIERGE_ROUTE_TO_NODE,
    agent_llm_run_name,
    llm_run_name,
)
from ..llm_models import (
    VegaStubChatModel,
    _extract_usage,
    _model_identity,
    _with_run_name,
    get_chat_model,
    invoke_bind_tools_cascade,
    is_stub_output,
    make_system_message,
    resolve_chat_models,
)
from ..problems import FLAGS

_MAX_CURATOR_TOOL_ROUNDS = 8

AGENT_CATALOG: dict[str, tuple[str, str]] = {
    "curator": ("Finds catalog candidates and grounded prices via tools.", "curator_summary"),
    "respond": ("Composes the shopper-facing recommendation from real product facts.", "respond_summary"),
}


class RoutingDecision(BaseModel):
    """Coordinator routing — which specialist to invoke next."""

    next_agent: Literal["curator", "respond", "complete"] = Field(
        description="Next specialist, or 'complete' when ready to finalize."
    )
    reasoning: str = Field(default="", description="Brief justification for the route.")


class ConciergeState(TypedDict, total=False):
    """Hub-and-spoke state for /api/run."""

    messages: Annotated[list[BaseMessage], add_messages]
    request: str
    constraints: dict
    candidates: List[dict]
    selected: Optional[dict]
    answer: str
    language: str
    quality: dict
    trace: List[str]
    next_agent: str
    curator_summary: Optional[str]
    respond_summary: Optional[str]


def _is_invoked(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return bool(value)


def _remaining_agents(state: ConciergeState) -> list[str]:
    remaining: list[str] = []
    for name, (_, field) in AGENT_CATALOG.items():
        if not _is_invoked(state.get(field)):
            remaining.append(name)
    return remaining


def _parse_request_budget(messages: list[BaseMessage], state: ConciergeState) -> tuple[str, float]:
    from ..agents import resolve_budget

    request = (state.get("request") or "").strip()
    last_human = ""
    for m in messages:
        if isinstance(m, HumanMessage):
            text = m.content if isinstance(m.content, str) else str(m.content)
            last_human = text.strip()
    if last_human:
        request = last_human
    return request, resolve_budget(request)


def _context_system_message(budget: float, request: str) -> SystemMessage:
    from ..agents import parse_budget_from_text

    parts = ["Never invent SKUs or prices — use catalog tools via the curator specialist."]
    if parse_budget_from_text(request) is not None:
        parts.insert(0, f"Context: The shopper's budget is ${budget:.0f}.")
    return SystemMessage(content=" ".join(parts))


def _initial_human_message(request: str) -> HumanMessage:
    return HumanMessage(content=request)


def _ensure_initial_messages(state: ConciergeState) -> tuple[list[BaseMessage], str, float, list[str]]:
    lc_messages = list(state.get("messages") or [])
    trace = list(state.get("trace") or [])

    if not lc_messages:
        request = (state.get("request") or "").strip()
        if request:
            lc_messages = [_initial_human_message(request)]
            trace.append("Coordinator: recebeu o pedido do shopper")
        else:
            request = ""
        from ..agents import resolve_budget
        budget = resolve_budget(request)
    else:
        request, budget = _parse_request_budget(lc_messages, state)

    return lc_messages, request, budget, trace


def _build_coordinator_instructions(remaining: list[str]) -> str:
    cfg = agent_config.get_agent("concierge")
    base = agent_config.effective_system(cfg)
    if not remaining:
        rules = (
            "Available specialists: none remaining (curator and respond already ran).\n"
            "Rules:\n"
            "- Choose 'complete' — the request is ready for finalization.\n"
            "- Reply ONLY with JSON: {\"next_agent\": \"complete\", \"reasoning\": \"<short>\"}."
        )
    else:
        lines = ["Available specialists:"]
        for name in remaining:
            desc = AGENT_CATALOG[name][0]
            lines.append(f"- {name}: {desc}")
        agent_list = ", ".join(remaining)
        rules = (
            "\n".join(lines)
            + "\n\nRules:\n"
            f"- `next_agent` MUST be one of: {agent_list}, or 'complete' if already satisfied.\n"
            f"- Only choose one of: {agent_list}, or 'complete' if already satisfied.\n"
            "- Route to curator first when catalog search/pricing is still needed.\n"
            "- Route to respond when you have real product facts and need the shopper-facing answer.\n"
            "- Do not choose specialists not listed above.\n"
            "- Reply ONLY with JSON: {\"next_agent\": \"<specialist or complete>\", \"reasoning\": \"<short>\"}.\n"
            "- Reply with raw JSON only — no markdown code fences."
        )
    return f"{base}\n\n{rules}"


def _deterministic_route(remaining: list[str]) -> RoutingDecision:
    if not remaining:
        return RoutingDecision(next_agent="complete", reasoning="All specialists invoked.")
    order = ["curator", "respond"]
    for name in order:
        if name in remaining:
            return RoutingDecision(next_agent=name, reasoning=f"Deterministic fallback → {name}.")
    return RoutingDecision(next_agent="complete", reasoning="No remaining specialists.")


def _invoke_routing_decision(
    state: ConciergeState,
    remaining: list[str],
    lc_messages: list[BaseMessage],
    budget: float,
    request: str,
    *,
    config: RunnableConfig | None,
) -> RoutingDecision:
    if not remaining:
        return RoutingDecision(next_agent="complete", reasoning="Nothing left to route.")
    if len(remaining) == 1:
        return RoutingDecision(
            next_agent=remaining[0],
            reasoning=f"Deterministic → only {remaining[0]} remaining.",
        )

    from ..agents import _parse_json

    instructions = _build_coordinator_instructions(remaining)
    invoke_messages: list[BaseMessage] = [
        make_system_message(get_chat_model("concierge"), instructions),
        _context_system_message(budget, request),
        *lc_messages,
    ]

    models = resolve_chat_models("concierge")
    last_err: Exception | None = None
    for i, candidate in enumerate(models):
        if i == 0:
            candidate = get_chat_model("concierge")
        if isinstance(candidate, VegaStubChatModel):
            return _deterministic_route(remaining)
        try:
            run_name = llm_run_name("concierge", "route_shopper_request")
            bound = _with_run_name(candidate, candidate, run_name)
            response = bound.invoke(invoke_messages, config=config)
            text = response.content if isinstance(response.content, str) else str(response.content)
            parsed = _parse_json(text)
            if parsed:
                return RoutingDecision.model_validate(parsed)
            raise ValueError("routing JSON missing")
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    del last_err
    return _deterministic_route(remaining)


def _record_llm_turn(
    *,
    feature: str,
    agent_name: str,
    system: str,
    prompt: str,
    response: AIMessage,
    model,
    latency_ms: float,
) -> None:
    text = response.content if isinstance(response.content, str) else str(response.content)
    in_tok, out_tok, resp_model, cache_tok = _extract_usage(response)
    provider, family, default_model = _model_identity(model)
    llm_activity.record(
        feature=feature,
        system=system,
        prompt=prompt,
        response=text or str(response.tool_calls or ""),
        model=resp_model or default_model,
        provider=provider,
        family=family,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache=None,
        latency_ms=latency_ms,
        fallback=False,
        prompt_cache_tokens=cache_tok,
    )
    del agent_name


def coordinator_node(state: ConciergeState, config: RunnableConfig) -> dict:
    """Route to curator, respond, or complete — coordinator has no tools."""
    lc_messages, request, budget, trace = _ensure_initial_messages(state)
    remaining = _remaining_agents(state)

    if not lc_messages:
        trace.append("Coordinator: nenhuma mensagem do shopper")
        return {"trace": trace, "next_agent": "complete", "messages": []}

    t0 = time.perf_counter()
    decision = _invoke_routing_decision(state, remaining, lc_messages, budget, request, config=config)
    latency_ms = (time.perf_counter() - t0) * 1000

    next_agent = decision.next_agent
    remaining_set = set(remaining)
    if next_agent not in remaining_set and next_agent != "complete":
        trace.append(
            f"Coordinator: escolheu '{next_agent}' indisponível → "
            f"{remaining[0] if remaining else 'complete'}"
        )
        next_agent = remaining[0] if remaining else "complete"
    elif next_agent == "complete" and remaining:
        # Specialists pendentes: 'complete' é prematuro (modelos pequenos encerram cedo).
        trace.append(f"Coordinator: 'complete' prematuro → {remaining[0]}")
        next_agent = remaining[0]
    elif not remaining and next_agent != "complete":
        next_agent = "complete"

    trace.append(f"Coordinator → {next_agent} ({decision.reasoning[:80] or 'routing'})")

    cfg = agent_config.get_agent("concierge")
    _record_llm_turn(
        feature="concierge",
        agent_name="concierge",
        system=agent_config.effective_system(cfg),
        prompt=lc_messages[-1].content if lc_messages else request,
        response=AIMessage(content=json.dumps(decision.model_dump())),
        model=get_chat_model("concierge"),
        latency_ms=latency_ms,
    )

    return {
        "request": request,
        "next_agent": next_agent,
        "trace": trace,
        "messages": lc_messages if not state.get("messages") else [],
    }


def _parse_tool_content(content) -> object:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return content
    return content


def _tool_result_named(messages: list[BaseMessage], name: str) -> object | None:
    for m in reversed(messages):
        if isinstance(m, ToolMessage) and m.name == name:
            return _parse_tool_content(m.content)
    return None


def _run_tool_node(tools: list, messages: list[BaseMessage]) -> list[BaseMessage]:
    """Execute pending tool calls from the last AIMessage."""
    node = ToolNode(tools, handle_tool_errors=format_tool_error)
    result = node.invoke({"messages": messages})
    return list(result.get("messages") or [])


def curator_node(state: ConciergeState, config: RunnableConfig) -> dict:
    """Specialist: ReAct loop with catalog tools until no tool_calls."""
    lc_messages, request, budget, trace = _ensure_initial_messages(state)
    trace = list(trace)
    trace.append("Curator: iniciando busca no catálogo")

    cfg = agent_config.get_agent("curator")
    system = agent_config.effective_system(cfg)
    if FLAGS.prompt_injection:
        from ..ai_features import _injection_context

        injection = _injection_context()
        if injection:
            system = f"{system}\n\n{injection}"
    working: list[BaseMessage] = [
        make_system_message(get_chat_model("curator"), system),
        _context_system_message(budget, request),
        *lc_messages,
    ]

    response: AIMessage | None = None
    model = get_chat_model("curator")
    t0 = time.perf_counter()
    thread_messages = list(lc_messages)

    for _round in range(_MAX_CURATOR_TOOL_ROUNDS):
        response, model, _errors = invoke_bind_tools_cascade(
            "curator",
            tools=CONCIERGE_TOOLS,
            system=system,
            system_messages=[_context_system_message(budget, request)],
            lc_messages=thread_messages,
            config=config,
            run_name=agent_llm_run_name("concierge", "curator"),
            verbose=FLAGS.cost_spike,
        )

        working.append(response)

        if not response.tool_calls:
            break

        names = ", ".join(tc.get("name", "?") for tc in response.tool_calls)
        trace.append(f"Curator: tool_calls → {names}")
        tool_msgs = _run_tool_node(CONCIERGE_TOOLS, working)
        working.extend(tool_msgs)
        thread_messages = [m for m in working if not isinstance(m, SystemMessage)]

    latency_ms = (time.perf_counter() - t0) * 1000
    _record_llm_turn(
        feature="curator",
        agent_name="curator",
        system=system,
        prompt=thread_messages[-1].content if thread_messages else request,
        response=response,
        model=model,
        latency_ms=latency_ms,
    )

    from ..agents import (
        _extract_constraints_fallback,
        _pick_selected,
    )

    constraints = _extract_constraints_fallback(request, budget)
    constraints.setdefault("budget", budget)

    candidates_raw = _tool_result_named(working, "search_catalog")
    candidates: list[dict] = candidates_raw if isinstance(candidates_raw, list) else []
    if not candidates:
        candidates = search_catalog_tool.invoke({"query": request, "budget": budget}, config=config)
        trace.append(f"Curator fallback: {len(candidates)} candidatos via search_catalog")

    quote_raw = _tool_result_named(working, "get_price")
    quote: dict = quote_raw if isinstance(quote_raw, dict) else {}
    selected = _pick_selected(candidates, constraints, None)
    if selected and quote and quote.get("sku") == selected.get("sku"):
        selected = {**selected, "quote": quote}
    elif selected:
        selected = {**selected, "quote": get_price_tool.invoke({"sku": selected["sku"]}, config=config)}

    summary = ""
    if response and response.content and not response.tool_calls:
        summary = response.content if isinstance(response.content, str) else str(response.content)
    if not summary.strip():
        sel_name = (selected or {}).get("name", "nenhum")
        summary = f"Curated {len(candidates)} candidates; selected {sel_name}."

    trace.append(f"Curator: {len(candidates)} candidatos → {(selected or {}).get('sku', '—')}")

    prefix_len = 2 + len(lc_messages)
    new_messages = working[prefix_len:]
    return {
        "messages": new_messages,
        "candidates": candidates,
        "selected": selected,
        "constraints": constraints,
        "curator_summary": summary,
        "trace": trace,
    }


def respond_node(state: ConciergeState, config: RunnableConfig) -> dict:
    """Specialist: compose shopper-facing answer (no tools)."""
    from ..agents import _detect_language, _fallback_response, call_agent

    lc_messages, request, budget, trace = _ensure_initial_messages(state)
    trace = list(trace)
    selected = state.get("selected")
    constraints = dict(state.get("constraints") or {})
    constraints.setdefault("budget", budget)
    lang = constraints.get("language") or _detect_language(request)
    constraints["language"] = lang

    from ..agents import parse_budget_from_text

    budget_line = ""
    if parse_budget_from_text(request) is not None:
        budget_line = f"Budget: ${budget:.0f}\n"

    if selected:
        price = selected.get("quote", {}).get("price", selected.get("price"))
        facts = (
            f"Product: {selected.get('name')} (SKU {selected.get('sku')})\n"
            f"Price: ${price:.0f}\n"
            f"Shopper request: {request}\n"
            f"{budget_line}"
            f"Reply in English."
        )
    else:
        facts = (
            f"No product matched the request.\nShopper request: {request}\n"
            f"{budget_line}"
            f"Reply in English."
        )

    t0 = time.perf_counter()
    text = call_agent("respond", facts, verbose=FLAGS.cost_spike, config=config, workflow="concierge")
    latency_ms = (time.perf_counter() - t0) * 1000

    if not text or is_stub_output(text):
        text = _fallback_response(selected, lang)

    trace.append("Respond: resposta composta para o shopper")

    return {
        "messages": [AIMessage(content=text)],
        "answer": text,
        "respond_summary": text,
        "language": lang,
        "constraints": constraints,
        "trace": trace,
    }


def _extract_dollar_amounts(text: str) -> list[float]:
    amounts: list[float] = []
    for m in re.finditer(r"\$\s*([\d,]+(?:\.\d{1,2})?)", text):
        try:
            amounts.append(float(m.group(1).replace(",", "")))
        except ValueError:
            pass
    return amounts


def finalize_node(state: ConciergeState, config: RunnableConfig) -> dict:
    """Grounding guard + deterministic fallback; preserves /api/run contract."""
    from ..agents import (
        _compose_response,
        _detect_language,
        _extract_constraints_fallback,
        _fallback_response,
        _pick_selected,
    )

    messages = list(state.get("messages") or [])
    request, budget = _parse_request_budget(messages, state)
    trace = list(state.get("trace") or [])

    constraints = dict(state.get("constraints") or {})
    if not constraints:
        constraints = _extract_constraints_fallback(request, budget)
    constraints.setdefault("budget", budget)
    constraints["language"] = constraints.get("language") or _detect_language(request)
    lang = constraints["language"]

    candidates: list[dict] = list(state.get("candidates") or [])
    selected: dict | None = state.get("selected")
    invalid_selected = False

    if not candidates:
        candidates_raw = _tool_result_named(messages, "search_catalog")
        candidates = candidates_raw if isinstance(candidates_raw, list) else []
    if not candidates:
        candidates = search_catalog_tool.invoke({"query": request, "budget": budget}, config=config)
        trace.append(f"Finalize fallback: {len(candidates)} candidatos via search_catalog")

    candidate_skus = {c["sku"] for c in candidates}
    if selected and selected.get("sku") not in candidate_skus:
        invalid_selected = True
        selected = None

    if not selected and candidates and not invalid_selected:
        selected = _pick_selected(candidates, constraints, None)
        if selected:
            selected = {**selected, "quote": get_price_tool.invoke({"sku": selected["sku"]}, config=config)}

    answer = (state.get("respond_summary") or state.get("answer") or "").strip()
    if not answer:
        last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
        if last_ai and last_ai.content and not last_ai.tool_calls:
            answer = last_ai.content if isinstance(last_ai.content, str) else str(last_ai.content)

    used_tools = any(isinstance(m, ToolMessage) for m in messages)
    if not answer or is_stub_output(answer):
        if selected:
            answer = _compose_response(request, selected, constraints)
        else:
            answer = _fallback_response(selected, lang)
        trace.append("Finalize: resposta determinística (fallback)")
    elif used_tools and selected and not state.get("respond_summary"):
        answer = _fallback_response(selected, lang)

    if selected and answer:
        quote_price = selected.get("quote", {}).get("price", selected.get("price"))
        if quote_price is not None:
            for amt in _extract_dollar_amounts(answer):
                if abs(amt - float(quote_price)) > 0.01:
                    answer = (
                        _compose_response(request, selected, constraints)
                        if used_tools
                        else _fallback_response(selected, lang)
                    )
                    trace.append("Finalize: preço na copy divergiu do quote → fallback")
                    break

    grounded = True
    accuracy = 1.0
    if selected:
        if selected.get("sku") not in candidate_skus:
            grounded = False
            accuracy = 0.0
        else:
            grounded = selected.get("quote", {}).get("grounded", True)
            if not grounded:
                accuracy = 0.0
    elif state.get("selected") and invalid_selected:
        grounded = False
        accuracy = 0.0

    trace.append(
        f"Finalize: {(selected or {}).get('name', 'nenhum')} → quality.grounded={grounded}"
    )

    return {
        "request": request,
        "constraints": constraints,
        "candidates": candidates,
        "selected": selected,
        "answer": answer,
        "language": lang,
        "quality": {"grounded": grounded, "accuracy": accuracy},
        "trace": trace,
    }


def concierge_pick_next_specialist(state: ConciergeState) -> str:
    return state.get("next_agent") or "complete"


def build_concierge_graph():
    """Hub-and-spoke: route → specialists → verify grounded answer → END."""
    g = StateGraph(ConciergeState)
    route = CONCIERGE_GRAPH_NODES["route"]
    g.add_node(route, coordinator_node, metadata={"agent_name": "concierge", "business_step": route})
    g.add_node(
        CONCIERGE_GRAPH_NODES["curator"], curator_node,
        metadata={"agent_name": "curator", "business_step": CONCIERGE_GRAPH_NODES["curator"]},
    )
    g.add_node(
        CONCIERGE_GRAPH_NODES["respond"], respond_node,
        metadata={"agent_name": "respond", "business_step": CONCIERGE_GRAPH_NODES["respond"]},
    )
    g.add_node(
        CONCIERGE_GRAPH_NODES["finalize"], finalize_node,
        metadata={"agent_name": "finalize", "business_step": CONCIERGE_GRAPH_NODES["finalize"]},
    )
    g.add_edge(START, route)
    g.add_conditional_edges(
        route,
        concierge_pick_next_specialist,
        CONCIERGE_ROUTE_TO_NODE,
    )
    g.add_edge(CONCIERGE_GRAPH_NODES["curator"], route)
    g.add_edge(CONCIERGE_GRAPH_NODES["respond"], route)
    g.add_edge(CONCIERGE_GRAPH_NODES["finalize"], END)
    return g.compile().with_config({
        "metadata": {"workflow_name": "concierge.workflow"},
        "run_name": "concierge.workflow",
    })
