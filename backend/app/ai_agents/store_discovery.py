"""Standalone search and recommendation agents for store-only endpoints."""
from __future__ import annotations

import json
import time

from ..llm.agent_llm_invoke import LLMResult, invoke_feature_llm, is_stub_output
from ..problems import FLAGS
from ..store.tools import CATALOG

SEARCH_CONTROL_STEP_NAME = "search"
SEARCH_LLM_RUN_NAME = "feature.semantic_product_search"
CART_CROSSSELL_CONTROL_STEP_NAME = "cart_crosssell"
CART_CROSSSELL_LLM_RUN_NAME = "feature.suggest_cart_additions"

_SEARCH_SYSTEM_PROMPT = (
    "You map a shopper query to store catalog SKUs. Return only the requested JSON and never "
    "invent products outside the supplied catalog."
)
_RECOMMENDATION_SYSTEM_PROMPT = (
    "You recommend store catalog products. Return only the requested JSON and only use supplied SKUs."
)
_JSON_ONLY = " Reply with raw JSON only — no markdown code fences."
CROSSSELL_N = 3


def find_product(sku: str) -> dict | None:
    return next((product for product in CATALOG if product["sku"] == sku), None)


def catalog_index(products: list[dict] | None = None) -> str:
    return "\n".join(
        f"{product['sku']}: {product['name']} [{', '.join(product['tags'])}] ${product['price']:.2f}"
        for product in (products or CATALOG)
    )


def stable_skus(skus: list[str] | None) -> list[str]:
    return sorted({sku for sku in skus or [] if isinstance(sku, str) and sku})


def parse_json(text: str) -> dict | None:
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = json.loads(text[start:end + 1])
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def maybe_latency() -> None:
    if FLAGS.latency_spike:
        time.sleep(1.2)


def is_unavailable(result: LLMResult) -> bool:
    return result.system in {"stub", "error"} or is_stub_output(result.text)


def invoke_local_llm(
    agent_name: str, system: str, prompt: str, *, run_name: str, max_tokens: int, config=None,
) -> LLMResult:
    """Run this discovery agent's provider cascade with LangChain callbacks."""
    return invoke_feature_llm(
        agent_name, system, prompt, run_name=run_name, max_tokens=max_tokens, config=config,
    )


def _keyword_search(query: str) -> list[dict]:
    tokens = [token for token in query.lower().split() if len(token) > 2]
    if not tokens:
        return []
    return [
        product for product in CATALOG
        if any(token in f"{product['name']} {' '.join(product['tags'])} {product['description']}".lower()
               for token in tokens)
    ]


def _resolve_skus(skus, limit: int, exclude: set[str] | None = None) -> list[dict]:
    products, seen = [], set()
    for sku in skus or []:
        if not isinstance(sku, str) or sku in seen or sku in (exclude or set()):
            continue
        product = find_product(sku)
        if product:
            products.append(product)
            seen.add(sku)
        if len(products) >= limit:
            break
    return products


def _related_by_tags(seed_skus: list[str], limit: int, exclude: set[str]) -> list[dict]:
    seed_tags = {tag for sku in seed_skus if (product := find_product(sku)) for tag in product["tags"]}
    pool = [product for product in CATALOG if product["sku"] not in exclude and product["stock"] > 0]
    if seed_tags:
        related = sorted(pool, key=lambda product: len(seed_tags & set(product["tags"])), reverse=True)
        related = [product for product in related if seed_tags & set(product["tags"])]
        if related:
            return related[:limit]
    return pool[:limit]


def semantic_search(query: str, *, config=None) -> dict:
    query = (query or "").strip()
    if not query:
        return {"products": [], "interpretation": "", "suggestion": None}
    grounded = not FLAGS.price_hallucination
    maybe_latency()
    prompt = (
        f"Catalog (sku: name [tags] price):\n{catalog_index()}\n\n"
        f"Shopper query: {query}\n\n"
        'Map the query to 1-6 matching products. Return ONLY JSON: {"skus": ["NS-001"], '
        '"interpretation": "<one short sentence>", "did_you_mean": "<short alternative, or null>"}. '
        "Best first. Reply in English."
        if grounded else
        f"Shopper query: {query}\n\nGuess matching product SKUs (format NS-0XX). Return ONLY JSON: "
        '{"skus": [], "interpretation": "<sentence>", "did_you_mean": null}. Reply in English.'
    )
    result = invoke_local_llm(
        SEARCH_CONTROL_STEP_NAME,
        _SEARCH_SYSTEM_PROMPT,
        prompt + _JSON_ONLY,
        run_name=SEARCH_LLM_RUN_NAME,
        max_tokens=200,
        config=config,
    )
    parsed = None if is_unavailable(result) else parse_json(result.text)
    products = _resolve_skus((parsed or {}).get("skus"), 6)
    interpretation = ((parsed or {}).get("interpretation") or "").strip()
    if not products and grounded:
        products = _keyword_search(query)
        interpretation = interpretation or f"Showing results for “{query}”."
    if not grounded and not interpretation:
        interpretation = "Hmm, I'm not sure I understood that."
    suggestion = (parsed or {}).get("did_you_mean")
    return {
        "products": products[:6],
        "interpretation": interpretation,
        "suggestion": suggestion if isinstance(suggestion, str) else None,
    }


def cart_crosssell(cart_skus: list[str] | None = None, *, config=None) -> dict:
    cart = stable_skus([sku for sku in cart_skus or [] if find_product(sku)])[:12]
    if not cart:
        return {"products": [], "blurb": ""}
    grounded = not FLAGS.price_hallucination
    maybe_latency()
    names = [product["name"] for sku in cart if (product := find_product(sku))]
    cart_line = f"The cart contains: {', '.join(names)}.\n"
    prompt = (
        f"Catalog (sku: name [tags] price):\n{catalog_index()}\n\n{cart_line}"
        f"Suggest up to {CROSSSELL_N} products that complete this purchase and are not already in the cart. "
        'Return ONLY JSON: {"skus": ["NS-001"], "blurb": "<one short sentence>"}. Reply in English.'
        if grounded else
        f"{cart_line}Suggest up to {CROSSSELL_N} add-on product SKUs (format NS-0XX). Return ONLY JSON: "
        '{"skus": [], "blurb": "<one sentence>"}. Reply in English.'
    )
    result = invoke_local_llm(
        CART_CROSSSELL_CONTROL_STEP_NAME,
        _RECOMMENDATION_SYSTEM_PROMPT,
        prompt + _JSON_ONLY,
        run_name=CART_CROSSSELL_LLM_RUN_NAME,
        max_tokens=180,
        config=config,
    )
    parsed = None if is_unavailable(result) else parse_json(result.text)
    products = _resolve_skus((parsed or {}).get("skus"), CROSSSELL_N, set(cart))
    blurb = ((parsed or {}).get("blurb") or "").strip()
    if not products and grounded:
        products = _related_by_tags(cart, CROSSSELL_N, set(cart))
        blurb = blurb or "Goes well with what's in your cart."
    return {"products": products, "blurb": blurb or ("Complete your purchase." if grounded else "You might also like these.")}
