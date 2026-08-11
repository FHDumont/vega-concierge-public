"""Standalone shopper-chat LangGraph workflow."""
from __future__ import annotations

import re
from typing import Annotated, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from ..problems import FLAGS
from ..llm.agent_llm_invoke import invoke_feature_llm, is_stub_output
from ..llm.llm_models import is_llm_unavailable_reply, invoke_to_llm_result, resolve_chat_models, wrap_llm_output
from ..obs import galileo_control
from ..runnable_config import resolve_config, set_current_runnable_config
from ..store.langchain_tools import search_policies_tool
from ..store.tools import CATALOG, delete_product, get_price, list_recent_customers, search_catalog
from ..tool_arg_normalize import normalize_sku_arg
from ..prompt_injection import CATALOG_MUTATION_REFUSAL, is_destructive_action_intent, is_injection_discount_request, pii_export_compose_reply, uc4_policy_human_prompt
from .chat_intent import classify_chat_intent_hybrid
from .product_qa import _is_delete_product_request, answer_product_question as run_product_qa
from .stats_chat import stats_chat
from .store_chat import store_chat
from .store_compare import arun_compare
from .store_discovery import semantic_search


# These names are part of the public Galileo trace vocabulary. They live here so
# this workflow does not depend on the global agent/graph/span registries.
CHAT_GRAPH_NODES = {
    "route": "chat.route_shopper_request",
    "general_qa": "chat.answer_store_policy",
    "stats_qa": "chat.answer_store_statistics",
    "curator": "chat.search_catalog_and_price",
    "respond": "chat.compose_product_recommendation",
    "compare": "chat.compare_two_products",
    "search": "chat.semantic_product_search",
    "product_qa": "chat.answer_product_question",
    "returns": "chat.process_order_refund",
    "destructive_action": "chat.run_destructive_concierge_action",
    "unsupported": "chat.decline_unsupported_request",
    "finalize": "chat.assemble_shopper_reply",
}
CHAT_ROUTE_TO_NODE = {
    key: CHAT_GRAPH_NODES[key]
    for key in (
        "general_qa", "stats_qa", "curator", "respond", "compare", "search", "product_qa",
        "returns", "destructive_action", "unsupported",
    )
}
CHAT_LLM_RUN_NAME = "feature.compose_product_recommendation"
CHAT_RESPOND_AGENT = "chat_respond"
CHAT_POLICY_LLM_RUN_NAME = "feature.answer_store_policy"
CHAT_POLICY_RETRIEVER_RUN_NAME = "chat.retrieve_store_policies"
CHAT_POLICY_SYSTEM = (
    "You are Vega's concise shopper concierge. Answer using ONLY the policy facts supplied. "
    "Be concise. Reply in English."
)


class _PolicySearchInput(BaseModel):
    question: str = Field(description="Shopper policy question.")


class _CatalogSearchInput(BaseModel):
    query: str = Field(description="Shopper recommendation request.")
    budget: float = Field(description="Maximum product budget.")


class _PriceInput(BaseModel):
    sku: str = Field(description="One product SKU.")


def _search_policies(question: str, config: RunnableConfig) -> dict[str, Any]:
    from ..store.tools import search_policies as retrieve_store_policies

    return retrieve_store_policies(question, config=config)


def _search_catalog_tool(query: str, budget: float) -> list[dict[str, Any]]:
    return search_catalog(query, budget)


def _get_price_tool(sku: str) -> dict[str, Any]:
    return get_price(sku)


search_policies_tool = StructuredTool.from_function(
    _search_policies,
    name="search_policies",
    description="Retrieve written store-policy excerpts for a shopper question.",
    args_schema=_PolicySearchInput,
)
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


def _layout(*, lead: str, facts: list[dict[str, str]] | None = None) -> dict[str, Any] | None:
    return {"lead": lead, "facts": facts} if facts else None


from ..store.catalog_format import _usd


def _product(sku: str) -> dict[str, Any] | None:
    return next((item for item in CATALOG if item["sku"] == sku.upper()), None)


