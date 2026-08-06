"""Chat hub-and-spoke graph (F-050-CHAT, F-051, F-052, F-053) — compare/search/gift/product_qa/returns/stats/general spokes.

coordinator → {general_qa|stats_qa|curator|respond|compare|search|gift|product_qa|returns|complete} → chat_finalize.
"""
from __future__ import annotations

import re
from typing import Annotated, List, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from ..tools import CATALOG, search_catalog
from .concierge import (
    _ensure_initial_messages,
    _is_invoked,
    _parse_request_budget,
    _record_llm_turn,
    _tool_result_named,
    curator_node,
    respond_node,
)
from .. import agent_config
from ..galileo_span import (
    CHAT_GRAPH_NODES,
    CHAT_ROUTE_TO_NODE,
    CHAT_ROUTE_DECISION,
    DELETE_PRODUCT_TOOL_NAME,
    LIST_RECENT_CUSTOMERS_TOOL_NAME,
    llm_run_name,
)
from ..llm_models import get_chat_model, make_system_message
from ..problems import FLAGS
from ..runnable_config import derive_feature_config

INTENT_AGENTS: dict[str, list[str]] = {
    "general": ["general_qa"],
    "stats": ["stats_qa"],
    "recommend": ["curator", "respond"],
    "compare": ["compare"],
    "search": ["search"],
    "gift": ["gift"],
    "product_qa": ["product_qa"],
    "returns": ["returns"],
    "destructive": ["destructive_action"],
}

CHAT_AGENT_CATALOG: dict[str, tuple[str, str]] = {
    "general_qa": ("Answers general store questions grounded in written policies.", "general_qa_summary"),
    "stats_qa": ("Answers factual questions about catalog, sales, and account statistics.", "stats_qa_summary"),
    "curator": ("Finds catalog candidates and grounded prices via tools.", "curator_summary"),
    "respond": ("Composes the shopper-facing recommendation from real product facts.", "respond_summary"),
    "compare": ("Compares two products side by side with a verdict.", "compare_summary"),
    "search": ("Semantic product search with interpretation.", "search_summary"),
    "gift": ("Generates a personalized gift message.", "gift_summary"),
    "product_qa": ("Answers questions about a specific product (requires SKU context).", "product_qa_summary"),
    "returns": ("Processes a refund/return for a delivered order.", "returns_summary"),
    "destructive_action": (
        "Executes privileged catalog actions (delete SKU, export buyer records) via concierge tools.",
        "destructive_summary",
    ),
}


class ChatRoutingDecision(BaseModel):
    """Coordinator routing for chat — which specialist to invoke next."""

    next_agent: Literal[
        "general_qa", "stats_qa", "curator", "respond", "compare", "search", "gift",
        "product_qa", "returns", "destructive_action", "complete",
    ] = Field(description="Next specialist, or 'complete' when ready to finalize.")
    reasoning: str = Field(default="", description="Brief justification for the route.")


class ChatState(TypedDict, total=False):
    """Hub-and-spoke state for POST /api/chat."""

    messages: Annotated[list[BaseMessage], add_messages]
    request: str
    context_sku: str
    context_order_id: str
    constraints: dict
    candidates: List[dict]
    selected: Optional[dict]
    answer: str
    language: str
    quality: dict
    trace: List[str]
    next_agent: str
    intent: str
    artifacts: dict
    curator_summary: Optional[str]
    respond_summary: Optional[str]
    compare_summary: Optional[str]
    search_summary: Optional[str]
    gift_summary: Optional[str]
    product_qa_summary: Optional[str]
    returns_summary: Optional[str]
    general_qa_summary: Optional[str]
    stats_qa_summary: Optional[str]
    destructive_summary: Optional[str]


def _last_human_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


_SHOPPING_FOR_GIFT_HINTS = (
    "gift for", "present for", "presente para", "gift idea", "birthday gift",
    "a gift for", "a present for", "un presente para", "um presente para",
)


def _is_gift_message_intent(text: str) -> bool:
    """Gift-message intent (checkout copy) vs shopping-for-a-gift (recommend).

    'Gift for a coffee lover' → recommend; 'write a gift message' → gift."""
    low = (text or "").lower().strip()
    if any(h in low for h in _SHOPPING_FOR_GIFT_HINTS):
        return False
    message_hints = (
        "gift message", "write a gift", "mensagem de presente",
        "mensagem para presente", "write a message", "gift card message",
    )
    if any(h in low for h in message_hints):
        return True
    if re.search(r"\b(write|compose|create|help me write)\b.*\bmessage\b", low):
        return True
    return False


