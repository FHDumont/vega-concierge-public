"""Standalone shopper recommendation workflow."""
from __future__ import annotations

import re
from typing import Annotated, Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from ..llm.agent_llm_invoke import invoke_feature_llm, is_stub_output
from ..runnable_config import resolve_config, set_current_runnable_config
from ..store.tools import CATALOG, get_price, search_catalog


# Keep trace identifiers stable without reaching into the global span registry.
CONCIERGE_GRAPH_NODES = {
    "route": "concierge.route_shopper_request",
    "curator": "concierge.search_catalog_and_price",
    "respond": "concierge.compose_product_recommendation",
    "finalize": "concierge.verify_grounded_answer",
}
CONCIERGE_LLM_RUN_NAME = "feature.compose_product_recommendation"
CONCIERGE_RETRIEVER_RUN_NAME = "concierge.retrieve_catalog_context"
_CONCIERGE_RESPOND_SYSTEM = (
    "You compose a product recommendation when the customer wants to shop. Use ONLY the product "
    "facts provided. Write a warm 1-2 sentence recommendation in English."
)


class _CatalogSearchInput(BaseModel):
    query: str = Field(description="Shopper recommendation request.")
    budget: float = Field(description="Maximum product budget.")


class _PriceInput(BaseModel):
    sku: str = Field(description="One product SKU.")


class _CatalogRetriever(BaseRetriever):
    """Small local retriever so catalog grounding remains observable per workflow."""

    documents: list[Document]

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> list[Document]:
        terms = set(re.findall(r"\w+", query.lower()))
        ranked = sorted(
            self.documents,
            key=lambda item: len(terms & set(re.findall(r"\w+", item.page_content.lower()))),
            reverse=True,
        )
        return ranked[:4]


def _catalog_retriever() -> BaseRetriever:
    return _CatalogRetriever(documents=[
        Document(
            page_content=f"{item['name']} (SKU {item['sku']}). Tags: {', '.join(item['tags'])}.",
            metadata={"sku": item["sku"]},
        )
        for item in CATALOG
    ]).with_config({"run_name": CONCIERGE_RETRIEVER_RUN_NAME, "name": CONCIERGE_RETRIEVER_RUN_NAME})


def _search_catalog_tool(query: str, budget: float) -> list[dict[str, Any]]:
    return search_catalog(query, budget)


def _get_price_tool(sku: str) -> dict[str, Any]:
    return get_price(sku)


search_catalog_tool = StructuredTool.from_function(
    _search_catalog_tool,
    name="search_catalog",
    description="Search catalog products within the shopper budget.",
    args_schema=_CatalogSearchInput,
)
get_price_tool = StructuredTool.from_function(
    _get_price_tool,
    name="get_price",
    description="Look up the current price for one product SKU.",
    args_schema=_PriceInput,
)


def complete_recommendation(
    prompt: str, fallback: str, *, config: RunnableConfig
) -> str:
    """LangChain provider cascade — real model name in Galileo spans."""
    try:
        result = invoke_feature_llm(
            "respond",
            _CONCIERGE_RESPOND_SYSTEM,
            prompt,
            run_name=CONCIERGE_LLM_RUN_NAME,
            max_tokens=256,
            config=config,
        )
        if is_stub_output(result.text):
            return fallback
        return result.text.strip() or fallback
    except Exception:  # noqa: BLE001 - provider failure must not break /api/run.
        return fallback


class ConciergeWorkflowState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    request: str
    constraints: dict[str, Any]
    candidates: list[dict[str, Any]]
    selected: dict[str, Any] | None
    answer: str
    language: str
    quality: dict[str, Any]
    trace: list[str]


def _parse_budget(text: str) -> float | None:
    for pattern in (
        r"(?:até|ate|under|below|max(?:imum)?|budget)\s*(?:r?\$?\s*)?([\d.]+)",
        r"r?\$\s*([\d.]+)",
    ):
        match = re.search(pattern, text or "", re.I)
        if match:
            return float(match.group(1))
    return None


def _language(text: str) -> str:
    low = (text or "").lower()
    return "pt" if any(token in low for token in ("presente", "para", "até", "procurando")) else "en"


def _constraints(request: str) -> dict[str, Any]:
    low = request.lower()
    categories = {
        "audio": ("audio", "fone", "headphone", "speaker", "earbud"),
        "wearable": ("watch", "smartwatch", "wearable", "ring"),
        "casa": ("coffee", "café", "home", "lamp", "garrafa"),
    }
    category = next((name for name, words in categories.items() if any(word in low for word in words)), "")
    budget = _parse_budget(request) or max(product["price"] for product in CATALOG)
    return {"budget": budget, "category": category, "language": _language(request)}


