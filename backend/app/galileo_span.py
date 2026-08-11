"""Human-readable labels for Splunk Agent Observability spans via LangChain (F-GALILEO-4).

Names describe **surface** (chat, concierge, compare…) + **business step**
(what the logic is doing), not internal roles (`coordinator`, `curator`) nor
LangChain classes (`RunnableSequence`, `ChatOpenAI`).

Complements `galileo_obs.GalileoAsyncCallback` — no SDK `@log` (ADR-032).
"""
from __future__ import annotations

from dataclasses import dataclass

# Business step per configurable agent_key — independent of surface.
BUSINESS_STEPS: dict[str, str] = {
    "concierge": "route_shopper_request",
    "curator": "search_catalog_and_price",
    "respond": "compose_product_recommendation",
    "store_chat": "answer_store_policy",
    "stats_chat": "answer_store_statistics",
    "chat_intent_classifier": "classify_shopper_intent",
    "compare_coordinator": "fetch_prices_for_comparison",
    "comparator": "write_comparison_verdict",
    "fulfillment_coordinator": "verify_cart_inventory_and_price",
    "fraude": "decide_fraud_allow_or_block",
    "returns_coordinator": "coordinate_refund_request",
    "eligibility": "check_refund_eligibility",
    "abuse_check": "screen_refund_abuse",
    "search": "semantic_product_search",
    "product_qa": "answer_product_question",
    "cart_crosssell": "suggest_cart_additions",
    "fraud_explain": "explain_fraud_hold",
    "admin_insights": "summarize_admin_metrics",
    "account_insights": "summarize_account_history",
    "notification_copy": "compose_notification_text",
    "chat_respond": "compose_product_recommendation",
}

# Fallback workflow when call site doesn't pass explicit `run_name` (F-GALILEO-13).
# Missing keys → `feature.{business_step}` (e.g. comparator → feature.write_comparison_verdict).
AGENT_DEFAULT_WORKFLOW: dict[str, str] = {
    "concierge": "concierge",
    "curator": "concierge",
    "respond": "concierge",
    "chat_respond": "chat",
    "compare_coordinator": "compare",
    "fulfillment_coordinator": "fulfillment",
    "fraude": "fulfillment",
    "returns_coordinator": "returns",
    "eligibility": "returns",
    "abuse_check": "returns",
}

# Retriever spans L4r — readable names in Console (avoids bare `VectorStoreRetriever`).
RETRIEVE_STORE_POLICIES_RUN_NAME = "retrieve_store_policies"
RETRIEVE_CATALOG_RUN_NAME = "retrieve_catalog"

# RAG prep inside feature chain — avoids generic RunnableAssign/RunnableSequence in Console.
MERGE_POLICY_CONTEXT_RUN_NAME = "feature.merge_policy_context"
MERGE_CATALOG_CONTEXT_RUN_NAME = "feature.merge_catalog_context"
MERGE_STATIC_CONTEXT_RUN_NAME = "feature.merge_static_context"
MERGE_POLICY_RETRIEVE_RUN_NAME = "feature.retrieve_policies_for_context"
MERGE_CATALOG_RETRIEVE_RUN_NAME = "feature.retrieve_catalog_for_context"
PREPARE_FEATURE_MESSAGES_RUN_NAME = "feature.prepare_messages"

# Tool span on F-022 cache hit (F-GALILEO-9) — StructuredTool, not workflow chain.
RESPONSE_CACHE_TOOL_NAME = "check_response_cache"

# Stats aggregation before LLM (F-TRACE-UX-1) — visible deterministic span, not RAG/tool.
AGGREGATE_STORE_STATISTICS = "aggregate_store_statistics"

# Chat deterministic routing/finalize (F-TRACE-UX-1) — mini-chains when no LLM span.
CHAT_ROUTE_DECISION = "chat.route_decision"

# Fraud decision on checkout — StructuredTool for input/output visible in Splunk Agent Observability Console.
FRAUD_DECISION_TOOL_NAME = "decide_fraud_allow_or_block"

# Eligibility and abuse on returns — StructuredTools for input/output visible in Splunk Agent Observability Console.
REFUND_ELIGIBILITY_TOOL_NAME = "check_refund_eligibility"
REFUND_ABUSE_TOOL_NAME = "screen_refund_abuse"