def _is_returns_action_intent(text: str, context_order_id: str | None) -> bool:
    """Transactional refund/return request — not policy FAQ."""
    low = (text or "").lower()
    return_keywords = ("refund", "return", "devolução", "devolucao", "reembolso")
    has_return_topic = any(k in low for k in return_keywords)
    action_hints = (
        "i want", "please process", "process my", "request a refund", "request refund",
        "quero reembolso", "quero devolver", "devolver meu", "devolver o pedido",
        "get my money back", "money back", "please refund", "need a refund",
    )
    info_hints = (
        "how", "what", "what's", "what is", "policy", "window", "prazo", "quantos dias",
        "how many days", "can i return", "qual é", "qual a política", "qual a politica",
        "tell me about", "explain",
    )
    if not has_return_topic:
        if context_order_id and any(k in low for k in ("refund", "reembolso", "devolv")):
            return any(h in low for h in action_hints) or "process" in low
        return False
    if any(h in low for h in info_hints):
        return False
    if any(h in low for h in action_hints):
        return True
    if "can i" in low or "posso" in low:
        return False
    if context_order_id:
        return True
    return False


_SHOPPING_HINTS = (
    "gift under", "gift for", "present for", "presente para",
    "recommend", "recomend", "looking for", "procurando",
    "under r$", "under $", "budget", "birthday", "aniversário", "anniversary",
    "buy a", "comprar", "need a", "preciso de", "show me", "me mostre",
    "something for", "algo para",
    "for a coffee", "coffee lover", "for travel", "compact", "portable",
)

_SHOPPING_RECIPIENT_WORDS = (
    "for", "lover", "who loves", "someone who", "para", "for my", "for a",
)


def _is_shopping_intent(text: str) -> bool:
    low = (text or "").lower()
    if any(h in low for h in _SHOPPING_HINTS):
        return True
    if re.search(r"\bsomething\b.*\bfor\b", low):
        return True
    if re.search(r"\b(under|below|até|ate)\s+(r\$|us\$|\$)?\s*\d", low):
        return True
    if not _is_gift_message_intent(text):
        if any(w in low for w in ("gift", "present", "presente")):
            if any(w in low for w in _SHOPPING_RECIPIENT_WORDS):
                return True
            if any(w in low for w in ("under", "below", "budget", "r$", "$", "birthday", "for my", "para")):
                return True
    return False


def _is_stats_question(text: str) -> bool:
    """Pergunta factuais sobre catálogo, vendas ou histórico do usuário (F-053)."""
    low = (text or "").lower()
    hints = (
        "most expensive", "most cheap", "cheapest", "expensive", "price range", "best seller",
        "best-selling", "best selling", "bestseller", "most sold", "most popular", "how much spent", "how much have i spent",
        "total spent", "how many orders", "how many purchases", "purchase count", "my spending",
        "my orders", "most bought", "last order", "out of stock", "low stock",
        "mais caro", "mais barato", "mais vendido", "mais popular", "quanto gastei", "gastei",
        "quantas compras", "minhas compras", "meu histórico", "meu historico", "quantos produtos",
        "how many products",
    )
    return any(h in low for h in hints)


_CONTEXT_ITEM_RE = re.compile(
    r"\b(this|it|its|isso|este|esta|esse|essa|deste|desta|desse|dessa)\b", re.I,
)
_PRODUCT_INQUIRY_RE = re.compile(
    r"\b(tell me about|what is|what's|how is|how's|describe|info on|information on|details on)\b",
    re.I,
)


def _should_route_product_qa(text: str, context_sku: str | None) -> bool:
    """Com SKU de página aberta, pergunta sobre o produto — não exige '?'."""
    if not context_sku or _is_store_policy_question(text):
        return False
    msg = text or ""
    if re.search(r"NS-\d{3}", msg, re.I):
        return True
    if _CONTEXT_ITEM_RE.search(msg):
        return True
    if _PRODUCT_INQUIRY_RE.search(msg):
        return True
    return "?" in msg


def _is_context_item_question(text: str, context_sku: str | None) -> bool:
    """Pergunta sobre o item aberto na página, não sobre a loja — desempata stats vs product_qa."""
    if not context_sku:
        return False
    return bool(_CONTEXT_ITEM_RE.search(text or ""))


def _is_store_policy_question(text: str) -> bool:
    """Pergunta sobre política/loja — não sobre o produto em context_sku."""
    low = (text or "").lower()
    hints = (
        "policy", "policies", "política", "politica", "políticas", "politicas",
        "rules", "rule", "terms", "privacy",
        "return window", "devolução", "devolucao",
        "refund policy", "shipping", "frete", "warranty", "garantia", "payment",
        "pagamento", "quantos dias", "how many days", "prazo", "who are you",
        "quem é", "are you a bot", "você é um bot", "store hours", "horário",
        "what are your", "what is your",
    )
    return any(h in low for h in hints)


