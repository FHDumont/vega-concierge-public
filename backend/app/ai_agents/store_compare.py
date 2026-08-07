"""Standalone comparison workflow for the store comparison endpoint."""
from __future__ import annotations

from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig

from ..chat_layout import build_compare_layout
from ..llm.agent_llm_invoke import LLMResult, is_stub_output
from ..product_retrieval import retrieve_catalog_excerpts
from ..store.catalog_format import _availability, _usd
from ..store.langchain_tools import get_price_tool
from ..store.tools import CATALOG

COMPARATOR_CONTROL_STEP_NAME = "comparator"
COMPARATOR_LLM_RUN_NAME = "feature.write_comparison_verdict"
GATHER_RUN_NAME = "compare.gather_product_context"
CATALOG_RETRIEVE_RUN_NAME = "compare.retrieve_catalog_context"
FETCH_PRICES_RUN_NAME = "compare.fetch_prices_for_comparison"
COMPOSE_RUN_NAME = "compare.compose_shopper_verdict"
WORKFLOW_RUN_NAME = "compare.workflow"
_COMPARATOR_SYSTEM_PROMPT = (
    "You compare two store products using only the supplied names, prices, tags, descriptions, "
    "and catalog excerpts. Give a concise shopper-facing recommendation in English with no markdown."
)


def find_product(sku: str) -> dict | None:
    return next((product for product in CATALOG if product["sku"] == sku), None)


def is_unavailable(result: LLMResult) -> bool:
    return result.system in {"stub", "error"} or is_stub_output(result.text)


def invoke_local_llm(
    agent_name: str, system: str, prompt: str, *, run_name: str, max_tokens: int, config=None,
) -> LLMResult:
    """Single-provider invoke — one LLM span per comparison (cascade retries stay off the trace)."""
    from ..llm.llm_models import get_chat_model, invoke_to_llm_result

    try:
        model = get_chat_model(agent_name)
        return invoke_to_llm_result(
            model, system, prompt, run_name=run_name, max_tokens=max_tokens, config=config,
        )
    except Exception:  # noqa: BLE001 — comparator falls back to deterministic verdict text
        return LLMResult("", 0, 0, "error", system="error")


def _product_context(product: dict) -> str:
    return (
        f"Product: {product['name']} (SKU {product['sku']})\n"
        f"List price: {_usd(product['price'])}\n"
        f"Description: {product['description']}\n"
        f"Tags: {', '.join(product['tags'])}\n"
        f"Availability: {_availability(product)}"
    )


def _comparison_question(a: dict, b: dict) -> str:
    return f"compare {a['name']} {a['sku']} vs {b['name']} {b['sku']}"


def _fallback_verdict(a: dict, b: dict, price_a: float, price_b: float) -> str:
    cheaper, premium = (a, b) if price_a <= price_b else (b, a)
    return (
        f"Both are solid picks. The {cheaper['name']} ({_usd(min(price_a, price_b))}) is the more "
        f"budget-friendly choice, while the {premium['name']} ({_usd(max(price_a, price_b))}) leans more "
        "premium — pick by whether price or features matter most to you."
    )


def _gather_product_context(state: dict, config: RunnableConfig) -> dict:
    del config
    a, b = state["product_a"], state["product_b"]
    return {
        **state,
        "comparison_question": _comparison_question(a, b),
        "product_context_a": _product_context(a),
        "product_context_b": _product_context(b),
    }


def _retrieve_catalog_context(state: dict, config: RunnableConfig) -> dict:
    question = state["comparison_question"]
    a, b = state["product_a"], state["product_b"]
    return {
        **state,
        "catalog_context_a": retrieve_catalog_excerpts(a, question, config=config),
        "catalog_context_b": retrieve_catalog_excerpts(b, question, config=config),
    }


def _fetch_prices(state: dict, config: RunnableConfig) -> dict:
    a, b = state["product_a"], state["product_b"]
    quote_a = get_price_tool.invoke({"sku": a["sku"]}, config=config)
    quote_b = get_price_tool.invoke({"sku": b["sku"]}, config=config)
    return {
        **state,
        "price_a": (quote_a or {}).get("price", a["price"]),
        "price_b": (quote_b or {}).get("price", b["price"]),
    }


def _compose_verdict(state: dict, config: RunnableConfig) -> dict:
    a, b = state["product_a"], state["product_b"]
    price_a, price_b = state["price_a"], state["price_b"]
    catalog_a = (state.get("catalog_context_a") or "").strip()
    catalog_b = (state.get("catalog_context_b") or "").strip()
    prompt = (
        f"{state['product_context_a']}\n"
        f"{catalog_a}\n\n"
        f"{state['product_context_b']}\n"
        f"{catalog_b}\n\n"
        f"Live prices — Product A: {_usd(price_a)}; Product B: {_usd(price_b)}\n\n"
        "Compare these two products for a shopper in 2-3 short sentences (one idea per sentence): "
        "who each is best for and which to pick."
    )
    result = invoke_local_llm(
        COMPARATOR_CONTROL_STEP_NAME,
        _COMPARATOR_SYSTEM_PROMPT,
        prompt,
        run_name=COMPARATOR_LLM_RUN_NAME,
        max_tokens=200,
        config=config,
    )
    verdict = _fallback_verdict(a, b, price_a, price_b) if is_unavailable(result) else result.text.strip()
    product_a = {**a, "price": price_a}
    product_b = {**b, "price": price_b}
    layout = build_compare_layout(verdict, product_a, product_b)
    return {
        "product_a": product_a,
        "product_b": product_b,
        "verdict": verdict,
        "layout": layout,
    }


def _named_step(fn, run_name: str) -> RunnableLambda:
    return RunnableLambda(fn, name=run_name).with_config({"run_name": run_name})


def compare_products(sku_a: str, sku_b: str, *, config=None) -> dict | None:
    """Run the full comparison under a single observable workflow root."""
    a, b = find_product(sku_a), find_product(sku_b)
    if a is None or b is None:
        return None
    gather = _named_step(_gather_product_context, GATHER_RUN_NAME)
    retrieve = _named_step(_retrieve_catalog_context, CATALOG_RETRIEVE_RUN_NAME)
    fetch = _named_step(_fetch_prices, FETCH_PRICES_RUN_NAME)
    compose = _named_step(_compose_verdict, COMPOSE_RUN_NAME)
    workflow = (gather | retrieve | fetch | compose).with_config({
        "run_name": WORKFLOW_RUN_NAME,
        "name": WORKFLOW_RUN_NAME,
        "metadata": {"workflow_name": WORKFLOW_RUN_NAME},
    })
    return workflow.invoke({"product_a": a, "product_b": b}, config=config)


async def arun_compare(sku_a: str, sku_b: str, *, config=None) -> dict | None:
    return compare_products(sku_a, sku_b, config=config)


store_compare = compare_products
