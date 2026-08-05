"""Compare 2 produtos (F-029) — orquestração ReAct via LangGraph (F-OBS-PREP-4).

O **Compare Coordinator** roda loop ReAct (`get_price` ×2 via ToolNode); o **Comparator**
(veredito) fica no nó `finalize` com `feature_complete("comparator")` — cache F-022 na 2ª
comparação do mesmo par.
"""
from .graphs.compare import build_compare_graph, _find as _catalog_find
from .runnable_config import resolve_config, set_current_runnable_config


def run_compare(sku_a: str, sku_b: str, *, config=None) -> dict | None:
    """Compara 2 produtos do catálogo. Retorna `{product_a, product_b, verdict}` ou None se algum
    SKU não existe (404). Honra toggles (`price_hallucination`, `cost_spike`)."""
    a, b = _catalog_find(sku_a), _catalog_find(sku_b)
    if a is None or b is None:
        return None
    resolved = resolve_config(config, feature="compare")
    token = set_current_runnable_config(resolved)
    try:
        result = build_compare_graph().invoke(
            {
                "sku_a": sku_a,
                "sku_b": sku_b,
                "product_a": a,
                "product_b": b,
                "messages": [],
                "trace": [],
            },
            config=resolved,
        )
    finally:
        set_current_runnable_config(None, token)
    return {
        "product_a": result["product_a"],
        "product_b": result["product_b"],
        "verdict": result["verdict"],
    }


async def arun_compare(sku_a: str, sku_b: str, *, config=None) -> dict | None:
    a, b = _catalog_find(sku_a), _catalog_find(sku_b)
    if a is None or b is None:
        return None
    resolved = resolve_config(config, feature="compare")
    token = set_current_runnable_config(resolved)
    try:
        result = await build_compare_graph().ainvoke(
            {
                "sku_a": sku_a,
                "sku_b": sku_b,
                "product_a": a,
                "product_b": b,
                "messages": [],
                "trace": [],
            },
            config=resolved,
        )
    finally:
        set_current_runnable_config(None, token)
    return {
        "product_a": result["product_a"],
        "product_b": result["product_b"],
        "verdict": result["verdict"],
    }
