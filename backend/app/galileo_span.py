"""Rótulos legíveis para spans Splunk Agent Observability via LangChain (F-GALILEO-4).

Nomes descrevem **superfície** (chat, concierge, compare…) + **passo de negócio**
(o que a lógica está fazendo), não papéis internos (`coordinator`, `curator`) nem
classes LangChain (`RunnableSequence`, `ChatOpenAI`).

Complementa `galileo_obs.GalileoAsyncCallback` — sem `@log` do SDK (ADR-032).
"""
from __future__ import annotations

from dataclasses import dataclass

# Passo de negócio por agent_key configurável — independente de superfície.
BUSINESS_STEPS: dict[str, str] = {
    "concierge": "route_shopper_request",
    "curator": "search_catalog_and_price",
    "respond": "compose_product_recommendation",
    "store_chat": "answer_store_policy",
    "stats_chat": "answer_store_statistics",
    "compare_coordinator": "fetch_prices_for_comparison",
    "comparator": "write_comparison_verdict",
    "fulfillment_coordinator": "verify_cart_inventory_and_price",
    "fraude": "decide_fraud_allow_or_block",
    "returns_coordinator": "coordinate_refund_request",
    "eligibility": "check_refund_eligibility",
    "abuse_check": "screen_refund_abuse",
    "search": "semantic_product_search",
    "product_qa": "answer_product_question",
    "gift_message": "write_gift_message",
    "product_desc": "write_product_description",
    "home_picks": "pick_homepage_products",
    "cart_crosssell": "suggest_cart_additions",
    "order_status": "explain_order_status",
    "fraud_explain": "explain_fraud_hold",
    "admin_insights": "summarize_admin_metrics",
    "account_insights": "summarize_account_history",
    "notification_copy": "compose_notification_text",
}

# Fallback workflow quando call site não passa `run_name` explícito (F-GALILEO-13).
# Chaves ausentes → `feature.{business_step}` (ex. comparator → feature.write_comparison_verdict).
AGENT_DEFAULT_WORKFLOW: dict[str, str] = {
    "concierge": "concierge",
    "curator": "concierge",
    "respond": "concierge",
    "compare_coordinator": "compare",
    "fulfillment_coordinator": "fulfillment",
    "fraude": "fulfillment",
    "returns_coordinator": "returns",
    "eligibility": "returns",
    "abuse_check": "returns",
}

# Retriever spans L4r — nomes legíveis no Console (evita `VectorStoreRetriever` cru).
RETRIEVE_STORE_POLICIES_RUN_NAME = "retrieve_store_policies"
RETRIEVE_CATALOG_RUN_NAME = "retrieve_catalog"

# Prep RAG dentro da feature chain — evita RunnableAssign/RunnableSequence genéricos no Console.
MERGE_POLICY_CONTEXT_RUN_NAME = "feature.merge_policy_context"
MERGE_CATALOG_CONTEXT_RUN_NAME = "feature.merge_catalog_context"
MERGE_STATIC_CONTEXT_RUN_NAME = "feature.merge_static_context"
MERGE_POLICY_RETRIEVE_RUN_NAME = "feature.retrieve_policies_for_context"
MERGE_CATALOG_RETRIEVE_RUN_NAME = "feature.retrieve_catalog_for_context"
PREPARE_FEATURE_MESSAGES_RUN_NAME = "feature.prepare_messages"

# Tool span no hit de cache F-022 (F-GALILEO-9) — StructuredTool, não workflow chain.
RESPONSE_CACHE_TOOL_NAME = "check_response_cache"

# Stats aggregation before LLM (F-TRACE-UX-1) — visible deterministic span, not RAG/tool.
AGGREGATE_STORE_STATISTICS = "aggregate_store_statistics"

# Chat deterministic routing/finalize (F-TRACE-UX-1) — mini-chains when no LLM span.
CHAT_ROUTE_DECISION = "chat.route_decision"

# Decisão de fraude no checkout — StructuredTool p/ input/output visível no Console Splunk Agent Observability.
FRAUD_DECISION_TOOL_NAME = "decide_fraud_allow_or_block"

# Elegibilidade e abuse no returns — StructuredTools p/ input/output visível no Console Splunk Agent Observability.
REFUND_ELIGIBILITY_TOOL_NAME = "check_refund_eligibility"
REFUND_ABUSE_TOOL_NAME = "screen_refund_abuse"