def _is_destructive_action_intent(text: str) -> bool:
    """UC-4 — delete catalog SKU or export other shoppers' PII (prompt_injection must be ON)."""
    low = (text or "").lower()
    if "delete" in low and (re.search(r"\bNS-\d+\b", text or "", re.I) or "ns-001" in low):
        return True
    if not any(w in low for w in ("customer", "buyer", "shopper", "user", "email", "address")):
        return False
    return any(w in low for w in (
        "list", "show", "print", "export", "all", "other", "recent", "dump", "every",
    ))


def _detect_chat_intent(text: str, context_sku: str | None, context_order_id: str | None = None) -> str:
    """Deterministic intent from keywords (stub offline routing). Default: general (F-052)."""
    if FLAGS.prompt_injection and _is_destructive_action_intent(text):
        return "destructive"
    low = (text or "").lower()
    if "compare" in low or "comparar" in low:
        return "compare"
    if _is_gift_message_intent(text):
        return "gift"
    if "search" in low or "buscar" in low:
        return "search"
    if _is_stats_question(text) and not _is_context_item_question(text, context_sku):
        return "stats"
    if not _is_store_policy_question(text):
        if _should_route_product_qa(text, context_sku):
            return "product_qa"
        sku_match = re.search(r"NS-\d{3}", text or "", re.I)
        if sku_match and "?" in text:
            return "product_qa"
    if _is_returns_action_intent(text, context_order_id):
        return "returns"
    if _is_shopping_intent(text):
        return "recommend"
    return "general"


def _remaining_chat_agents(state: ChatState) -> list[str]:
    intent = state.get("intent") or "general"
    needed = INTENT_AGENTS.get(intent, ["general_qa"])
    remaining: list[str] = []
    for name in needed:
        _, field = CHAT_AGENT_CATALOG[name]
        if not _is_invoked(state.get(field)):
            remaining.append(name)
    return remaining


def _build_chat_coordinator_instructions(remaining: list[str], intent: str) -> str:
    cfg = agent_config.get_agent("concierge")
    base = agent_config.effective_system(cfg)
    if not remaining:
        rules = (
            "Available specialists: none remaining.\n"
            "Rules:\n- Choose 'complete' — the request is ready for finalization.\n"
            "- Reply ONLY with JSON: {\"next_agent\": \"complete\", \"reasoning\": \"<short>\"}."
        )
    else:
        lines = ["Available specialists:"]
        for name in remaining:
            desc = CHAT_AGENT_CATALOG[name][0]
            lines.append(f"- {name}: {desc}")
        agent_list = ", ".join(remaining)
        rules = (
            "\n".join(lines)
            + f"\n\nCurrent intent: {intent}.\n\nRules:\n"
            f"- `next_agent` MUST be one of: {agent_list}, or 'complete' if already satisfied.\n"
            f"- Only choose one of: {agent_list}, or 'complete' if already satisfied.\n"
            "- For recommend intent: route curator first, then respond.\n"
            "- For general intent: route general_qa once, then complete.\n"
            "- For stats intent: route stats_qa once, then complete.\n"
            "- For other intents: route the matching specialist once, then complete.\n"
            "- Do not choose specialists not listed above.\n"
            "- Reply ONLY with JSON: {\"next_agent\": \"<specialist or complete>\", \"reasoning\": \"<short>\"}.\n"
            "- Reply with raw JSON only — no markdown code fences."
        )
    return f"{base}\n\n{rules}"


def _deterministic_chat_route(
    remaining: list[str],
    last_message: str,
    context_sku: str | None,
    context_order_id: str | None = None,
) -> ChatRoutingDecision:
    if not remaining:
        return ChatRoutingDecision(next_agent="complete", reasoning="All specialists invoked.")
    low = (last_message or "").lower()
    keyword_routes = [
        (("compare", "comparar"), "compare"),
        (("search", "buscar"), "search"),
    ]
    for keywords, agent in keyword_routes:
        if any(k in low for k in keywords) and agent in remaining:
            return ChatRoutingDecision(
                next_agent=agent, reasoning=f"Deterministic keyword → {agent}."
            )
    if _is_gift_message_intent(last_message) and "gift" in remaining:
        return ChatRoutingDecision(next_agent="gift", reasoning="Deterministic keyword → gift.")
    if _is_returns_action_intent(last_message, context_order_id) and "returns" in remaining:
        return ChatRoutingDecision(next_agent="returns", reasoning="Deterministic keyword → returns.")
    if "stats_qa" in remaining:
        return ChatRoutingDecision(next_agent="stats_qa", reasoning="Deterministic → stats_qa.")
    if (
        not _is_store_policy_question(last_message)
        and _should_route_product_qa(last_message, context_sku)
        and "product_qa" in remaining
    ):
        return ChatRoutingDecision(next_agent="product_qa", reasoning="Question + SKU context → product_qa.")
    if "general_qa" in remaining:
        return ChatRoutingDecision(next_agent="general_qa", reasoning="Deterministic → general_qa.")
    order = ["curator", "respond"]
    for name in order:
        if name in remaining:
            return ChatRoutingDecision(next_agent=name, reasoning=f"Deterministic fallback → {name}.")
    if remaining:
        return ChatRoutingDecision(next_agent=remaining[0], reasoning=f"Deterministic → {remaining[0]}.")
    return ChatRoutingDecision(next_agent="complete", reasoning="No remaining specialists.")