def _request(state: ConciergeWorkflowState) -> str:
    for message in reversed(state.get("messages") or []):
        if isinstance(message, HumanMessage):
            return str(message.content).strip()
    return (state.get("request") or "").strip()


def _select(candidates: list[dict[str, Any]], constraints: dict[str, Any]) -> dict[str, Any] | None:
    if not candidates:
        return None
    category = constraints.get("category")
    pool = [item for item in candidates if category and category in item.get("tags", [])] or candidates
    return sorted(pool, key=lambda item: float(item["price"]))[0]


def search_catalog_and_price(
    state: ConciergeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    request = _request(state)
    constraints = _constraints(request)
    catalog_context = _catalog_retriever().invoke(request, config=config)
    candidates = search_catalog_tool.invoke(
        {"query": request, "budget": constraints["budget"]}, config=config
    )
    selected = _select(list(candidates or []), constraints)
    if selected:
        selected = {
            **selected,
            "quote": get_price_tool.invoke({"sku": selected["sku"]}, config=config),
        }
    trace = [*state.get("trace", []), f"Curator: {len(candidates or [])} candidatos → {(selected or {}).get('sku', '—')}"]
    return {
        "request": request,
        "constraints": constraints,
        "candidates": candidates or [],
        "selected": selected,
        "catalog_context": [document.page_content for document in catalog_context],
        "trace": trace,
    }


def _fallback(selected: dict[str, Any] | None) -> str:
    if not selected:
        return "We couldn't find an ideal match. Try widening your budget or search."
    price = selected.get("quote", {}).get("price", selected["price"])
    return f"We recommend the {selected['name']} at ${price:.0f} — a great fit for what you asked."


def compose_product_recommendation(
    state: ConciergeWorkflowState, config: RunnableConfig
) -> dict[str, Any]:
    request = _request(state)
    selected = state.get("selected")
    if not selected:
        answer = _fallback(None)
    else:
        price = selected.get("quote", {}).get("price", selected["price"])
        prompt = (
            f"Product: {selected['name']} (SKU {selected['sku']})\nPrice: ${price:.0f}\n"
            f"Shopper request: {request}\nReply in English with a concise recommendation. No markdown."
        )
        answer = complete_recommendation(prompt, _fallback(selected), config=config)
    return {
        "answer": answer,
        "language": state.get("constraints", {}).get("language") or _language(request),
        "messages": [AIMessage(content=answer)],
        "trace": [*state.get("trace", []), "Respond: resposta composta para o shopper"],
    }


def verify_grounded_answer(state: ConciergeWorkflowState) -> dict[str, Any]:
    selected = state.get("selected")
    candidates = state.get("candidates") or []
    grounded = bool(selected) and selected.get("sku") in {item.get("sku") for item in candidates}
    grounded = grounded and selected.get("quote", {}).get("price") == selected.get("price") if selected else False
    return {
        "quality": {"grounded": grounded, "accuracy": 1.0 if grounded else 0.0},
        "trace": [*state.get("trace", []), f"Finalize: quality.grounded={grounded}"],
    }


def build_concierge_workflow():
    graph = StateGraph(ConciergeWorkflowState)
    graph.add_node(CONCIERGE_GRAPH_NODES["curator"], search_catalog_and_price)
    graph.add_node(CONCIERGE_GRAPH_NODES["respond"], compose_product_recommendation)
    graph.add_node(CONCIERGE_GRAPH_NODES["finalize"], verify_grounded_answer)
    graph.add_edge(START, CONCIERGE_GRAPH_NODES["curator"])
    graph.add_edge(CONCIERGE_GRAPH_NODES["curator"], CONCIERGE_GRAPH_NODES["respond"])
    graph.add_edge(CONCIERGE_GRAPH_NODES["respond"], CONCIERGE_GRAPH_NODES["finalize"])
    graph.add_edge(CONCIERGE_GRAPH_NODES["finalize"], END)
    return graph.compile(name="concierge.workflow").with_config(
        {"run_name": "concierge.workflow", "metadata": {"workflow_name": "concierge.workflow"}}
    )


workflow = build_concierge_workflow()


async def arun_workflow(request: str = "a birthday gift under $300", *, config=None) -> dict[str, Any]:
    resolved = resolve_config(config, feature="concierge")
    token = set_current_runnable_config(resolved)
    try:
        return await workflow.ainvoke(
            {"request": request, "messages": [], "trace": []}, config=resolved
        )
    finally:
        set_current_runnable_config(None, token)
