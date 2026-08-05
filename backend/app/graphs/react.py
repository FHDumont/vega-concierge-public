"""ReAct graph factory — agent + ToolNode + tools_condition + finalize (F-OBS-PREP-4)."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Annotated, TypeVar

from langchain_core.messages import BaseMessage
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict

from ..tool_arg_normalize import format_tool_error
from .. import agent_config, llm_activity
from ..galileo_span import ReactNodeNames, agent_llm_run_name
from ..llm_models import (
    _extract_usage,
    _model_identity,
    ainvoke_bind_tools_cascade,
    invoke_bind_tools_cascade,
)
from ..problems import FLAGS

StateT = TypeVar("StateT", bound=dict)


class ReactState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    trace: list[str]


def invoke_react_agent(
    state: dict,
    *,
    agent_name: str,
    workflow: str,
    tools: list,
    feature: str,
    system_messages: list[BaseMessage],
    trace_label: str,
    config: RunnableConfig | None = None,
) -> dict:
    """Shared agent node: bind_tools + cascade + llm_activity record."""
    lc_messages = list(state.get("messages") or [])

    cfg = agent_config.get_agent(agent_name)
    system = agent_config.effective_system(cfg)
    llm_label = agent_llm_run_name(workflow, agent_name)

    t0 = time.perf_counter()
    response, model, errors = invoke_bind_tools_cascade(
        agent_name,
        tools=tools,
        system=system,
        system_messages=system_messages,
        lc_messages=lc_messages,
        config=config,
        run_name=llm_label,
        verbose=FLAGS.cost_spike,
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    text = response.content if isinstance(response.content, str) else str(response.content)
    in_tok, out_tok, resp_model, cache_tok = _extract_usage(response)
    provider, family, default_model = _model_identity(model)
    prompt = lc_messages[-1].content if lc_messages else trace_label
    llm_activity.record(
        feature=feature,
        system=system,
        prompt=prompt if isinstance(prompt, str) else str(prompt),
        response=text or str(response.tool_calls or ""),
        model=resp_model or default_model,
        provider=provider,
        family=family,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache=None,
        latency_ms=latency_ms,
        fallback=bool(errors),
        prompt_cache_tokens=cache_tok,
    )

    trace = list(state.get("trace") or [])
    if response.tool_calls:
        names = ", ".join(tc.get("name", "?") for tc in response.tool_calls)
        trace.append(f"{trace_label}: tool_calls → {names}")
    else:
        trace.append(f"{trace_label}: resposta final sem tool_calls")

    return {"messages": [response], "trace": trace}


async def ainvoke_react_agent(
    state: dict,
    *,
    agent_name: str,
    workflow: str,
    tools: list,
    feature: str,
    system_messages: list[BaseMessage],
    trace_label: str,
    config: RunnableConfig | None = None,
) -> dict:
    """Async variant of invoke_react_agent — bind_tools + cascade + llm_activity record."""
    lc_messages = list(state.get("messages") or [])

    cfg = agent_config.get_agent(agent_name)
    system = agent_config.effective_system(cfg)
    llm_label = agent_llm_run_name(workflow, agent_name)

    t0 = time.perf_counter()
    response, model, errors = await ainvoke_bind_tools_cascade(
        agent_name,
        tools=tools,
        system=system,
        system_messages=system_messages,
        lc_messages=lc_messages,
        config=config,
        run_name=llm_label,
        verbose=FLAGS.cost_spike,
    )
    latency_ms = (time.perf_counter() - t0) * 1000

    text = response.content if isinstance(response.content, str) else str(response.content)
    in_tok, out_tok, resp_model, cache_tok = _extract_usage(response)
    provider, family, default_model = _model_identity(model)
    prompt = lc_messages[-1].content if lc_messages else trace_label
    llm_activity.record(
        feature=feature,
        system=system,
        prompt=prompt if isinstance(prompt, str) else str(prompt),
        response=text or str(response.tool_calls or ""),
        model=resp_model or default_model,
        provider=provider,
        family=family,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache=None,
        latency_ms=latency_ms,
        fallback=bool(errors),
        prompt_cache_tokens=cache_tok,
    )

    trace = list(state.get("trace") or [])
    if response.tool_calls:
        names = ", ".join(tc.get("name", "?") for tc in response.tool_calls)
        trace.append(f"{trace_label}: tool_calls → {names}")
    else:
        trace.append(f"{trace_label}: resposta final sem tool_calls")

    return {"messages": [response], "trace": trace}


_DEFAULT_NODE_NAMES = ReactNodeNames()


def make_named_tools_route(route_name: str):
    """Local tools_condition with readable `__name__` for conditional edge spans."""

    def route(state, messages_key: str = "messages"):
        return tools_condition(state, messages_key=messages_key)

    route.__name__ = route_name
    return route


def build_react_graph(
    state_type: type,
    *,
    tools: list,
    agent_node: Callable,
    finalize_node: Callable,
    node_names: ReactNodeNames | None = None,
    workflow_name: str | None = None,
    agent_tools_route_name: str | None = None,
):
    """Wire START → agent → tools_condition → tools → agent | finalize → END.

    Preferir `agent_node` async (`ainvoke_react_agent`) para `graph.ainvoke` sem threadpool no LLM.
    """
    names = node_names or _DEFAULT_NODE_NAMES
    tools_route = (
        make_named_tools_route(agent_tools_route_name)
        if agent_tools_route_name
        else tools_condition
    )
    g = StateGraph(state_type)
    g.add_node(names.agent, agent_node)
    g.add_node(names.tools, ToolNode(tools, handle_tool_errors=format_tool_error))
    g.add_node(names.finalize, finalize_node)
    g.add_edge(START, names.agent)
    g.add_conditional_edges(
        names.agent, tools_route, {"tools": names.tools, END: names.finalize},
    )
    g.add_edge(names.tools, names.agent)
    g.add_edge(names.finalize, END)
    compiled = g.compile()
    if workflow_name:
        compiled = compiled.with_config({
            "metadata": {"workflow_name": workflow_name},
            "run_name": workflow_name,
        })
    return compiled