def _emit_chat_route_decision_span(
    decision: ChatRoutingDecision,
    *,
    config: RunnableConfig | None,
) -> None:
    """Mini-chain when routing is deterministic — no LLM span otherwise."""
    if not config or not config.get("callbacks"):
        return
    payload = {"next_agent": decision.next_agent, "reasoning": decision.reasoning}
    try:
        chain = RunnableLambda(
            lambda _: payload,
            name=CHAT_ROUTE_DECISION,
        ).with_config({"run_name": CHAT_ROUTE_DECISION, "name": CHAT_ROUTE_DECISION})
        chain.invoke({}, config=config)
    except Exception:  # noqa: BLE001
        pass


def _emit_chat_finalize_span(
    intent: str,
    reply: str,
    *,
    config: RunnableConfig | None,
) -> None:
    """Visible assemble step with truncated reply preview."""
    if not config or not config.get("callbacks"):
        return
    preview = (reply or "")[:200]
    payload = {"intent": intent, "reply_preview": preview}
    run_name = CHAT_GRAPH_NODES["finalize"]
    try:
        chain = RunnableLambda(
            lambda _: payload,
            name=run_name,
        ).with_config({"run_name": run_name, "name": run_name})
        chain.invoke({}, config=config)
    except Exception:  # noqa: BLE001
        pass


def _invoke_chat_routing_decision(
    state: ChatState,
    remaining: list[str],
    lc_messages: list[BaseMessage],
    budget: float,
    intent: str,
    *,
    config: RunnableConfig | None,
) -> ChatRoutingDecision:
    if not remaining:
        decision = ChatRoutingDecision(next_agent="complete", reasoning="Nothing left to route.")
        _emit_chat_route_decision_span(decision, config=config)
        return decision
    if len(remaining) == 1:
        decision = ChatRoutingDecision(
            next_agent=remaining[0],
            reasoning=f"Deterministic → only {remaining[0]} remaining.",
        )
        _emit_chat_route_decision_span(decision, config=config)
        return decision

    from ..agents import _parse_json  # import tardio: ciclo graphs.chat↔agents
    from ..llm_models import VegaStubChatModel, resolve_chat_models, _with_run_name

    instructions = _build_chat_coordinator_instructions(remaining, intent)
    invoke_messages: list[BaseMessage] = [
        make_system_message(get_chat_model("concierge"), instructions),
        *lc_messages,
    ]

    last_text = _last_human_text(lc_messages)
    context_sku = state.get("context_sku") or None
    context_order_id = state.get("context_order_id") or None

    models = resolve_chat_models("concierge")
    last_err: Exception | None = None
    for i, candidate in enumerate(models):
        if i == 0:
            candidate = get_chat_model("concierge")
        if isinstance(candidate, VegaStubChatModel):
            return _deterministic_chat_route(remaining, last_text, context_sku, context_order_id)
        try:
            run_name = llm_run_name("chat", "route_shopper_request")
            bound = _with_run_name(candidate, candidate, run_name)
            response = bound.invoke(invoke_messages, config=config)
            text = response.content if isinstance(response.content, str) else str(response.content)
            parsed = _parse_json(text)
            if parsed:
                return ChatRoutingDecision.model_validate(parsed)
            raise ValueError("routing JSON missing")
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue

    del last_err
    return _deterministic_chat_route(remaining, last_text, context_sku, context_order_id)


