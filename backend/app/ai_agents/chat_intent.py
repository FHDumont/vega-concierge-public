"""Deterministic chat intent heuristics — ported from graphs/chat_intent.py (F-BACKEND-2)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from langchain_core.messages import BaseMessage, HumanMessage
from pydantic import BaseModel, Field

from ..galileo_span import BUSINESS_STEPS, llm_run_name
from ..llm.agent_llm_invoke import invoke_feature_llm, is_stub_output
from ..problems import FLAGS


INTENT_CONFIDENCE_THRESHOLD = 0.7
CLASSIFIER_AGENT = "chat_intent_classifier"
CLASSIFIER_LLM_RUN_NAME = llm_run_name("feature", BUSINESS_STEPS[CLASSIFIER_AGENT])

_SUPPORTED_INTENTS = frozenset({
    "general", "stats", "recommend", "compare", "search", "product_qa", "returns", "destructive",
    "unsupported",
})

_UNSUPPORTED_HINTS = (
    "where is my order", "track my order", "order status", "track order", "order tracking",
    "onde está meu pedido", "onde esta meu pedido", "rastrear", "rastreio", "tracking number",
    "change my address", "change address", "update address", "delivery address",
    "muda meu endereço", "muda meu endereco", "alterar endereço", "alterar endereco",
    "change my cart", "update cart", "empty my cart", "clear cart",
    "capital of", "capital da", "capital do", "what is the weather", "what's the weather",
    "who is the president", "quem é o presidente",
)

_CLASSIFIER_SYSTEM = (
    "You classify shopper chat messages into exactly one intent for a Vega store concierge. "
    "Reply with raw JSON only — no markdown code fences."
)


class ChatIntentClassification(BaseModel):
    intent: str = Field(description="One of: general, stats, recommend, compare, search, product_qa, returns, unsupported.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in the chosen intent.")
    reason: str = Field(description="Short explanation for routing trace.")


@dataclass(frozen=True)
class IntentClassification:
    intent: str
    source: str
    confidence: float
    reason: str = ""


def last_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return message.content if isinstance(message.content, str) else str(message.content)
    return ""


def is_returns_action_intent(text: str, context_order_id: str | None) -> bool:
    """Transactional refund/return — not policy FAQ."""
    low = (text or "").lower()
    return_keywords = ("refund", "return", "devolução", "devolucao", "reembolso")
    has_return_topic = any(k in low for k in return_keywords)
    action_hints = (
        "i want", "please process", "process my", "request a refund", "request refund",
        "quero reembolso", "quero devolver", "get my money back", "money back",
        "please refund", "need a refund",
    )
    info_hints = (
        "how", "what", "what's", "what is", "policy", "window", "prazo", "quantos dias",
        "how many days", "can i return", "tell me about", "explain", "works",
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
    return bool(context_order_id)


def is_stats_question(text: str) -> bool:
    low = (text or "").lower()
    hints = (
        "most expensive", "most cheap", "cheapest", "expensive", "price range", "best seller",
        "best-selling", "bestseller", "most sold", "most popular", "how much spent",
        "how much have i spent", "total spent", "how many orders", "how many purchases",
        "purchase count", "my spending", "my orders", "most bought", "last order",
        "out of stock", "low stock", "mais caro", "mais barato", "mais vendido",
        "quanto gastei", "gastei", "quantas compras", "how many products",
    )
    return any(h in low for h in hints)


_CONTEXT_ITEM_RE = re.compile(
    r"\b(this|it|its|isso|este|esta|esse|essa|deste|desta|desse|dessa)\b", re.I,
)
_PRODUCT_INQUIRY_RE = re.compile(
    r"\b(tell me about|what is|what's|how is|how's|describe|info on|information on|details on)\b",
    re.I,
)


def is_store_policy_question(text: str) -> bool:
    low = (text or "").lower()
    hints = (
        "policy", "policies", "política", "politica", "políticas", "politicas",
        "rules", "rule", "terms", "privacy", "return window", "devolução", "devolucao",
        "refund policy", "shipping", "frete", "warranty", "garantia", "payment",
        "pagamento", "quantos dias", "how many days", "prazo", "who are you",
        "are you a bot", "what are your", "what is your", "how works", "how do returns",
        "how do refunds", "returns work", "refunds work",
    )
    if any(h in low for h in hints):
        return True
    if any(k in low for k in ("return", "refund", "reembolso", "devolv")):
        return any(h in low for h in ("how", "what", "explain", "policy", "window", "works", "work"))
    return False


def should_route_product_qa(text: str, context_sku: str | None) -> bool:
    if is_store_policy_question(text):
        return False
    msg = text or ""
    if re.search(r"NS-\d{3}", msg, re.I):
        return True
    if not context_sku:
        return False
    if _CONTEXT_ITEM_RE.search(msg):
        return True
    if _PRODUCT_INQUIRY_RE.search(msg):
        return True
    return "?" in msg


def is_catalog_price_question(text: str) -> bool:
    """Price/cost question about a SKU in the message (UC-1 chat without ``?``)."""
    low = (text or "").lower()
    if not re.search(r"NS-\d{3}", text or "", re.I):
        return False
    return any(h in low for h in ("price", "cost", "how much", "how many"))


def is_context_item_question(text: str, context_sku: str | None) -> bool:
    return bool(context_sku and _CONTEXT_ITEM_RE.search(text or ""))


def is_shopping_intent(text: str) -> bool:
    low = (text or "").lower()
    hints = (
        "gift under", "gift for", "present for", "presente para", "recommend", "recomend",
        "looking for", "procurando", "under $", "budget", "birthday", "buy a", "comprar",
        "need a", "preciso de", "show me", "something for", "coffee lover", "for travel",
    )
    if any(h in low for h in hints):
        return True
    if re.search(r"\b(under|below)\s+(r\$|us\$|\$)?\s*\d", low):
        return True
    return bool(re.search(r"\bsomething\b.*\bfor\b", low))


from ..prompt_injection import (
    is_destructive_action_intent,
    is_injection_discount_request,
)


def is_prompt_injection_product_qa(text: str, context_sku: str | None) -> bool:
    """UC-4 — discount/override prompts routed to product Q&A when override + price cues present."""
    if not FLAGS.prompt_injection:
        return False
    return is_injection_discount_request(text)


def _is_gift_message_composition_request(text: str) -> bool:
    """Checkout gift-message UI removed — compose requests stay in general policy chat."""
    low = (text or "").lower()
    return any(h in low for h in (
        "gift message", "write a gift", "mensagem de presente",
        "mensagem para presente", "gift card message",
    )) or bool(re.search(r"\b(write|compose|create|help me write)\b.*\bmessage\b", low))


def is_unsupported_question(text: str) -> bool:
    low = (text or "").lower()
    return any(h in low for h in _UNSUPPORTED_HINTS)


def _heuristic_conflicts(text: str, context_sku: str | None) -> bool:
    stats = is_stats_question(text) and not is_context_item_question(text, context_sku)
    product = should_route_product_qa(text, context_sku)
    return stats and product


def detect_chat_intent(text: str, context_sku: str | None, context_order_id: str | None = None) -> str:
    if not FLAGS.prompt_injection and is_injection_discount_request(text):
        return "product_qa"
    if not FLAGS.prompt_injection and is_destructive_action_intent(text, context_sku):
        low = (text or "").lower()
        if re.search(r"NS-\d{3}", text or "", re.I):
            return "product_qa"
        return "destructive_action"
    if FLAGS.prompt_injection and is_destructive_action_intent(text, context_sku):
        return "destructive"
    if FLAGS.prompt_injection and is_prompt_injection_product_qa(text, context_sku):
        return "product_qa"
    low = (text or "").lower()
    if _is_gift_message_composition_request(text):
        return "general"
    if "compare" in low or "comparar" in low:
        return "compare"
    if "search" in low or "buscar" in low:
        return "search"
    if is_stats_question(text) and not is_context_item_question(text, context_sku):
        return "stats"
    if not is_store_policy_question(text):
        if should_route_product_qa(text, context_sku):
            return "product_qa"
        if is_catalog_price_question(text):
            return "product_qa"
        if re.search(r"NS-\d{3}", text or "", re.I) and "?" in text:
            return "product_qa"
    if is_returns_action_intent(text, context_order_id):
        return "returns"
    if is_shopping_intent(text):
        return "recommend"
    return "general"


def _parse_classifier_json(text: str) -> ChatIntentClassification | None:
    raw = (text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        payload = json.loads(raw[start:end + 1])
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return ChatIntentClassification.model_validate(payload)
    except Exception:  # noqa: BLE001
        return None


def _fallback_classifier(text: str, heuristic: str) -> IntentClassification:
    if is_unsupported_question(text):
        return IntentClassification(
            intent="unsupported",
            source="fallback",
            confidence=0.85,
            reason="unsupported keyword",
        )
    if heuristic != "general":
        return IntentClassification(
            intent=heuristic,
            source="fallback",
            confidence=0.8,
            reason="heuristic after stub",
        )
    if _is_gift_message_composition_request(text) or is_store_policy_question(text):
        return IntentClassification(
            intent="general",
            source="fallback",
            confidence=0.75,
            reason="policy or gift-message keywords",
        )
    if "?" in text and (
        _PRODUCT_INQUIRY_RE.search(text)
        or _CONTEXT_ITEM_RE.search(text)
        or re.search(r"NS-\d{3}", text or "", re.I)
    ):
        return IntentClassification(
            intent="product_qa",
            source="fallback",
            confidence=0.7,
            reason="product question without page context",
        )
    if is_catalog_price_question(text):
        return IntentClassification(
            intent="product_qa",
            source="fallback",
            confidence=0.75,
            reason="catalog price question with SKU",
        )
    return IntentClassification(
        intent="unsupported",
        source="fallback",
        confidence=0.6,
        reason="no clear match",
    )


def _classifier_prompt(text: str, context_sku: str | None, context_order_id: str | None) -> str:
    context_bits: list[str] = []
    if context_sku:
        context_bits.append(f"product page SKU: {context_sku}")
    if context_order_id:
        context_bits.append(f"order context: {context_order_id}")
    context_line = "\n".join(context_bits) or "none"
    return (
        "Classify the shopper message into one intent:\n"
        "- general: store policies, returns/shipping/warranty FAQ, who are you\n"
        "- stats: catalog prices, bestsellers, personal spending/order count (needs sign-in for account)\n"
        "- recommend: gift or product shopping help\n"
        "- compare: compare two products\n"
        "- search: find products in catalog\n"
        "- product_qa: question about a specific product (needs SKU in message or product page context)\n"
        "- returns: transactional refund/return request (not policy FAQ)\n"
        "- unsupported: order tracking, cart/checkout changes, address edits, off-topic, anything else\n\n"
        "If the question needs order tracking, cart changes, address edits, or is off-topic, "
        "return unsupported.\n\n"
        f"Page context: {context_line}\n"
        f"Shopper message: {text}\n\n"
        'Return JSON: {"intent":"...", "confidence":0.0-1.0, "reason":"..."}'
    )


def _normalize_classifier_result(
    parsed: ChatIntentClassification,
    text: str,
    heuristic: str,
) -> IntentClassification:
    intent = parsed.intent.strip().lower()
    if intent not in _SUPPORTED_INTENTS:
        intent = "unsupported"
    confidence = float(parsed.confidence)
    if intent == "unsupported" or confidence < INTENT_CONFIDENCE_THRESHOLD:
        fallback = _fallback_classifier(text, heuristic)
        if fallback.intent != "unsupported":
            return fallback
        return IntentClassification(
            intent="unsupported",
            source="llm",
            confidence=confidence,
            reason=parsed.reason or "low confidence",
        )
    return IntentClassification(
        intent=intent,
        source="llm",
        confidence=confidence,
        reason=parsed.reason,
    )


def _resolve_product_sku(text: str, context_sku: str | None) -> str | None:
    match = re.search(r"NS-\d{3}", text or "", re.I)
    if match:
        return match.group(0).upper()
    sku = (context_sku or "").strip().upper()
    return sku or None


def apply_intent_guards(
    intent: str,
    text: str,
    *,
    context_sku: str | None,
    context_order_id: str | None,
) -> str:
    if intent == "product_qa" and not _resolve_product_sku(text, context_sku):
        if is_injection_discount_request(text):
            return intent
        if FLAGS.prompt_injection and is_prompt_injection_product_qa(text, context_sku):
            return intent
        return "unsupported"
    if intent == "returns" and not context_order_id and not is_returns_action_intent(text, context_order_id):
        return "unsupported"
    return intent


def classify_chat_intent_hybrid(
    text: str,
    context_sku: str | None,
    context_order_id: str | None = None,
    *,
    config=None,
) -> IntentClassification:
    heuristic = detect_chat_intent(text, context_sku, context_order_id)
    if heuristic != "general" and not _heuristic_conflicts(text, context_sku):
        result = IntentClassification(
            intent=heuristic,
            source="heuristic",
            confidence=1.0,
            reason="keyword match",
        )
    else:
        llm_result = invoke_feature_llm(
            CLASSIFIER_AGENT,
            _CLASSIFIER_SYSTEM,
            _classifier_prompt(text, context_sku, context_order_id),
            run_name=CLASSIFIER_LLM_RUN_NAME,
            max_tokens=80,
            config=config,
        )
        parsed = _parse_classifier_json(llm_result.text)
        if parsed is None or is_stub_output(llm_result.text):
            result = _fallback_classifier(text, heuristic)
        else:
            result = _normalize_classifier_result(parsed, text, heuristic)

    guarded = apply_intent_guards(
        result.intent,
        text,
        context_sku=context_sku,
        context_order_id=context_order_id,
    )
    if guarded != result.intent:
        return IntentClassification(
            intent=guarded,
            source=result.source,
            confidence=result.confidence,
            reason=f"guard: missing prerequisite (was {result.intent})",
        )
    return result