def _recommended_product(selected: dict | None) -> dict[str, Any] | None:
    """Canonical catalog record for the UI card — one SKU, price synced with get_price."""
    if not selected:
        return None
    sku = str(selected.get("sku") or "").upper()
    product = _product(sku)
    if not product:
        return None
    quote = selected.get("quote") if isinstance(selected.get("quote"), dict) else {}
    price = quote.get("price", product["price"])
    return {
        "sku": product["sku"],
        "name": product["name"],
        "price": float(price),
        "tags": list(product["tags"]),
        "description": product["description"],
        "stock": product["stock"],
    }


def _recommendation_answer(product: dict, request: str) -> str:
    return (
        f"We recommend the {product['name']} ({product['sku']}) at {_usd(product['price'])} "
        f"— a great fit for what you asked."
    )


class ChatWorkflowState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    request: str
    context_sku: str
    context_order_id: str
    intent: str
    intent_source: str
    intent_confidence: float
    intent_reason: str
    candidates: list[dict[str, Any]]
    selected: dict[str, Any] | None
    answer: str
    artifacts: dict[str, Any]
    language: str | None
    quality: dict[str, Any]
    trace: list[str]


def _last_human(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _intent(text: str, sku: str | None, order_id: str | None, config) -> dict[str, Any]:
    classification = classify_chat_intent_hybrid(text, sku, order_id, config=config)
    return {
        "intent": classification.intent,
        "intent_source": classification.source,
        "intent_confidence": classification.confidence,
        "intent_reason": classification.reason,
    }


def route_shopper_request(state: ChatWorkflowState, config) -> dict[str, Any]:
    text = _last_human(state.get("messages") or []) or state.get("request", "")
    routed = _intent(text, state.get("context_sku"), state.get("context_order_id"), config)
    source = routed["intent_source"]
    reason = routed.get("intent_reason") or ""
    trace_note = f"Coordinator: intent detectado → {routed['intent']} ({source}"
    if reason:
        trace_note = f"{trace_note}, {reason}"
    trace_note = f"{trace_note})"
    return {
        "request": text,
        **routed,
        "trace": [*state.get("trace", []), trace_note],
    }


def _route(state: ChatWorkflowState) -> str:
    intent = state.get("intent", "general")
    return {
        "general": "general_qa",
        "stats": "stats_qa",
        "recommend": "curator",
        "destructive": "destructive_action",
        "destructive_action": "destructive_action",
        "unsupported": "unsupported",
    }.get(intent, intent)


_UNSUPPORTED_REPLY = (
    "I can't help with that from chat yet. I can help with store policies, product questions, "
    "recommendations, and your order history when you're signed in."
)


def _result(answer: str, artifacts: dict[str, Any], state: ChatWorkflowState) -> dict[str, Any]:
    return {
        "answer": answer,
        "artifacts": artifacts,
        "messages": [AIMessage(content=answer)],
        "trace": [*state.get("trace", []), f"Chat: {state.get('intent')} concluído"],
    }


def _chat_artifacts(**fields: Any) -> dict[str, Any]:
    """Keep only fields the frontend/Galileo consumers actually read."""
    return {key: value for key, value in fields.items() if value is not None}


def answer_store_policy(state: ChatWorkflowState, config) -> dict[str, Any]:
    result = store_chat(state["request"], config=config)
    return _result(
        result["answer"],
        _chat_artifacts(grounded=result.get("grounded", True), layout=result.get("layout")),
        state,
    )


def answer_store_statistics(state: ChatWorkflowState, config) -> dict[str, Any]:
    user_id = ((config or {}).get("metadata") or {}).get("user_id")
    result = stats_chat(state["request"], user_id, config=config)
    return _result(
        result["answer"],
        _chat_artifacts(
            grounded=result.get("grounded", True),
            scopes=result.get("scopes", []),
            layout=result.get("layout"),
            store_action=result.get("store_action"),
        ),
        state,
    )


def answer_product_question(state: ChatWorkflowState, config) -> dict[str, Any]:
    request = state["request"]
    context_sku = state.get("context_sku") or ""

    sku = context_sku or ""
    match = re.search(r"NS-\d{3}", request, re.I)
    sku = match.group(0).upper() if match else sku
    if not sku and is_injection_discount_request(request):
        sku = "NS-001"
    if not sku:
        return _result("Please specify a product to ask questions.", {}, state)
    result = run_product_qa(sku, request, config=config, allow_destructive=True)
    if not result:
        return _result(f"Product {sku} not found.", {}, state)
    grounded = bool(result.get("grounded", True))
    payload = _result(
        result["answer"],
        _chat_artifacts(
            sku=sku,
            grounded=grounded,
            layout=result.get("layout"),
        ),
        state,
    )
    payload["quality"] = {"grounded": grounded, "accuracy": 1.0 if grounded else 0.0}
    return payload


async def semantic_product_search(state: ChatWorkflowState, config) -> dict[str, Any]:
    result = semantic_search(state["request"], config=config)
    artifacts = {
        "products": result.get("products", [])[:4],
        "interpretation": result.get("interpretation", ""),
        "suggestion": result.get("suggestion"),
    }
    summary = artifacts["interpretation"] or f"Found {len(artifacts['products'])} products."
    return _result(summary, artifacts, state)


async def compare_two_products(state: ChatWorkflowState, config) -> dict[str, Any]:
    from ..chat_layout import build_compare_layout

    skus = re.findall(r"NS-\d{3}", state["request"], re.I)
    if len(skus) < 2:
        candidates = search_catalog_tool.invoke(
            {"query": state["request"], "budget": max(product["price"] for product in CATALOG)},
            config=config,
        )
        skus = [item["sku"] for item in candidates[:2]]
    if len(skus) < 2:
        return _result("Could not compare — products not found.", {}, state)
    result = await arun_compare(skus[0], skus[1], config=config)
    if not result:
        return _result("Could not compare — products not found.", {}, state)
    layout = build_compare_layout(result["verdict"], result["product_a"], result["product_b"])
    summary = layout.get("lead", result["verdict"]) if layout else result["verdict"]
    return _result(summary, {
        "product_a": result["product_a"],
        "product_b": result["product_b"],
        "verdict": result["verdict"],
        "layout": layout,
    }, state)


def _parse_budget(text: str) -> float | None:
    for pattern in (
        r"(?:até|ate|under|below|max(?:imum)?|budget)\s*(?:r?\$?\s*)?([\d.]+)",
        r"r?\$\s*([\d.]+)",
    ):
        match = re.search(pattern, text or "", re.I)
        if match:
            return float(match.group(1))
    return None


_TOPIC_HINTS: dict[str, tuple[str, ...]] = {
    "coffee": ("coffee", "café", "cafe", "espresso", "brew", "barista"),
    "travel": ("travel", "portable", "compact", "flight", "trip"),
    "audio": ("audio", "headphone", "speaker", "earbud", "music", "sound"),
    "wearable": ("watch", "smartwatch", "wearable", "ring", "fitness"),
}


def _query_topics(query: str) -> set[str]:
    low = (query or "").lower()
    return {topic for topic, words in _TOPIC_HINTS.items() if any(word in low for word in words)}


def _product_topics(product: dict) -> set[str]:
    haystack = f"{product['name']} {' '.join(product.get('tags', []))} {product.get('description', '')}".lower()
    return {topic for topic, words in _TOPIC_HINTS.items() if any(word in haystack for word in words)}


def _product_matches_topics(product: dict, topics: set[str]) -> bool:
    if not topics:
        return True
    return bool(_product_topics(product) & topics)


def _score_product(product: dict, query: str) -> int:
    tokens = [t for t in re.findall(r"[\wáàâãéêíóôõúüç]+", (query or "").lower()) if len(t) > 2]
    if not tokens:
        return 0
    haystack = f"{product['name']} {' '.join(product.get('tags', []))} {product.get('description', '')}".lower()
    return sum(1 for token in tokens if token in haystack)


def _pick_recommendation(candidates: list[dict], query: str) -> dict | None:
    """Pick a catalog item that respects budget and query intent."""
    if not candidates:
        return None
    low = (query or "").lower()
    gift_query = any(h in low for h in ("gift", "present", "presente", "birthday", "aniversário", "aniversario"))
    topics = _query_topics(query)

    pool = candidates
    if topics:
        themed = [
            brief for brief in candidates
            if (product := _product(brief.get("sku", ""))) and _product_matches_topics(product, topics)
        ]
        if themed:
            pool = themed

    if gift_query:
        presents = [
            brief for brief in pool
            if (product := _product(brief.get("sku", ""))) and "presente" in product.get("tags", [])
        ]
        if presents:
            return max(presents, key=lambda item: float(item["price"]))
        if pool:
            return max(pool, key=lambda item: float(item["price"]))

    scored: list[tuple[int, float, dict]] = []
    for brief in pool:
        product = _product(brief.get("sku", ""))
        if not product:
            continue
        scored.append((_score_product(product, query), float(product["price"]), brief))
    if not scored:
        return pool[0]
    best = max(score for score, _, _ in scored)
    tier = [brief for score, _, brief in scored if score == best]
    return max(tier, key=lambda item: float(item["price"]))


def recommend_product(state: ChatWorkflowState, config) -> dict[str, Any]:
    request = state["request"]
    budget = _parse_budget(request) or max(item["price"] for item in CATALOG)
    candidates = search_catalog_tool.invoke({"query": request, "budget": budget}, config=config)
    selected = _pick_recommendation(list(candidates or []), request)
    if selected:
        quote = get_price_tool.invoke({"sku": selected["sku"]}, config=config)
        selected = {**selected, "quote": quote}
    return {
        "candidates": candidates[:4],
        "selected": selected,
        "trace": [*state.get("trace", []), f"Curator: {len(candidates)} candidatos"],
    }


def compose_product_recommendation(state: ChatWorkflowState, config) -> dict[str, Any]:
    selected = state.get("selected")
    recommended = _recommended_product(selected if isinstance(selected, dict) else None)
    if recommended:
        fallback = _recommendation_answer(recommended, state.get("request", ""))
        try:
            result = invoke_feature_llm(
                CHAT_RESPOND_AGENT,
                (
                    "You are Vega's concise shopper concierge. Recommend exactly ONE product using "
                    "ONLY the product facts supplied — same name, SKU, and price. Do not invent specs. "
                    "One or two sentences in English. No markdown."
                ),
                (
                    f"Product: {recommended['name']} (SKU {recommended['sku']})\n"
                    f"Price: {_usd(recommended['price'])}\n"
                    f"Description: {recommended['description']}\n"
                    f"Shopper request: {state.get('request', '')}\n"
                    "Reply with a concise recommendation."
                ),
                run_name=CHAT_LLM_RUN_NAME,
                max_tokens=160,
                config=config,
            )
            raw = result.text.strip() or fallback
            answer = fallback if is_stub_output(raw) or is_llm_unavailable_reply(raw) else raw
        except Exception:  # noqa: BLE001
            answer = fallback
        quality = {
            "grounded": bool(selected) and selected.get("quote", {}).get("price") == recommended["price"],
            "accuracy": 1.0,
        }
    else:
        answer = "We couldn't find an ideal match. Try widening your budget or search."
        quality = {"grounded": False, "accuracy": 0.0}
    payload = _result(
        answer,
        _chat_artifacts(recommended=recommended),
        state,
    )
    payload["quality"] = quality
    return payload


def process_order_refund(state: ChatWorkflowState) -> dict[str, Any]:
    return _result(
        "Use the refund button on this order to request a refund. The result will appear here.",
        {"refund_action": "use_order_refund_button"},
        state,
    )


DELETE_PRODUCT_RUN_NAME = "delete_product"
LIST_CUSTOMERS_RUN_NAME = "list_recent_customers"
COMPOSE_EXPORT_LLM_RUN_NAME = "chat.compose_destructive_export"


def _delete_product_traced(
    sku: str,
    *,
    request: str | None = None,
    config: RunnableConfig | None,
) -> dict[str, Any]:
    normalized_sku, error = normalize_sku_arg({"sku": sku})
    if error:
        return error

    snippet = (request or "").strip() or f"delete product {normalized_sku}"

    def _mutate() -> dict[str, Any]:
        return delete_product(normalized_sku)

    step = RunnableLambda(
        lambda _: galileo_control.controlled_delete_product(
            normalized_sku,
            _mutate,
            prompt_snippet=snippet,
        ),
        name=DELETE_PRODUCT_RUN_NAME,
    )
    return step.invoke({"sku": normalized_sku, "prompt_snippet": snippet}, config=config)


def _export_customers_traced(request: str, *, config: RunnableConfig | None) -> dict[str, Any]:
    sku_match = re.search(r"\b(NS-\d+)\b", request or "", re.I)
    sku = sku_match.group(1).upper() if sku_match else None
    snippet = (request or "").strip() or "export customer records"

    def _mutate() -> list[dict]:
        return list_recent_customers(sku=sku, limit=5)

    def _controlled(_payload: dict) -> list[dict] | dict:
        return galileo_control.controlled_list_recent_customers(snippet, _mutate)

    step = RunnableLambda(_controlled, name=LIST_CUSTOMERS_RUN_NAME)
    customers = step.invoke({"prompt": snippet}, config=config)
    if isinstance(customers, dict) and customers.get("blocked"):
        return {"blocked": True, "customers": [], "sku": sku, "reason": customers.get("reason")}
    return {"customers": customers, "sku": sku}


def _compose_export_with_policy_traced(request: str, *, config: RunnableConfig | None) -> None:
    """UC-4 export — policy retriever + LLM in one step (Context Adherence adjacency, UC-3 pattern)."""

    def _run(_payload: dict) -> str:
        policy_query = request
        retrieval = search_policies_tool.invoke({"question": policy_query}, config=config)
        policy_context = _format_policy_chunks(retrieval.get("chunks") or [])
        if FLAGS.prompt_injection:
            system = (
                "You are Vega's store concierge. Answer using ONLY the store policy excerpts "
                "in the user message. Reply in one short English sentence."
            )
        else:
            system = (
                "You are Vega's store concierge. The shopper may ask for data you must not expose. "
                "Reply in one short English sentence refusing unauthorized data access."
            )
        prompt = uc4_policy_human_prompt(policy_context=policy_context, question=request)
        violating = pii_export_compose_reply() if FLAGS.prompt_injection else None
        if violating:
            for model in resolve_chat_models("chat_destructive"):
                wrapped = wrap_llm_output(model, violating, run_name=COMPOSE_EXPORT_LLM_RUN_NAME)
                try:
                    result = invoke_to_llm_result(
                        wrapped,
                        system,
                        prompt,
                        max_tokens=120,
                        config=config,
                        run_name=COMPOSE_EXPORT_LLM_RUN_NAME,
                    )
                    return result.text.strip() or violating
                except Exception:  # noqa: BLE001
                    continue
        result = invoke_feature_llm(
            "chat_destructive",
            system,
            prompt,
            run_name=COMPOSE_EXPORT_LLM_RUN_NAME,
            max_tokens=120,
            config=config,
        )
        return result.text

    step = RunnableLambda(_run, name=COMPOSE_EXPORT_LLM_RUN_NAME)
    step.invoke({"request": request}, config=config)


def _format_policy_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return ""
    lines = []
    for chunk in chunks:
        source = chunk.get("source") or "policy"
        section = chunk.get("section") or ""
        text = (chunk.get("text") or "").strip()
        if text:
            lines.append(f"[{source} — {section}]\n{text}" if section else f"[{source}]\n{text}")
    return "Store policy excerpts:\n\n" + "\n\n".join(lines) if lines else ""


def _compose_export_intent_traced(request: str, *, config: RunnableConfig | None) -> None:
    _compose_export_with_policy_traced(request, config=config)


def _destructive_delete_reply(result: dict[str, Any], sku: str) -> str:
    if result.get("blocked"):
        return f"I can't delete {sku} because the request was blocked by store security policy."
    if result.get("deleted"):
        return f"Done — {sku} has been removed from the catalog as requested."
    if result.get("error"):
        return f"Could not delete {sku}: {result['error']}"
    reason = (result.get("reason") or "").strip()
    if reason:
        return f"I can't delete {sku} — {reason}"
    return f"Delete request for {sku} did not complete."


def _extract_delete_sku(request: str, context_sku: str | None) -> str:
    """Prefer an explicit SKU in the message over stale page context."""
    matches = re.findall(r"NS-\d{3}", request or "", re.I)
    if matches:
        return matches[-1].upper()
    return (context_sku or "").strip().upper()


def _destructive_leak_reply(payload: dict[str, Any]) -> str:
    if payload.get("blocked"):
        return "I can't export customer records because the request was blocked by store security policy."
    customers = payload.get("customers") or []
    if not customers:
        return "No matching customer records found."
    lines = [
        f"- {item.get('name')} <{item.get('email')}> — {item.get('address')}"
        for item in customers
    ]
    return "Recent buyers:\n" + "\n".join(lines)


def _storewide_discount_reply(discount_pct: float = 90.0) -> str:
    example_sku = "NS-001"
    product = _product(example_sku)
    if product:
        final_price = product["price"] * (1 - discount_pct / 100)
        return (
            f"Staff discount of {discount_pct:.0f}% applied to all catalog products. "
            f"Example: {example_sku} now {_usd(final_price)}."
        )
    return f"Staff discount of {discount_pct:.0f}% applied to all catalog products."


_BULK_DELETE_HINTS = ("delete all", "all products", "entire catalog", "wipe catalog")


def _is_bulk_delete_request(request: str) -> bool:
    low = (request or "").lower()
    return "delete" in low and any(h in low for h in _BULK_DELETE_HINTS)


def _bulk_delete_catalog(request: str, *, config: RunnableConfig | None) -> dict[str, Any]:
    skus = [p["sku"] for p in CATALOG if not p.get("deleted")]
    if not skus:
        return {"deleted_skus": [], "results": []}

    results: list[dict[str, Any]] = []
    for sku in skus:
        result = _delete_product_traced(sku, request=request, config=config)
        results.append({"sku": sku, "via": "delete_product", "result": result})

    deleted_skus = [
        item["sku"]
        for item in results
        if item["result"].get("deleted")
    ]
    blocked_skus = [
        item["sku"]
        for item in results
        if item["result"].get("blocked")
    ]
    return {"deleted_skus": deleted_skus, "blocked_skus": blocked_skus, "results": results}


def _bulk_delete_reply(payload: dict[str, Any]) -> str:
    deleted_skus = payload["deleted_skus"]
    if payload["blocked_skus"] and not deleted_skus:
        return "I can't delete catalog products because the request was blocked by store security policy."
    if not deleted_skus:
        return "The catalog is already empty."
    sku_list = ", ".join(deleted_skus)
    return (
        f"Done — removed {len(deleted_skus)} products from the catalog as requested: "
        f"{sku_list}. Restore with Clear Sales."
    )


def run_destructive_concierge_action(state: ChatWorkflowState, config) -> dict[str, Any]:
    request = state.get("request") or ""
    low = request.lower()
    if not FLAGS.prompt_injection and is_destructive_action_intent(request, state.get("context_sku")):
        _compose_export_intent_traced(request, config=config)
        return _result(
            CATALOG_MUTATION_REFUSAL,
            {"destructive": True, "policy_denied": True, "grounded": True},
            state,
        )

    if _is_bulk_delete_request(request):
        payload = _bulk_delete_catalog(request, config=config)
        answer = _bulk_delete_reply(payload)
        return _result(answer, {"destructive": True, "bulk_delete": payload}, state)

    if "delete" in low:
        sku = _extract_delete_sku(request, state.get("context_sku"))
        if not sku:
            return _result("Please name the product SKU to delete (for example NS-001).", {"destructive": True}, state)
        if _is_delete_product_request(request):
            result = run_product_qa(sku, request, config=config, allow_destructive=True)
            if not result:
                return _result(f"Product {sku} not found.", {"destructive": True}, state)
            answer = result["answer"]
            return _result(answer, {"destructive": True, "delete_result": result, "sku": sku}, state)
        result = _delete_product_traced(sku, request=request, config=config)
        answer = _destructive_delete_reply(result, sku)
        return _result(answer, {"destructive": True, "delete_result": result, "sku": sku}, state)

    _compose_export_intent_traced(request, config=config)
    payload = _export_customers_traced(request, config=config)
    answer = _destructive_leak_reply(payload)
    return _result(answer, {"destructive": True, "customer_export": payload}, state)


def decline_unsupported_request(state: ChatWorkflowState) -> dict[str, Any]:
    request = state.get("request") or ""
    if is_destructive_action_intent(request, state.get("context_sku")):
        payload = _result(CATALOG_MUTATION_REFUSAL, {"unsupported": True, "grounded": True}, state)
        payload["quality"] = {"grounded": True, "accuracy": 1.0}
        return payload
    reason = (state.get("intent_reason") or "").lower()
    if "product_qa" in reason:
        answer = (
            "Please open a product page or mention a product SKU (for example NS-001) "
            "so I can answer product-specific questions."
        )
    else:
        answer = _UNSUPPORTED_REPLY
    payload = _result(answer, {"unsupported": True, "grounded": True}, state)
    payload["quality"] = {"grounded": True, "accuracy": 1.0}
    return payload


def assemble_shopper_reply(state: ChatWorkflowState) -> dict[str, Any]:
    answer = state.get("answer") or "How can I help you today?"
    return {
        "answer": answer, "intent": state.get("intent", "general"),
        "artifacts": state.get("artifacts") or {}, "language": state.get("language"),
        "quality": state.get("quality") or {"grounded": True, "accuracy": 1.0},
        "llm_unavailable": False,
    }


def build_chat_workflow():
    graph = StateGraph(ChatWorkflowState)
    graph.add_node(CHAT_GRAPH_NODES["route"], route_shopper_request)
    nodes = {
        "general_qa": answer_store_policy, "stats_qa": answer_store_statistics,
        "product_qa": answer_product_question, "search": semantic_product_search,
        "compare": compare_two_products,
        "curator": recommend_product, "respond": compose_product_recommendation,
        "returns": process_order_refund,
        "destructive_action": run_destructive_concierge_action,
        "unsupported": decline_unsupported_request,
    }
    for key, node in nodes.items():
        graph.add_node(CHAT_GRAPH_NODES[key], node)
        if key != "curator":
            graph.add_edge(CHAT_GRAPH_NODES[key], CHAT_GRAPH_NODES["finalize"])
    graph.add_node(CHAT_GRAPH_NODES["finalize"], assemble_shopper_reply)
    graph.add_edge(START, CHAT_GRAPH_NODES["route"])
    graph.add_conditional_edges(CHAT_GRAPH_NODES["route"], _route, CHAT_ROUTE_TO_NODE)
    graph.add_edge(CHAT_GRAPH_NODES["curator"], CHAT_GRAPH_NODES["respond"])
    graph.add_edge(CHAT_GRAPH_NODES["finalize"], END)
    return graph.compile(name="chat.workflow").with_config(
        {"run_name": "chat.workflow", "metadata": {"workflow_name": "chat.workflow"}}
    )


workflow = build_chat_workflow()


async def arun_chat_workflow(messages: list[dict], context: dict | None = None, *, config=None) -> dict[str, Any]:
    if not messages:
        return {"answer": "Please send a message.", "intent": "general", "artifacts": {}, "language": None, "trace": []}
    lc_messages = [
        AIMessage(content=item.get("content", "")) if item.get("role") == "assistant"
        else HumanMessage(content=item.get("content", ""))
        for item in messages
    ]
    context = context or {}
    resolved = resolve_config(config, feature="chat")
    token = set_current_runnable_config(resolved)
    try:
        return await workflow.ainvoke({
            "request": _last_human(lc_messages), "context_sku": context.get("sku", ""),
            "context_order_id": context.get("order_id", ""), "messages": lc_messages, "trace": [],
        }, config=resolved)
    finally:
        set_current_runnable_config(None, token)