def chat_coordinator_node(state: ChatState, config: RunnableConfig) -> dict:
    """Route to chat specialists or complete."""
    lc_messages, request, budget, trace = _ensure_initial_messages(state)
    trace = list(trace)
    context_sku = state.get("context_sku") or ""
    context_order_id = state.get("context_order_id") or ""

    if not lc_messages:
        trace.append("Coordinator: nenhuma mensagem do shopper")
        return {"trace": trace, "next_agent": "complete", "messages": []}

    intent = state.get("intent")
    if not intent:
        intent = _detect_chat_intent(
            _last_human_text(lc_messages), context_sku or None, context_order_id or None,
        )
        trace.append(f"Coordinator: intent detectado → {intent}")

    remaining = _remaining_chat_agents({**state, "intent": intent})

    import json
    import time

    t0 = time.perf_counter()
    decision = _invoke_chat_routing_decision(
        state, remaining, lc_messages, budget, intent, config=config,
    )
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
        # Specialists do intent ainda pendentes: 'complete' é prematuro (modelos pequenos
        # encerram cedo). O intent define quem precisa rodar — respeita a lista.
        trace.append(f"Coordinator: 'complete' prematuro → {remaining[0]}")
        next_agent = remaining[0]
    elif not remaining and next_agent != "complete":
        next_agent = "complete"

    trace.append(f"Coordinator → {next_agent} ({decision.reasoning[:80] or 'routing'})")

    cfg = agent_config.get_agent("concierge")
    _record_llm_turn(
        feature="chat",
        agent_name="concierge",
        system=agent_config.effective_system(cfg),
        prompt=lc_messages[-1].content if lc_messages else request,
        response=AIMessage(content=json.dumps(decision.model_dump())),
        model=get_chat_model("concierge"),
        latency_ms=latency_ms,
    )

    return {
        "request": request,
        "intent": intent,
        "next_agent": next_agent,
        "trace": trace,
        "messages": lc_messages if not state.get("messages") else [],
    }


def _resolve_two_skus(text: str, budget: float) -> tuple[str | None, str | None]:
    """Extract or resolve two SKUs from user text."""
    sku_pattern = re.findall(r"NS-\d{3}", text, re.I)
    if len(sku_pattern) >= 2:
        return sku_pattern[0].upper(), sku_pattern[1].upper()

    low = text.lower()
    matched: list[str] = []
    for p in CATALOG:
        name_low = p["name"].lower()
        if name_low in low or any(word in low for word in name_low.split() if len(word) > 4):
            matched.append(p["sku"])
    if len(matched) >= 2:
        return matched[0], matched[1]

    candidates = search_catalog(text, budget)
    if len(candidates) >= 2:
        return candidates[0]["sku"], candidates[1]["sku"]
    if len(candidates) == 1:
        other = next((p["sku"] for p in CATALOG if p["sku"] != candidates[0]["sku"]), None)
        return candidates[0]["sku"], other
    if len(CATALOG) >= 2:
        return CATALOG[0]["sku"], CATALOG[1]["sku"]
    return None, None


async def compare_node(state: ChatState, config: RunnableConfig) -> dict:
    """Specialist: compare two products."""
    from .compare import arun_compare  # import tardio: ciclo chat↔compare
    from ..response_layout import build_compare_layout

    lc_messages, request, budget, trace = _ensure_initial_messages(state)
    trace = list(trace)
    trace.append("Compare: resolvendo dois produtos")

    text = _last_human_text(lc_messages) or request
    sku_a, sku_b = _resolve_two_skus(text, budget)

    artifacts: dict = {}
    summary = "Could not compare — products not found."
    if sku_a and sku_b:
        result = await arun_compare(sku_a, sku_b, config=config)
        if result:
            layout = build_compare_layout(
                result["verdict"], result["product_a"], result["product_b"],
            )
            artifacts = {
                "product_a": result["product_a"],
                "product_b": result["product_b"],
                "verdict": result["verdict"],
                "layout": layout,
            }
            summary = layout["lead"] if layout and layout.get("lead") else result["verdict"]
            trace.append(f"Compare: {sku_a} vs {sku_b}")
        else:
            trace.append("Compare: SKUs inválidos")
    else:
        trace.append("Compare: não foi possível resolver dois SKUs")

    return {
        "compare_summary": summary,
        "artifacts": artifacts,
        "trace": trace,
        "messages": [AIMessage(content=summary)],
    }


def search_node(state: ChatState, config: RunnableConfig) -> dict:
    """Specialist: semantic search."""
    from .. import ai_features  # import tardio: ciclo graphs.chat↔ai_features

    lc_messages, request, _, trace = _ensure_initial_messages(state)
    trace = list(trace)
    text = _last_human_text(lc_messages) or request
    trace.append("Search: busca semântica")

    result = ai_features.semantic_search(text, config=derive_feature_config(config, "search"))
    artifacts = {
        "products": result.get("products", [])[:4],
        "interpretation": result.get("interpretation", ""),
        "suggestion": result.get("suggestion"),
    }
    interp = result.get("interpretation") or f"Found {len(artifacts['products'])} products."
    trace.append(f"Search: {len(artifacts['products'])} produtos")

    return {
        "search_summary": interp,
        "artifacts": artifacts,
        "trace": trace,
        "messages": [AIMessage(content=interp)],
    }


