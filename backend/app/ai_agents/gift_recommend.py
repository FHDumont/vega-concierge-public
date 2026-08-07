"""Standalone UC-2 gift recommendation workflow (`gift_recommend.workflow`)."""
from __future__ import annotations

import re
from typing import Any

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..llm.agent_llm_invoke import invoke_feature_llm, is_stub_output
from ..problems import FLAGS
from ..store.catalog_format import _usd
from ..store.tools import CATALOG, get_price, search_catalog

WORKFLOW_RUN_NAME = "gift_recommend.workflow"
RETRIEVE_RUN_NAME = "gift_recommend.retrieve_catalog_context"
RESCAN_RETRIEVE_RUN_NAME = "gift_recommend.rescan_catalog_context"
SEARCH_RUN_NAME = "gift_recommend.search_catalog"
RESCAN_RUN_NAME = "gift_recommend.rescan_catalog"
CONFIRM_SEARCH_RUN_NAME = "gift_recommend.confirm_catalog_search"
QUOTE_RUN_NAME = "gift_recommend.quote_selected_product"
VERIFY_RUN_NAME = "gift_recommend.verify_price_quote"
COMPOSE_RUN_NAME = "feature.compose_gift_recommendation"
POLISH_RUN_NAME = "gift_recommend.polish_recommendation"
OBSERVABILITY_RUN_NAME = "gift_recommend.attach_observability"
LLM_FEATURE = "gift_recommend"

_SYSTEM_PROMPT = (
    "You compose a product gift recommendation when the customer wants to shop. "
    "Use ONLY the product facts provided. Write a warm 1-2 sentence recommendation in English."
)
_POLISH_SYSTEM = (
    "You polish a product gift recommendation for tone and warmth. "
    "Keep the same product name, SKU, and price. One or two sentences in English."
)


class _CatalogSearchInput(BaseModel):
    query: str = Field(description="Shopper recommendation request.")
    budget: float = Field(description="Maximum product budget.")


class _PriceInput(BaseModel):
    sku: str = Field(description="One product SKU.")


class _CatalogRetriever(BaseRetriever):
    """Local retriever so catalog grounding is visible as [retriever] in Galileo traces."""

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


def _catalog_retriever(*, run_name: str) -> BaseRetriever:
    return _CatalogRetriever(documents=[
        Document(
            page_content=f"{item['name']} (SKU {item['sku']}). Tags: {', '.join(item['tags'])}.",
            metadata={"sku": item["sku"]},
        )
        for item in CATALOG
    ]).with_config({"run_name": run_name, "name": run_name})


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


def _find_product(sku: str) -> dict | None:
    needle = (sku or "").upper()
    return next((product for product in CATALOG if product["sku"] == needle), None)


def _parse_budget(text: str) -> float | None:
    for pattern in (
        r"(?:até|ate|under|below|max(?:imum)?|budget)\s*(?:r?\$?\s*)?([\d.]+)",
        r"r?\$\s*([\d.]+)",
    ):
        match = re.search(pattern, text or "", re.I)
        if match:
            return float(match.group(1))
    return None


def _select_gift(candidates: list[dict], request: str) -> dict | None:
    if not candidates:
        return None
    low = (request or "").lower()
    gift_query = any(h in low for h in ("gift", "present", "presente", "birthday"))
    pool = list(candidates)
    if gift_query:
        presents = [
            brief for brief in pool
            if (product := _find_product(str(brief.get("sku") or "")))
            and "presente" in product.get("tags", [])
        ]
        if presents:
            return max(presents, key=lambda item: float(item["price"]))
    return max(pool, key=lambda item: float(item["price"]))


def _recommended_record(selected: dict | None, quote: dict | None) -> dict[str, Any] | None:
    if not selected:
        return None
    product = _find_product(str(selected.get("sku") or ""))
    if not product:
        return None
    price = float((quote or {}).get("price", product["price"]))
    return {
        "sku": product["sku"],
        "name": product["name"],
        "price": price,
        "tags": list(product["tags"]),
        "description": product["description"],
        "stock": product["stock"],
    }


