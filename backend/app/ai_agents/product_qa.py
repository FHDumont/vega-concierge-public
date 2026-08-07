"""Standalone UC-1 product-question workflow (`feature.answer_product_question`)."""
from __future__ import annotations

import contextvars
import csv
import time
from pathlib import Path
from typing import Callable

from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig

from ..llm.agent_llm_invoke import LLMResult, invoke_feature_llm, is_stub_output
from ..obs import galileo_obs
from ..problems import FLAGS
from ..store.catalog_format import _availability, _usd
from ..store.langchain_tools import search_policies_tool
from ..store.tools import CATALOG
from ..chat_layout import build_product_qa_layout, is_product_overview_question, shopper_reply_from_layout
from ..product_retrieval import retrieve_catalog_excerpts

CONTROL_STEP_NAME = "product_qa"
LLM_RUN_NAME = "feature.answer_product_question"
GATHER_RUN_NAME = "product_qa.gather_product_context"
POLICY_RETRIEVE_RUN_NAME = "product_qa.retrieve_policy_context"
CATALOG_RETRIEVE_RUN_NAME = "product_qa.retrieve_catalog_context"
COMPOSE_RUN_NAME = "product_qa.compose_product_answer"
WORKFLOW_RUN_NAME = "product_qa.workflow"
PRODUCTS_QA_CSV = Path(__file__).resolve().parents[2] / "data" / "catalog" / "products_qa.csv"
_OFF_TOPIC_REDIRECT = (
    "I can only answer questions about this product here. "
    "For store policies, orders, or shopping help, use the concierge chat."
)

_SYSTEM_PROMPT = (
    "You answer customer questions about a single store product, grounded strictly in the product "
    "data provided. If something isn't in the data, say you don't have that detail. Be concise. "
    "Reply in English."
)
_invoke_fn_var: contextvars.ContextVar[Callable[[str], tuple[LLMResult, str]] | None] = (
    contextvars.ContextVar("product_qa_invoke", default=None)
)
_result_sink_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "product_qa_result_sink", default=None,
)
_control_handler = None


def _is_unavailable_reply(text: str) -> bool:
    return is_stub_output(text) or (text or "").strip().startswith(("The AI provider", "The AI assistant"))


def _find_product(sku: str) -> dict | None:
    return next((product for product in CATALOG if product["sku"] == sku), None)


def _load_product_specs() -> list[dict[str, str]]:
    if not PRODUCTS_QA_CSV.is_file():
        return []
    with PRODUCTS_QA_CSV.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _is_overview_question(question: str) -> bool:
    return is_product_overview_question(question)


def _product_context(product: dict) -> str:
    base = (
        f"Product: {product['name']}\n"
        f"Price: {_usd(product['price'])}\n"
        f"Description: {product['description']}\n"
        f"Tags: {', '.join(product['tags'])}\n"
        f"Availability: {_availability(product)}"
    )
    details = next((row for row in _load_product_specs() if row["sku"] == product["sku"]), None)
    return f"{base}\nTechnical specifications:\n{details['answer']}" if details and details.get("answer") else base


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


def _is_off_topic_product_question(question: str) -> bool:
    """General store/chat questions belong in the concierge, not the PDP assistant."""
    q = (question or "").lower().strip()
    if not q:
        return False
    if q in {"hi", "hello", "hey", "thanks", "thank you"}:
        return True
    if any(m in q for m in ("who are you", "are you a bot", "what are you")):
        return True
    if any(m in q for m in ("my order", "where is my package", "track my order", "order status")):
        return True
    if any(m in q for m in ("recommend", "curate picks", "gift under", "birthday gift under")):
        return True
    if "store polic" in q or "your polic" in q or "vega polic" in q:
        return True
    if any(phrase in q for phrase in (
        "how do returns work", "how does return work", "how do refunds work",
        "return policy", "refund policy", "shipping policy", "how does shipping work",
        "payment policy", "how do i pay",
    )):
        return True
    product_anchor = any(w in q for w in ("this product", "this item", " this ", " it "))
    if not product_anchor and any(w in q for w in ("return", "refund")) and "work" in q:
        return True
    return False


def _fallback(product: dict, question: str, grounded: bool) -> str:
    if not grounded:
        return "Absolutely — it's on a special deal at just $9.90 today and ships worldwide instantly."
    question = question.lower()
    if any(word in question for word in ("price", "cost", "how much", "expensive")):
        return f"The {product['name']} is priced at {_usd(product['price'])}."
    if any(word in question for word in ("stock", "available", "availability", "in stock")):
        return f"The {product['name']} is currently {_availability(product)}."
    return f"{product['name']}: {product['description']}"


def _is_stub(result) -> bool:
    return getattr(result, "system", None) == "stub" or _is_unavailable_reply(result.text)


def _invoke_llm(prompt: str, system: str, *, max_tokens: int, config=None) -> tuple[LLMResult, str]:
    """Own the provider cascade and the stable UC-1 LLM span name."""
    return invoke_feature_llm(
        "product_qa",
        system,
        prompt,
        run_name=LLM_RUN_NAME,
        max_tokens=max_tokens,
        config=config,
    ), "miss"


def _control_is_active() -> bool:
    if not galileo_obs.is_enabled():
        return False
    try:
        import agent_control  # noqa: F401
    except ImportError:
        return False
    return True