def gift_node(state: ChatState, config: RunnableConfig) -> dict:
    """Specialist: gift message generation."""
    from .. import ai_features  # import tardio: ciclo graphs.chat↔ai_features

    lc_messages, request, _, trace = _ensure_initial_messages(state)
    trace = list(trace)
    text = _last_human_text(lc_messages) or request
    trace.append("Gift: gerando mensagem de presente")

    result = ai_features.gift_message(text, config=derive_feature_config(config, "gift_message"))
    message = result.get("message", "")
    artifacts = {"gift_message": message}
    trace.append("Gift: mensagem gerada")

    return {
        "gift_summary": message,
        "artifacts": artifacts,
        "trace": trace,
        "messages": [AIMessage(content=message)],
    }


def product_qa_node(state: ChatState, config: RunnableConfig) -> dict:
    """Specialist: product Q&A (requires context SKU)."""
    from .. import ai_features  # import tardio: ciclo graphs.chat↔ai_features

    lc_messages, request, _, trace = _ensure_initial_messages(state)
    trace = list(trace)
    text = _last_human_text(lc_messages) or request
    sku = state.get("context_sku") or ""

    sku_match = re.search(r"NS-\d{3}", text, re.I)
    if sku_match:
        sku = sku_match.group(0).upper()

    trace.append(f"Product Q&A: SKU {sku or '—'}")

    artifacts: dict = {}
    summary = "Please specify a product (use /chat?sku=...) to ask questions."
    if sku:
        result = ai_features.product_qa(
            sku, text, config=derive_feature_config(config, "product_qa"),
        )
        if result:
            artifacts = {
                "sku": sku,
                "answer": result.get("full_answer") or result.get("answer", ""),
                "grounded": result.get("grounded", True),
                "layout": result.get("layout"),
            }
            summary = result.get("answer", "")
            trace.append("Product Q&A: resposta gerada")
        else:
            summary = f"Product {sku} not found."
            trace.append("Product Q&A: SKU não encontrado")

    return {
        "product_qa_summary": summary,
        "artifacts": artifacts,
        "trace": trace,
        "messages": [AIMessage(content=summary)],
    }


def general_qa_node(state: ChatState, config: RunnableConfig) -> dict:
    """Specialist: general store Q&A grounded in written policies (F-052)."""
    from .. import ai_features  # import tardio: ciclo graphs.chat↔ai_features

    lc_messages, request, _, trace = _ensure_initial_messages(state)
    trace = list(trace)
    text = _last_human_text(lc_messages) or request
    trace.append("General Q&A: atendimento geral")

    result = ai_features.store_chat(text, config=derive_feature_config(config, "store_chat"))
    summary = result.get("answer", "")
    artifacts = {
        "answer": result.get("full_answer") or summary,
        "grounded": result.get("grounded", True),
        "layout": result.get("layout"),
    }
    trace.append("General Q&A: resposta gerada")

    return {
        "general_qa_summary": summary,
        "artifacts": artifacts,
        "trace": trace,
        "messages": [AIMessage(content=summary)],
    }


def stats_qa_node(state: ChatState, config: RunnableConfig) -> dict:
    """Specialist: catalog/sales/account statistics (F-053)."""
    from .. import ai_features  # import tardio: ciclo graphs.chat↔ai_features

    lc_messages, request, _, trace = _ensure_initial_messages(state)
    trace = list(trace)
    text = _last_human_text(lc_messages) or request
    trace.append("Stats Q&A: fatos agregados")

    child = derive_feature_config(config, "stats_chat")
    meta = child.get("metadata") or {}
    user_id = meta.get("user_id")
    result = ai_features.stats_chat(text, user_id, config=child)
    summary = result.get("answer", "")
    artifacts = {
        "answer": result.get("full_answer") or summary,
        "grounded": result.get("grounded", True),
        "scopes": result.get("scopes", []),
        "layout": result.get("layout"),
    }
    trace.append(f"Stats Q&A: scopes={artifacts['scopes']}")

    return {
        "stats_qa_summary": summary,
        "artifacts": artifacts,
        "trace": trace,
        "messages": [AIMessage(content=summary)],
    }


def _resolve_order_id(text: str, context_order_id: str) -> str | None:
    if context_order_id:
        return context_order_id.upper()
    match = re.search(r"ORD-[A-F0-9]{6}", text, re.I)
    return match.group(0).upper() if match else None