def _fallback_answer(recommended: dict | None, request: str) -> str:
    if not recommended:
        return "We couldn't find an ideal gift match. Try widening your budget or search."
    return (
        f"We recommend the {recommended['name']} ({recommended['sku']}) at "
        f"{_usd(recommended['price'])} — a great birthday gift for what you asked."
    )


def _format_catalog_context(documents: list[Document]) -> str:
    if not documents:
        return ""
    lines = [document.page_content for document in documents if document.page_content.strip()]
    return "Catalog excerpts:\n\n" + "\n\n".join(lines) if lines else ""


def _invoke_llm(
    prompt: str,
    system: str,
    *,
    run_name: str,
    max_tokens: int,
    config: RunnableConfig | None,
) -> str:
    result = invoke_feature_llm(
        LLM_FEATURE,
        system,
        prompt,
        run_name=run_name,
        max_tokens=max_tokens,
        verbose=FLAGS.cost_spike,
        config=config,
    )
    return result.text.strip()


def _retrieve_catalog_context_step(state: dict, config: RunnableConfig) -> dict:
    request = state["request"]
    documents = _catalog_retriever(run_name=RETRIEVE_RUN_NAME).invoke(request, config=config)
    return {
        **state,
        "catalog_context": _format_catalog_context(list(documents or [])),
    }


def _rescan_catalog_context_step(state: dict, config: RunnableConfig) -> dict:
    request = state["request"]
    documents = _catalog_retriever(run_name=RESCAN_RETRIEVE_RUN_NAME).invoke(request, config=config)
    rescan_context = _format_catalog_context(list(documents or []))
    prior = (state.get("catalog_context") or "").strip()
    merged = f"{prior}\n\n{rescan_context}".strip() if prior and rescan_context else prior or rescan_context
    return {**state, "catalog_context": merged}


def _search_catalog_step(state: dict, config: RunnableConfig) -> dict:
    request = state["request"]
    budget = state["budget"]
    candidates = search_catalog_tool.invoke({"query": request, "budget": budget}, config=config)
    selected = _select_gift(list(candidates or []), request)
    return {**state, "candidates": candidates or [], "selected": selected}


def _rescan_catalog_step(state: dict, config: RunnableConfig) -> dict:
    request = state["request"]
    budget = state["budget"]
    candidates = search_catalog_tool.invoke({"query": request, "budget": budget}, config=config)
    selected = _select_gift(list(candidates or []), request) or state.get("selected")
    return {**state, "candidates": candidates or state.get("candidates") or [], "selected": selected}


def _confirm_catalog_search_step(state: dict, config: RunnableConfig) -> dict:
    """Third identical catalog search — obvious token waste for UC-2 Agent Efficiency."""
    request = state["request"]
    budget = state["budget"]
    candidates = search_catalog_tool.invoke({"query": request, "budget": budget}, config=config)
    selected = _select_gift(list(candidates or []), request) or state.get("selected")
    return {**state, "candidates": candidates or state.get("candidates") or [], "selected": selected}


def _quote_selected_product_step(state: dict, config: RunnableConfig) -> dict:
    selected = state.get("selected")
    if not selected:
        return {**state, "quote": None}
    quote = get_price_tool.invoke({"sku": str(selected["sku"])}, config=config)
    return {**state, "quote": quote}


def _verify_price_quote_step(state: dict, config: RunnableConfig) -> dict:
    selected = state.get("selected")
    if not selected:
        return state
    quote = get_price_tool.invoke({"sku": str(selected["sku"])}, config=config)
    return {**state, "quote": quote}


def _compose_gift_recommendation_step(state: dict, config: RunnableConfig) -> dict:
    request = state["request"]
    recommended = _recommended_record(state.get("selected"), state.get("quote"))
    fallback = _fallback_answer(recommended, request)
    if not recommended:
        return {**state, "recommended": None, "answer": fallback, "quality": {"grounded": False, "accuracy": 0.0}}
    context = (state.get("catalog_context") or "").strip()
    prompt = (
        f"{context}\n\n" if context else ""
    ) + (
        f"Product: {recommended['name']} (SKU {recommended['sku']})\n"
        f"Price: {_usd(recommended['price'])}\n"
        f"Shopper request: {request}\n"
        "Reply with a concise recommendation."
    )
    try:
        text = _invoke_llm(
            prompt, _SYSTEM_PROMPT, run_name=COMPOSE_RUN_NAME, max_tokens=160, config=config,
        )
        answer = fallback if is_stub_output(text) or not text else text
    except Exception:  # noqa: BLE001
        answer = fallback
    quality = {
        "grounded": bool(recommended),
        "accuracy": 1.0 if recommended else 0.0,
    }
    return {**state, "recommended": recommended, "answer": answer, "quality": quality}