def _registered_control_handler():
    """Register this agent's UC-1 pre-call step without using shared control wrappers."""
    global _control_handler
    if _control_handler is not None:
        return _control_handler
    from agent_control import control

    @control(step_name=CONTROL_STEP_NAME)
    def controlled(prompt: str) -> str:
        invoke = _invoke_fn_var.get()
        if invoke is None:
            raise RuntimeError("missing product QA invoke function")
        result = invoke(prompt)
        sink = _result_sink_var.get()
        if sink is not None:
            sink["result"] = result
        return result[0].text

    _control_handler = controlled
    return controlled


def _build_layout(product: dict, answer: str, question: str) -> dict | None:
    return build_product_qa_layout(product, answer, question=question)


def _controlled_invoke(
    prompt: str, invoke: Callable[[str], tuple[LLMResult, str]],
) -> tuple[str, LLMResult, str]:
    if not _control_is_active():
        result, status = invoke(prompt)
        return result.text, result, status
    try:
        handler = _registered_control_handler()
    except Exception:  # noqa: BLE001 - control is opt-in and must not break UC-1
        result, status = invoke(prompt)
        return result.text, result, status
    sink: dict = {}
    invoke_token = _invoke_fn_var.set(invoke)
    sink_token = _result_sink_var.set(sink)
    try:
        text = handler(prompt)
        result, status = sink.get("result") or invoke(prompt)
        return text, result, status
    except Exception as exc:  # Agent Control uses a provider-specific violation exception.
        if type(exc).__name__ == "ControlViolationError":
            text = (
                "I can only help with product details from our catalog. "
                "Please ask about this item's features, price, or availability."
            )
            return text, LLMResult(text, 0, 0, "control-block", system="control"), "control_block"
        raise
    finally:
        _invoke_fn_var.reset(invoke_token)
        _result_sink_var.reset(sink_token)


def _gather_product_context(state: dict, config: RunnableConfig) -> dict:
    del config
    product = state["product"]
    return {**state, "product_context": _product_context(product)}


def _retrieve_policy_context(state: dict, config: RunnableConfig) -> dict:
    if not state.get("grounded"):
        return {**state, "policy_context": ""}
    retrieval = search_policies_tool.invoke({"question": state["question"]}, config=config)
    chunks = retrieval.get("chunks") or []
    return {**state, "policy_context": _format_policy_chunks(chunks)}


def _retrieve_catalog_context(state: dict, config: RunnableConfig) -> dict:
    if not state.get("grounded"):
        return {**state, "catalog_context": ""}
    product = state["product"]
    question = state["question"]
    catalog_context = retrieve_catalog_excerpts(product, question, config=config)
    return {**state, "catalog_context": catalog_context}


def _compose_product_answer(state: dict, config: RunnableConfig) -> dict:
    product = state["product"]
    question = state["question"]
    grounded = state["grounded"]
    overview = _is_overview_question(question)
    static_parts = [
        state.get("product_context") or "",
        state.get("catalog_context") or "",
        state.get("policy_context") or "",
    ]
    static_context = "\n\n".join(part for part in static_parts if part).strip()
    if grounded:
        suffix = (
            "The customer wants an overview of this product. Answer in 2-3 sentences: what it is, "
            "who it's for, and 2-3 standout specs from the technical specifications. Use ONLY the "
            "provided data. Reply in English. No markdown."
            if overview else
            "Answer using ONLY the product information and store policy above. If it isn't covered, "
            "say you don't have that detail. Be concise (1-2 sentences). Reply in English. No markdown."
        )
    else:
        suffix = (
            f'Product: "{product["name"]}". You have no catalog or policy data for it. Answer confidently '
            "with specific figures anyway — never say you lack a detail and never tell the customer to "
            "check elsewhere. Be concise (1-2 sentences). Reply in English. No markdown."
        )
        static_context = ""

    system = f"{_SYSTEM_PROMPT}\n\n{static_context}\n\n{suffix}".strip()

    def invoke(current_prompt: str = question):
        return _invoke_llm(
            current_prompt, system, max_tokens=220 if overview and grounded else 160, config=config,
        )

    text, result, _status = _controlled_invoke(question, invoke)
    if _is_stub(result):
        text = _fallback(product, question, grounded)
    answer = text.strip()
    layout = _build_layout(product, answer, question)
    display = shopper_reply_from_layout(layout, answer)
    return {
        "answer": display,
        "grounded": grounded,
        "layout": layout,
    }


def answer_product_question(sku: str, question: str, *, config=None) -> dict | None:
    """Run UC-1 with locally-owned prompts, RAG assembly, naming, and control registration."""
    product = _find_product(sku)
    if product is None:
        return None

    question = (question or "").strip() or "Tell me about this product."
    if _is_off_topic_product_question(question):
        return {"answer": _OFF_TOPIC_REDIRECT, "grounded": True, "layout": None}

    grounded = not FLAGS.price_hallucination
    if FLAGS.latency_spike:
        time.sleep(1.2)

    def _named_step(fn, run_name: str) -> RunnableLambda:
        return RunnableLambda(fn, name=run_name).with_config({"run_name": run_name})

    gather = _named_step(_gather_product_context, GATHER_RUN_NAME)
    policy = _named_step(_retrieve_policy_context, POLICY_RETRIEVE_RUN_NAME)
    catalog = _named_step(_retrieve_catalog_context, CATALOG_RETRIEVE_RUN_NAME)
    compose = _named_step(_compose_product_answer, COMPOSE_RUN_NAME)
    workflow = (gather | policy | catalog | compose).with_config({
        "run_name": WORKFLOW_RUN_NAME,
        "name": WORKFLOW_RUN_NAME,
        "metadata": {"workflow_name": WORKFLOW_RUN_NAME},
    })
    return workflow.invoke(
        {"product": product, "question": question, "grounded": grounded},
        config=config,
    )


product_qa = answer_product_question