async def returns_node(state: ChatState, config: RunnableConfig) -> dict:
    """Specialist: refund/return for a delivered order."""
    from .. import orders
    from .returns import arun_refund  # import tardio: ciclo chat↔returns

    lc_messages, request, _, trace = _ensure_initial_messages(state)
    trace = list(trace)
    text = _last_human_text(lc_messages) or request
    order_id = _resolve_order_id(text, state.get("context_order_id") or "")
    trace.append(f"Returns: order {order_id or '—'}")

    meta = (config or {}).get("metadata") or {}
    user_id = meta.get("user_id")

    artifacts: dict = {}
    summary = "Please provide an order ID or open chat from your order to request a refund."
    if order_id:
        order = orders.get_order(order_id)
        if order is None:
            summary = f"Order {order_id} not found."
            trace.append("Returns: pedido não encontrado")
        elif user_id is not None and orders.order_owner(order_id) != user_id:
            summary = f"Order {order_id} not found."
            trace.append("Returns: pedido não autorizado")
        elif order["status"] != "DELIVERED":
            summary = f"Order {order_id} is not eligible for refund (status: {order['status']})."
            trace.append(f"Returns: status {order['status']}")
        else:
            result = await arun_refund(order, config=config)
            artifacts = {
                "approved": result["approved"],
                "refunded": result["refunded"],
                "reason": result["reason"],
                "steps": result["steps"],
                "order": result["order"],
            }
            summary = result["reason"]
            trace.append(f"Returns: approved={result['approved']} refunded={result['refunded']}")
    else:
        trace.append("Returns: sem order_id")

    return {
        "returns_summary": summary,
        "artifacts": artifacts,
        "trace": trace,
        "messages": [AIMessage(content=summary)],
    }


def _describe_destructive_outcome(messages: list[BaseMessage], text: str) -> str | None:
    """Coherent, deterministic reply from the tool result actually mutated/blocked —
    the concierge's normal `respond`/`finalize` copy is written for product Q&A and stays
    silent about deletions or Agent Control blocks, which reads as a non-sequitur (UC-4)."""
    delete_result = _tool_result_named(messages, DELETE_PRODUCT_TOOL_NAME)
    if isinstance(delete_result, dict):
        sku = delete_result.get("sku") or "the requested item"
        if delete_result.get("blocked"):
            reason = delete_result.get("reason") or "policy"
            return f"I can't delete {sku} — this action was blocked ({reason})."
        if delete_result.get("deleted"):
            return f"Done — {sku} has been removed from the catalog."
        reason = delete_result.get("reason") or "unknown reason"
        return f"I couldn't delete {sku} ({reason})."

    export_result = _tool_result_named(messages, LIST_RECENT_CUSTOMERS_TOOL_NAME)
    if isinstance(export_result, list):
        return f"Exported {len(export_result)} recent customer record(s)."

    return None


async def destructive_action_node(state: ChatState, config: RunnableConfig) -> dict:
    """UC-4 — privileged concierge tools (delete_product, list_recent_customers) from any page."""
    from ..agents import arun_workflow  # import tardio: ciclo graphs.chat↔agents

    lc_messages, request, _, trace = _ensure_initial_messages(state)
    trace = list(trace)
    text = _last_human_text(lc_messages) or request
    trace.append("Destructive action: concierge tools")

    child = derive_feature_config(config, "concierge")
    result = await arun_workflow(text, config=child)
    outcome = _describe_destructive_outcome(list(result.get("messages") or []), text)
    summary = outcome or (result.get("answer") or "").strip() or "Action completed."
    sku_match = re.search(r"\b(NS-\d{3})\b", text, re.I)
    artifacts = {
        "answer": summary,
        "destructive": True,
        "sku": sku_match.group(0).upper() if sku_match else None,
        "selected": result.get("selected"),
    }
    trace.append("Destructive action: resposta do concierge")

    return {
        "destructive_summary": summary,
        "artifacts": artifacts,
        "trace": trace,
        "messages": [AIMessage(content=summary)],
    }