def _polish_recommendation_step(state: dict, config: RunnableConfig) -> dict:
    recommended = state.get("recommended")
    answer = state.get("answer") or ""
    if not recommended or not answer:
        return state
    prompt = (
        f"Draft recommendation:\n{answer}\n\n"
        f"Product facts:\n{recommended['name']} ({recommended['sku']}) at {_usd(recommended['price'])}"
    )
    try:
        text = _invoke_llm(
            prompt, _POLISH_SYSTEM, run_name=POLISH_RUN_NAME, max_tokens=180, config=config,
        )
        if text and not is_stub_output(text):
            answer = text
    except Exception:  # noqa: BLE001
        pass
    state = {**state, "answer": answer}
    return state


def _attach_observability_step(state: dict, config: RunnableConfig) -> dict:
    if not FLAGS.cost_spike:
        return state
    return {
        **state,
        "observability": {
            "redundant_steps": [
                RESCAN_RETRIEVE_RUN_NAME,
                RESCAN_RUN_NAME,
                CONFIRM_SEARCH_RUN_NAME,
                VERIFY_RUN_NAME,
                POLISH_RUN_NAME,
            ],
            "duplicate_tool_calls": {"search_catalog": 3, "get_price": 2},
            "retriever_passes": 2,
            "llm_passes": 2,
        },
    }


def _named_step(fn, run_name: str) -> RunnableLambda:
    return RunnableLambda(fn, name=run_name).with_config({"run_name": run_name})


def recommend_gift(request: str, *, config=None) -> dict[str, Any]:
    """Run UC-2 gift recommendation with observable retriever, tool, and LLM spans."""
    request = (request or "").strip() or "a birthday gift under $300"
    budget = _parse_budget(request) or max(product["price"] for product in CATALOG)
    initial = {"request": request, "budget": budget}

    steps = [
        _named_step(_retrieve_catalog_context_step, RETRIEVE_RUN_NAME),
        _named_step(_search_catalog_step, SEARCH_RUN_NAME),
        _named_step(_quote_selected_product_step, QUOTE_RUN_NAME),
        _named_step(_compose_gift_recommendation_step, COMPOSE_RUN_NAME),
    ]
    if FLAGS.cost_spike:
        steps = [
            _named_step(_retrieve_catalog_context_step, RETRIEVE_RUN_NAME),
            _named_step(_search_catalog_step, SEARCH_RUN_NAME),
            _named_step(_rescan_catalog_context_step, RESCAN_RETRIEVE_RUN_NAME),
            _named_step(_rescan_catalog_step, RESCAN_RUN_NAME),
            _named_step(_confirm_catalog_search_step, CONFIRM_SEARCH_RUN_NAME),
            _named_step(_quote_selected_product_step, QUOTE_RUN_NAME),
            _named_step(_verify_price_quote_step, VERIFY_RUN_NAME),
            _named_step(_compose_gift_recommendation_step, COMPOSE_RUN_NAME),
            _named_step(_polish_recommendation_step, POLISH_RUN_NAME),
            _named_step(_attach_observability_step, OBSERVABILITY_RUN_NAME),
        ]

    workflow = steps[0]
    for step in steps[1:]:
        workflow = workflow | step
    metadata = {"workflow_name": WORKFLOW_RUN_NAME}
    if FLAGS.cost_spike:
        metadata["cost_spike"] = True
    workflow = workflow.with_config({
        "run_name": WORKFLOW_RUN_NAME,
        "name": WORKFLOW_RUN_NAME,
        "metadata": metadata,
    })
    final = workflow.invoke(initial, config=config)
    return {
        "answer": final.get("answer") or _fallback_answer(None, request),
        "recommended": final.get("recommended"),
        "quality": final.get("quality") or {"grounded": False, "accuracy": 0.0},
    }
