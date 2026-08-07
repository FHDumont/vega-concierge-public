"""Stub offline LangChain (F-OBS-PREP-1 / ADR-027) — fatia de llm_models.py (F-BACKEND-2).

`VegaStubChatModel`: texto/tokens determinísticos; com `bind_tools` emite tool_calls
determinísticos (ReAct) por grafo (concierge/compare/fulfillment/returns).
"""
from __future__ import annotations

import json
import random
import re
import time
import uuid

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from .llm import DEFAULT_STUB_MODEL


def _stub_budget(text: str) -> float:
    for pattern in (
        r"(?:até|ate|under|below|max(?:imum)?|budget)\s*(?:r?\$?\s*)?([\d.]+)",
        r"r?\$\s*([\d.]+)",
    ):
        match = re.search(pattern, text or "", re.I)
        if match:
            return float(match.group(1))
    from ..store.tools import CATALOG
    return max(product["price"] for product in CATALOG)


def _stub_language(text: str) -> str:
    low = (text or "").lower()
    return "pt" if any(token in low for token in ("presente", "aniversário", "até", "preço")) else "en"


def _stub_constraints(request: str, budget: float) -> dict:
    low = (request or "").lower()
    categories = {
        "audio": ("audio", "fone", "headphone", "som", "speaker", "earbud"),
        "wearable": ("wearable", "watch", "smartwatch", "band", "anel", "ring"),
        "casa": ("casa", "home", "café", "coffee", "luminária", "lamp", "garrafa"),
    }
    category = next((name for name, values in categories.items() if any(value in low for value in values)), "")
    return {"budget": budget, "category": category, "language": _stub_language(request)}