# Post-fraud checkout — StructuredTools for JSON I/O visible in Splunk Agent Observability Console (F-GALILEO-12).
CONFIRM_CART_STOCK_TOOL_NAME = "confirm_cart_stock"
CHARGE_PAYMENT_TOOL_NAME = "charge_payment"
SEND_ORDER_NOTIFICATION_TOOL_NAME = "send_order_notification"

# Post-ReAct refund — StructuredTool for JSON I/O visible in Splunk Agent Observability Console (F-GALILEO-16).
PROCESS_REFUND_TOOL_NAME = "process_refund"

# Curator misconfig (F-GALILEO-7) — destructive operation exposed to shopper-facing agent.
DELETE_PRODUCT_TOOL_NAME = "delete_product"
LIST_RECENT_CUSTOMERS_TOOL_NAME = "list_recent_customers"


def response_cache_replay_run_name(feature_run_name: str) -> str:
    """LCEL step that returns cached text — avoids generic `RunnableLambda` in Console."""
    return f"{feature_run_name}.replay_cached_response"


def response_cache_invoke_run_name(feature_run_name: str) -> str:
    """LCEL step after cache miss check — chains the real feature LLM chain."""
    return f"{feature_run_name}.invoke_llm"


def replay_stats_answer_run_name(feature_run_name: str) -> str:
    """Fast-path stats answer replay — avoids generic `RunnableLambda` in Console."""
    return f"{feature_run_name}.replay_stats_answer"

# LangGraph nodes — explicit surface in span id.
CHAT_GRAPH_NODES: dict[str, str] = {
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

CONCIERGE_GRAPH_NODES: dict[str, str] = {
    "route": "concierge.route_shopper_request",
    "curator": "concierge.search_catalog_and_price",
    "respond": "concierge.compose_product_recommendation",
    "finalize": "concierge.verify_grounded_answer",
}

# Internal routing keys (LLM structured output) → node id in chat graph.
CHAT_ROUTE_TO_NODE: dict[str, str] = {
    "general_qa": CHAT_GRAPH_NODES["general_qa"],
    "stats_qa": CHAT_GRAPH_NODES["stats_qa"],
    "curator": CHAT_GRAPH_NODES["curator"],
    "respond": CHAT_GRAPH_NODES["respond"],
    "compare": CHAT_GRAPH_NODES["compare"],
    "search": CHAT_GRAPH_NODES["search"],
    "product_qa": CHAT_GRAPH_NODES["product_qa"],
    "returns": CHAT_GRAPH_NODES["returns"],
    "destructive_action": CHAT_GRAPH_NODES["destructive_action"],
    "unsupported": CHAT_GRAPH_NODES["unsupported"],
    "complete": CHAT_GRAPH_NODES["finalize"],
}

CONCIERGE_ROUTE_TO_NODE: dict[str, str] = {
    "curator": CONCIERGE_GRAPH_NODES["curator"],
    "respond": CONCIERGE_GRAPH_NODES["respond"],
    "complete": CONCIERGE_GRAPH_NODES["finalize"],
}


def _step_slug(agent_key: str) -> str:
    if agent_key in BUSINESS_STEPS:
        return BUSINESS_STEPS[agent_key]
    return agent_key.replace("-", "_")


def default_llm_run_name(agent_key: str) -> str:
    """Default dotted `run_name`/`model.name` for L3 spans — aligns with L2/L4."""
    if not agent_key:
        return ""
    step = _step_slug(agent_key)
    workflow = AGENT_DEFAULT_WORKFLOW.get(agent_key)
    if workflow:
        return llm_run_name(workflow, step)
    return llm_run_name("feature", step)


def llm_run_name(workflow: str, step: str) -> str:
    """`run_name` for chains/structured-output — avoids `RunnableSequence` in trace."""
    return f"{workflow}.{step}"


def agent_llm_run_name(workflow: str, agent_key: str) -> str:
    """Dotted `run_name` for LLM spans of agents in graphs — aligns with `feature.{step}`."""
    return llm_run_name(workflow, _step_slug(agent_key))


@dataclass(frozen=True)
class ReactNodeNames:
    """LangGraph ReAct nodes — ids `{surface}.{business_step}`."""

    agent: str = "react.run_coordinator"
    tools: str = "react.run_tools"
    finalize: str = "react.finalize_outcome"