# Checkout pós-fraude — StructuredTools p/ I/O JSON visível no Console Splunk Agent Observability (F-GALILEO-12).
CONFIRM_CART_STOCK_TOOL_NAME = "confirm_cart_stock"
CHARGE_PAYMENT_TOOL_NAME = "charge_payment"
SEND_ORDER_NOTIFICATION_TOOL_NAME = "send_order_notification"

# Refund pós-ReAct — StructuredTool p/ I/O JSON visível no Console Splunk Agent Observability (F-GALILEO-16).
PROCESS_REFUND_TOOL_NAME = "process_refund"

# Curator misconfig (F-GALILEO-7) — operação destrutiva exposta ao shopper-facing agent.
DELETE_PRODUCT_TOOL_NAME = "delete_product"
LIST_RECENT_CUSTOMERS_TOOL_NAME = "list_recent_customers"


def response_cache_replay_run_name(feature_run_name: str) -> str:
    """Passo LCEL que devolve o texto cacheado — evita `RunnableLambda` genérico no Console."""
    return f"{feature_run_name}.replay_cached_response"


def response_cache_invoke_run_name(feature_run_name: str) -> str:
    """Passo LCEL pós-check de cache miss — encadeia a chain LLM real da feature."""
    return f"{feature_run_name}.invoke_llm"


def replay_stats_answer_run_name(feature_run_name: str) -> str:
    """Fast-path stats answer replay — evita RunnableLambda genérico no Console."""
    return f"{feature_run_name}.replay_stats_answer"

# Nós LangGraph — superfície explícita no id do span.
CHAT_GRAPH_NODES: dict[str, str] = {
    "route": "chat.route_shopper_request",
    "general_qa": "chat.answer_store_policy",
    "stats_qa": "chat.answer_store_statistics",
    "curator": "chat.search_catalog_and_price",
    "respond": "chat.compose_product_recommendation",
    "compare": "chat.compare_two_products",
    "search": "chat.semantic_product_search",
    "gift": "chat.write_gift_message",
    "product_qa": "chat.answer_product_question",
    "returns": "chat.process_order_refund",
    "destructive_action": "chat.run_destructive_concierge_action",
    "finalize": "chat.assemble_shopper_reply",
}

CONCIERGE_GRAPH_NODES: dict[str, str] = {
    "route": "concierge.route_shopper_request",
    "curator": "concierge.search_catalog_and_price",
    "respond": "concierge.compose_product_recommendation",
    "finalize": "concierge.verify_grounded_answer",
}

# Chaves internas de roteamento (LLM structured output) → id de nó no grafo chat.
CHAT_ROUTE_TO_NODE: dict[str, str] = {
    "general_qa": CHAT_GRAPH_NODES["general_qa"],
    "stats_qa": CHAT_GRAPH_NODES["stats_qa"],
    "curator": CHAT_GRAPH_NODES["curator"],
    "respond": CHAT_GRAPH_NODES["respond"],
    "compare": CHAT_GRAPH_NODES["compare"],
    "search": CHAT_GRAPH_NODES["search"],
    "gift": CHAT_GRAPH_NODES["gift"],
    "product_qa": CHAT_GRAPH_NODES["product_qa"],
    "returns": CHAT_GRAPH_NODES["returns"],
    "destructive_action": CHAT_GRAPH_NODES["destructive_action"],
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
    """Default dotted `run_name`/`model.name` para spans L3 — alinha com L2/L4."""
    if not agent_key:
        return ""
    step = _step_slug(agent_key)
    workflow = AGENT_DEFAULT_WORKFLOW.get(agent_key)
    if workflow:
        return llm_run_name(workflow, step)
    return llm_run_name("feature", step)


def llm_run_name(workflow: str, step: str) -> str:
    """`run_name` para chains/structured-output — evita `RunnableSequence` no trace."""
    return f"{workflow}.{step}"


def agent_llm_run_name(workflow: str, agent_key: str) -> str:
    """`run_name` dotted para LLM spans de agentes em grafos — alinha com `feature.{step}`."""
    return llm_run_name(workflow, _step_slug(agent_key))


@dataclass(frozen=True)
class ReactNodeNames:
    """Nós LangGraph ReAct — ids `{surface}.{business_step}`."""

    agent: str = "react.run_coordinator"
    tools: str = "react.run_tools"
    finalize: str = "react.finalize_outcome"