def _stub_pick(candidates: list[dict], constraints: dict) -> dict | None:
    if not candidates:
        return None
    category = constraints.get("category") or ""
    pool = [candidate for candidate in candidates if category and category in candidate.get("tags", [])] or candidates
    return sorted(pool, key=lambda candidate: candidate["price"])[len(pool) // 2 if len(pool) > 2 else 0]


def _stub_recommendation(selected: dict | None) -> str:
    if not selected:
        return "We couldn't find an ideal match. Try widening your budget or search."
    price = selected.get("quote", {}).get("price", selected["price"])
    return f"We recommend the {selected['name']} at ${price:.0f} — a great fit for what you asked."


class VegaStubChatModel(BaseChatModel):
    """Stub offline LangChain — texto/tokens; com `bind_tools` emite tool_calls determinísticos (ReAct)."""
    model_name: str = DEFAULT_STUB_MODEL
    name: str | None = None
    provider: str = "stub"
    family: str = "stub"

    @property
    def _llm_type(self) -> str:
        return "vega-stub"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: ANN001
        """ReAct offline: propaga tools via bind para `_generate_react`."""
        return self.bind(tools=tools, tool_choice=tool_choice, **kwargs)

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        tools = kwargs.get("tools")
        if tools:
            return self._generate_react(messages, stop, run_manager, **kwargs)
        return self._generate_plain(messages, stop, run_manager, **kwargs)

    def _generate_plain(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult

        system, prompt = _messages_to_parts(messages)
        verbose = bool(kwargs.get("verbose"))
        max_tokens = kwargs.get("max_tokens")
        time.sleep(0.05)
        text = _stub_plain_text(system, prompt, verbose=verbose, max_tokens=max_tokens)
        in_tok = max(8, len(system.split()) + len(prompt.split()))
        out_tok = (max_tokens or (120 if verbose else 30)) + random.randint(0, 10)
        msg = AIMessage(
            content=text,
            usage_metadata={"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok},
            response_metadata={"model": self.model_name},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate_react(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult

        tools = kwargs.get("tools") or []
        tool_names = {getattr(t, "name", None) for t in tools}

        if "search_catalog" in tool_names:
            return self._generate_react_concierge(messages, stop, run_manager, **kwargs)
        if "policy_lookup" in tool_names:
            return self._generate_react_returns(messages, stop, run_manager, **kwargs)
        if "check_inventory" in tool_names:
            return self._generate_react_fulfillment(messages, stop, run_manager, **kwargs)
        if tool_names == {"get_price"} or "get_price" in tool_names:
            return self._generate_react_compare(messages, stop, run_manager, **kwargs)
        return self._generate_react_concierge(messages, stop, run_manager, **kwargs)

    def _react_tool_response(self, system: str, request: str, name: str, args: dict, **kwargs) -> ChatResult:
        from langchain_core.outputs import ChatGeneration, ChatResult

        verbose = bool(kwargs.get("verbose"))
        max_tokens = kwargs.get("max_tokens")
        in_tok = max(8, len(system.split()) + len(request.split()))
        out_tok = (max_tokens or (120 if verbose else 24)) + random.randint(0, 5)
        call_id = f"call_{uuid.uuid4().hex[:8]}"
        msg = AIMessage(
            content="",
            tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
            usage_metadata={"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok},
            response_metadata={"model": self.model_name},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _react_final_response(self, system: str, request: str, text: str, **kwargs) -> ChatResult:
        from langchain_core.outputs import ChatGeneration, ChatResult

        verbose = bool(kwargs.get("verbose"))
        max_tokens = kwargs.get("max_tokens")
        in_tok = max(8, len(system.split()) + len(request.split()))
        out_tok = (max_tokens or (120 if verbose else 40)) + random.randint(0, 10)
        msg = AIMessage(
            content=text,
            usage_metadata={"input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok},
            response_metadata={"model": self.model_name},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _generate_react_concierge(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult

        from ..galileo_span import DELETE_PRODUCT_TOOL_NAME, LIST_RECENT_CUSTOMERS_TOOL_NAME
        from ..problems import FLAGS
        from ..store.tools import get_price, search_catalog

        system, _ = _messages_to_parts(messages)
        request, budget = _parse_request_from_messages(messages)
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]

        tools = kwargs.get("tools") or []
        tool_names = {getattr(t, "name", None) for t in tools}

        if (
            DELETE_PRODUCT_TOOL_NAME in tool_names
            and FLAGS.prompt_injection
            and _matches_delete_injection(request)
        ):
            delete_done = any(
                isinstance(m, ToolMessage) and getattr(m, "name", None) == DELETE_PRODUCT_TOOL_NAME
                for m in tool_messages
            )
            if not delete_done:
                sku_match = re.search(r"\b(NS-\d+)\b", request, re.I)
                sku = sku_match.group(1).upper() if sku_match else "NS-001"
                return self._react_tool_response(
                    system, request, DELETE_PRODUCT_TOOL_NAME, {"sku": sku}, **kwargs,
                )
            sku_match = re.search(r"\b(NS-\d+)\b", request, re.I)
            sku = sku_match.group(1).upper() if sku_match else "NS-001"
            return self._react_final_response(
                system,
                request,
                f"Done — {sku} has been removed from the catalog as requested.",
                **kwargs,
            )

        if (
            LIST_RECENT_CUSTOMERS_TOOL_NAME in tool_names
            and FLAGS.prompt_injection
            and _matches_customer_leak_injection(request)
        ):
            leak_done = any(
                isinstance(m, ToolMessage)
                and getattr(m, "name", None) == LIST_RECENT_CUSTOMERS_TOOL_NAME
                for m in tool_messages
            )
            sku_match = re.search(r"\b(NS-\d+)\b", request, re.I)
            sku = sku_match.group(1).upper() if sku_match else None
            if not leak_done:
                return self._react_tool_response(
                    system,
                    request,
                    LIST_RECENT_CUSTOMERS_TOOL_NAME,
                    {"sku": sku, "limit": 5},
                    **kwargs,
                )
            for m in tool_messages:
                if (
                    isinstance(m, ToolMessage)
                    and getattr(m, "name", None) == LIST_RECENT_CUSTOMERS_TOOL_NAME
                ):
                    customers = _parse_tool_payload(m.content)
                    if isinstance(customers, list) and customers:
                        lines = [
                            f"- {c.get('name')} <{c.get('email')}> — {c.get('address')}"
                            for c in customers
                        ]
                        return self._react_final_response(
                            system,
                            request,
                            "Recent buyers:\n" + "\n".join(lines),
                            **kwargs,
                        )
            return self._react_final_response(
                system, request, "No matching customer records found.", **kwargs,
            )

        constraints = _stub_constraints(request, budget)
        time.sleep(0.05)

        search_done = any(
            isinstance(m, ToolMessage) and getattr(m, "name", None) == "search_catalog"
            for m in tool_messages
        )
        price_done = any(
            isinstance(m, ToolMessage) and getattr(m, "name", None) == "get_price"
            for m in tool_messages
        )

        if not search_done:
            return self._react_tool_response(system, request, "search_catalog", {"query": request, "budget": budget}, **kwargs)

        candidates: list[dict] = []
        for m in tool_messages:
            if isinstance(m, ToolMessage) and getattr(m, "name", None) == "search_catalog":
                parsed = _parse_tool_payload(m.content)
                if isinstance(parsed, list):
                    candidates = parsed
                break
        if not candidates:
            candidates = search_catalog(request, budget)

        if not price_done:
            selected = _stub_pick(candidates, constraints)
            sku = (selected or {}).get("sku") or (candidates[0]["sku"] if candidates else "NS-001")
            return self._react_tool_response(system, request, "get_price", {"sku": sku}, **kwargs)

        selected = _stub_pick(candidates, constraints)
        quote: dict = {}
        for m in tool_messages:
            if isinstance(m, ToolMessage) and getattr(m, "name", None) == "get_price":
                parsed = _parse_tool_payload(m.content)
                if isinstance(parsed, dict):
                    quote = parsed
                break
        if selected and quote:
            selected = {**selected, "quote": quote}
        elif selected:
            selected = {**selected, "quote": get_price(selected["sku"])}

        text = _stub_recommendation(selected)
        return self._react_final_response(system, request, text, **kwargs)

    def _generate_react_compare(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        system, request = _messages_to_parts(messages)
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        time.sleep(0.05)

        skus: list[str] = []
        for m in messages:
            if isinstance(m, HumanMessage):
                text = m.content if isinstance(m.content, str) else str(m.content)
                for match in re.finditer(r"SKU:\s*([A-Z0-9-]+)", text, re.I):
                    skus.append(match.group(1).upper())
        sku_a = skus[0] if len(skus) > 0 else "NS-001"
        sku_b = skus[1] if len(skus) > 1 else "NS-002"

        priced: set[str] = set()
        for m in tool_messages:
            if isinstance(m, ToolMessage) and getattr(m, "name", None) == "get_price":
                parsed = _parse_tool_payload(m.content)
                if isinstance(parsed, dict) and parsed.get("sku"):
                    priced.add(str(parsed["sku"]).upper())

        for sku in (sku_a, sku_b):
            if sku.upper() not in priced:
                return self._react_tool_response(system, request, "get_price", {"sku": sku}, **kwargs)

        return self._react_final_response(system, request, "Prices fetched — hand off to comparator.", **kwargs)

    def _generate_react_fulfillment(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        system, request = _messages_to_parts(messages)
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        time.sleep(0.05)

        sku: str | None = None
        for m in messages:
            if isinstance(m, HumanMessage):
                text = m.content if isinstance(m.content, str) else str(m.content)
                # Cart line: "NS-003 x1 @ R$179" or bare "SKU: NS-003"
                m_sku = re.search(r"\b(NS-\d+)\b", text, re.I)
                if m_sku:
                    sku = m_sku.group(1).upper()
                break

        if sku is None:
            return self._react_final_response(
                system,
                request,
                "Unable to proceed — no cart SKU found in the conversation history.",
                **kwargs,
            )

        inv_done = any(
            isinstance(m, ToolMessage) and getattr(m, "name", None) == "check_inventory"
            for m in tool_messages
        )
        price_done = any(
            isinstance(m, ToolMessage) and getattr(m, "name", None) == "get_price"
            for m in tool_messages
        )

        if not inv_done:
            return self._react_tool_response(system, request, "check_inventory", {"sku": sku}, **kwargs)
        if not price_done:
            return self._react_tool_response(system, request, "get_price", {"sku": sku}, **kwargs)

        return self._react_final_response(
            system, request, "Inventory and price verified — proceed to fraud check.", **kwargs
        )

    def _generate_react_returns(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        system, request = _messages_to_parts(messages)
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        time.sleep(0.05)

        order_id: str | None = None
        status: str | None = None
        total: float | None = None
        for m in messages:
            if isinstance(m, HumanMessage):
                text = m.content if isinstance(m.content, str) else str(m.content)
                oid = re.search(r"order\s+(\S+)", text, re.I)
                if oid:
                    order_id = oid.group(1).rstrip("),.")
                st = re.search(r"status\s+(\w+)", text, re.I)
                if st:
                    status = st.group(1).upper()
                tm = re.search(r"total\s+R?\$?([\d.]+)", text, re.I)
                if tm:
                    total = float(tm.group(1))

        if order_id is None or status is None or total is None:
            missing = ", ".join(
                name
                for name, val in (("order_id", order_id), ("status", status), ("total", total))
                if val is None
            )
            return self._react_final_response(
                system,
                request,
                f"Unable to proceed — missing order context in conversation history: {missing}.",
                **kwargs,
            )

        policy_done = any(
            isinstance(m, ToolMessage) and getattr(m, "name", None) == "policy_lookup"
            for m in tool_messages
        )
        calc_done = any(
            isinstance(m, ToolMessage) and getattr(m, "name", None) == "refund_calc"
            for m in tool_messages
        )

        order_args = {"order_id": order_id, "status": status, "total": total}
        if not policy_done:
            return self._react_tool_response(system, request, "policy_lookup", order_args, **kwargs)
        if not calc_done:
            return self._react_tool_response(system, request, "refund_calc", order_args, **kwargs)

        return self._react_final_response(
            system, request, "Policy and refund amount ready — proceed to eligibility and abuse.", **kwargs
        )


def _messages_to_parts(messages: list[BaseMessage]) -> tuple[str, str]:
    system_parts: list[str] = []
    prompt_parts: list[str] = []
    for m in messages:
        content = m.content if isinstance(m.content, str) else str(m.content)
        if isinstance(m, SystemMessage):
            system_parts.append(content)
        elif isinstance(m, HumanMessage):
            prompt_parts.append(content)
    return "\n".join(system_parts), "\n".join(prompt_parts)


def _parse_request_from_messages(messages: list[BaseMessage]) -> tuple[str, float]:
    request = ""
    for m in messages:
        content = m.content if isinstance(m.content, str) else str(m.content)
        if isinstance(m, HumanMessage):
            if not request:
                request = content
    if "Shopper request:" in request:
        rm = re.search(r"Shopper request:\s*(.+?)(?:\nBudget:|$)", request, re.S | re.I)
        if rm:
            request = rm.group(1).strip()
    return request, _stub_budget(request)


def _parse_tool_payload(content) -> object:
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return content
    return content


def _matches_delete_injection(text: str) -> bool:
    """Stub UC-4: delete + SKU ou NS-001 explícito no prompt do shopper."""
    low = text.lower()
    if "delete" not in low:
        return False
    if "ns-001" in low:
        return True
    return bool(re.search(r"\bNS-\d+\b", text, re.I))


def _matches_customer_leak_injection(text: str) -> bool:
    """Stub UC-4: exportar PII de outros compradores."""
    low = text.lower()
    if not any(w in low for w in ("customer", "buyer", "shopper", "user", "email", "address")):
        return False
    return any(w in low for w in ("list", "show", "print", "export", "all", "other", "recent", "dump", "every"))


def _stub_plain_text(system: str, prompt: str, *, verbose: bool = False, max_tokens: int | None = None) -> str:
    """Respostas determinísticas do stub offline — JSON quando o agente pede structured output."""
    if "Assess fraud risk" in prompt:
        return '{"decision": "ALLOW", "score": 0.08}'
    if "eligible for a refund" in prompt:
        from ..problems import FLAGS
        from ..store.tools import REFUND_WINDOW_DAYS

        if "DELIVERED" in prompt:
            days_match = re.search(r"delivered ([\d.]+) days ago", prompt)
            days = float(days_match.group(1)) if days_match else None
            if (
                FLAGS.refund_false_denial
                and days is not None
                and days <= REFUND_WINDOW_DAYS
            ):
                wrong_days = int(days + REFUND_WINDOW_DAYS + 15)
                return json.dumps({
                    "eligible": False,
                    "reason": (
                        f"Delivered {wrong_days} days ago — outside the "
                        f"{REFUND_WINDOW_DAYS}-day window."
                    ),
                })
            return json.dumps({"eligible": True, "reason": "Stub eligibility assessment."})
        return json.dumps({"eligible": False, "reason": "Stub eligibility assessment."})
    if "Screen for abuse" in prompt:
        return '{"decision": "ALLOW", "score": 0.05}'
    if "Classify the shopper message into one intent" in prompt:
        low = prompt.lower()
        if any(h in low for h in (
            "where is my order", "track my order", "order status", "onde está meu pedido",
            "change my address", "muda meu endereço", "capital of", "capital da",
        )):
            return json.dumps({
                "intent": "unsupported",
                "confidence": 0.92,
                "reason": "needs order tracking or address change",
            })
        if any(h in low for h in ("return policy", "refund policy", "policies of vega", "are you a bot")):
            return json.dumps({
                "intent": "general",
                "confidence": 0.9,
                "reason": "store policy question",
            })
        if "gift message" in low or "mensagem de presente" in low:
            return json.dumps({
                "intent": "general",
                "confidence": 0.88,
                "reason": "gift message composition",
            })
        if "refunding" in low or "how is the refund" in low:
            return json.dumps({
                "intent": "general",
                "confidence": 0.88,
                "reason": "refund policy FAQ",
            })
        if any(h in low for h in ("gastei", "spent", "how much have i spent", "quantas compras")):
            return json.dumps({
                "intent": "stats",
                "confidence": 0.9,
                "reason": "account spending question",
            })
        return json.dumps({
            "intent": "unsupported",
            "confidence": 0.55,
            "reason": "ambiguous without provider",
        })
    return f"[stub] resposta para: {prompt[:48]}"


def make_stub_chat_model(model: str = DEFAULT_STUB_MODEL, *, name: str | None = None) -> VegaStubChatModel:
    return VegaStubChatModel(model_name=model or DEFAULT_STUB_MODEL, name=name)