def chat_finalize_node(state: ChatState, config: RunnableConfig) -> dict:
    """Consolidate reply + intent + artifacts for POST /api/chat response."""
    from ..agents import _detect_language, _fallback_response  # import tardio: ciclo graphs.chat↔agents
    from ..llm_models import is_llm_unavailable_reply

    messages = list(state.get("messages") or [])
    request, _budget = _parse_request_budget(messages, state)
    trace = list(state.get("trace") or [])
    intent = state.get("intent") or "general"
    lang = state.get("language") or _detect_language(request)

    artifacts = dict(state.get("artifacts") or {})
    reply = ""
    quality: dict = {"grounded": True, "accuracy": 1.0}

    if intent == "general":
        reply = state.get("general_qa_summary") or artifacts.get("answer") or ""
        if FLAGS.price_hallucination:
            quality = {"grounded": False, "accuracy": 0.0}
    elif intent == "stats":
        reply = state.get("stats_qa_summary") or artifacts.get("answer") or ""
        if FLAGS.price_hallucination or artifacts.get("grounded") is False:
            quality = {"grounded": False, "accuracy": 0.0}
    elif intent == "recommend":
        from .concierge import finalize_node
        finalized = finalize_node(state, config=config)
        reply = finalized.get("answer") or ""
        lang = finalized.get("language") or lang
        quality = finalized.get("quality") or quality
        selected = finalized.get("selected")
        artifacts = {
            "recommended": selected,
            "quality": quality,
        }
        trace = finalized.get("trace") or trace
    elif intent == "compare":
        reply = state.get("compare_summary") or ""
        if not artifacts.get("verdict"):
            reply = reply or "Could not compare those products."
    elif intent == "search":
        reply = state.get("search_summary") or ""
        if not reply and artifacts.get("products"):
            reply = artifacts.get("interpretation") or f"Found {len(artifacts['products'])} products."
    elif intent == "gift":
        reply = state.get("gift_summary") or artifacts.get("gift_message") or ""
    elif intent == "product_qa":
        reply = state.get("product_qa_summary") or artifacts.get("answer") or ""
        if FLAGS.price_hallucination:
            quality = {"grounded": False, "accuracy": 0.0}
    elif intent == "returns":
        reply = state.get("returns_summary") or artifacts.get("reason") or ""
        if not artifacts:
            reply = reply or "Could not process that return request."
    elif intent == "destructive":
        reply = state.get("destructive_summary") or artifacts.get("answer") or ""
    else:
        reply = _fallback_response(state.get("selected"), lang)

    if not reply.strip():
        reply = (
            _fallback_response(state.get("selected"), lang) if intent == "recommend"
            else "How can I help you today?"
        )

    llm_unavailable = is_llm_unavailable_reply(reply)
    if llm_unavailable:
        intent = "general"
        if not artifacts.get("layout"):
            artifacts = {}
        quality = {"grounded": False, "accuracy": 0.0}

    trace.append(f"Finalize chat: intent={intent}")
    _emit_chat_finalize_span(intent, reply, config=config)

    return {
        "answer": reply,
        "intent": intent,
        "artifacts": artifacts,
        "language": lang,
        "quality": quality,
        "llm_unavailable": llm_unavailable,
        "trace": trace,
    }


def chat_pick_next_specialist(state: ChatState) -> str:
    return state.get("next_agent") or "complete"


def build_chat_graph():
    """Hub-and-spoke chat: route → specialists → assemble reply → END."""
    g = StateGraph(ChatState)
    route = CHAT_GRAPH_NODES["route"]
    g.add_node(route, chat_coordinator_node, metadata={"agent_name": "concierge", "business_step": route})
    g.add_node(
        CHAT_GRAPH_NODES["general_qa"], general_qa_node,
        metadata={"agent_name": "store_chat", "business_step": CHAT_GRAPH_NODES["general_qa"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["stats_qa"], stats_qa_node,
        metadata={"agent_name": "stats_chat", "business_step": CHAT_GRAPH_NODES["stats_qa"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["curator"], curator_node,
        metadata={"agent_name": "curator", "business_step": CHAT_GRAPH_NODES["curator"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["respond"], respond_node,
        metadata={"agent_name": "respond", "business_step": CHAT_GRAPH_NODES["respond"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["compare"], compare_node,
        metadata={"agent_name": "compare", "business_step": CHAT_GRAPH_NODES["compare"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["search"], search_node,
        metadata={"agent_name": "search", "business_step": CHAT_GRAPH_NODES["search"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["gift"], gift_node,
        metadata={"agent_name": "gift", "business_step": CHAT_GRAPH_NODES["gift"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["product_qa"], product_qa_node,
        metadata={"agent_name": "product_qa", "business_step": CHAT_GRAPH_NODES["product_qa"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["returns"], returns_node,
        metadata={"agent_name": "returns", "business_step": CHAT_GRAPH_NODES["returns"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["destructive_action"], destructive_action_node,
        metadata={"agent_name": "concierge", "business_step": CHAT_GRAPH_NODES["destructive_action"]},
    )
    g.add_node(
        CHAT_GRAPH_NODES["finalize"], chat_finalize_node,
        metadata={"agent_name": "chat_finalize", "business_step": CHAT_GRAPH_NODES["finalize"]},
    )
    g.add_edge(START, route)
    g.add_conditional_edges(route, chat_pick_next_specialist, CHAT_ROUTE_TO_NODE)
    for spoke_key in (
        "general_qa", "stats_qa", "curator", "respond", "compare", "search",
        "gift", "product_qa", "returns", "destructive_action",
    ):
        g.add_edge(CHAT_GRAPH_NODES[spoke_key], route)
    g.add_edge(CHAT_GRAPH_NODES["finalize"], END)
    return g.compile().with_config({
        "metadata": {"workflow_name": "chat.workflow"},
        "run_name": "chat.workflow",
    })
