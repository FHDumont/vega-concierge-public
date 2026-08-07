"""Catalog/sales/account statistics for shopper chat (`feature.answer_store_statistics`)."""
from __future__ import annotations

import re
import time

from langchain_core.runnables import RunnableLambda

from ..galileo_span import AGGREGATE_STORE_STATISTICS, BUSINESS_STEPS, llm_run_name, replay_stats_answer_run_name
from ..llm.agent_llm_invoke import invoke_feature_llm, is_stub_output
from ..problems import FLAGS
from ..store.catalog_format import _account_stats_lines, _catalog_stats_lines, _sales_stats_lines, _usd
from ..store.tools import CATALOG
from ..chat_layout import build_stats_layout, shopper_reply_from_layout
from ..store.langchain_tools import get_account_stats_tool, get_catalog_stats_tool

LLM_RUN_NAME = "feature.answer_store_statistics"
_SYSTEM_PROMPT = (
    "You answer factual store statistics using ONLY the supplied aggregate facts. "
    "Be concise. Reply in English without markdown."
)
_PAID_STATUSES = ("PAID", "SHIPPED", "DELIVERED")
_CATALOG_STATS_HINTS = (
    "most expensive", "cheapest", "expensive", "price range", "out of stock", "low stock",
    "how many products", "mais caro", "mais barato", "quantos produtos",
)
_SALES_STATS_HINTS = (
    "best seller", "best-selling", "bestseller", "most sold", "most popular", "top seller",
    "mais vendido", "mais popular",
)
_ACCOUNT_STATS_HINTS = (
    "how much spent", "how much have i spent", "total spent", "my spending", "my orders",
    "how many orders", "how many purchases", "purchase count", "order count", "my history",
    "quanto gastei", "gastei", "quantas compras", "minhas compras",
)
_MOST_EXPENSIVE_ONLY = ("most expensive", "highest price", "priciest", "mais caro", "produto mais caro")
_CHEAPEST_ONLY = ("cheapest", "most cheap", "lowest price", "mais barato", "produto mais barato")
_COMPOUND_STATS = (
    "price range", "best seller", "best-selling", "bestseller", "most sold", "most popular",
    "out of stock", "low stock", "how much spent", "how many orders", "spending",
    "mais vendido", "quanto gastei", "esgotado",
)


def _product_brief(product: dict) -> dict:
    return {"sku": product["sku"], "name": product["name"], "price": round(float(product["price"]), 2)}


def catalog_stats() -> dict:
    if not CATALOG:
        return {"product_count": 0, "cheapest": None, "most_expensive": None,
                "price_range": {"min": 0.0, "max": 0.0}, "tag_counts": {}, "out_of_stock": [], "low_stock": []}
    cheapest = min(CATALOG, key=lambda item: item["price"])
    priciest = max(CATALOG, key=lambda item: item["price"])
    prices = [item["price"] for item in CATALOG]
    tag_counts: dict[str, int] = {}
    for product in CATALOG:
        for tag in product["tags"]:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return {
        "cheapest": _product_brief(cheapest),
        "most_expensive": _product_brief(priciest),
        "price_range": {"min": round(min(prices), 2), "max": round(max(prices), 2)},
        "product_count": len(CATALOG),
        "tag_counts": tag_counts,
        "out_of_stock": [_product_brief(p) for p in CATALOG if p["stock"] == 0],
        "low_stock": [{"sku": p["sku"], "name": p["name"], "stock": p["stock"]}
                      for p in CATALOG if 0 < p["stock"] <= 3],
    }


def store_sales_stats() -> dict:
    from ..store import orders

    paid = [order for order in orders.list_orders() if order["status"] in _PAID_STATUSES]
    units: dict[str, int] = {}
    total_units = 0
    for order in paid:
        for item in order.get("items", []):
            qty = int(item.get("qty") or 0)
            if qty:
                label = item.get("name") or item.get("sku", "")
                units[label] = units.get(label, 0) + qty
                total_units += qty
    top = sorted(units.items(), key=lambda item: item[1], reverse=True)[:3]
    revenue = round(sum(order["total"] for order in paid), 2)
    bestseller = {"name": top[0][0], "units": top[0][1]} if top else None
    return {
        "paid_orders": len(paid),
        "total_units": total_units,
        "top_products": top,
        "bestseller": bestseller,
        "avg_ticket": round(revenue / len(paid), 2) if paid else 0.0,
    }


def account_stats(user_id: str | None) -> dict | None:
    if not user_id:
        return None
    from ..store import orders, users

    user = users.get_user(user_id)
    if user is None:
        return None
    spend = orders.spend_for_user(user_id)
    public = users.public_user(user, spend)
    own_orders = orders.list_orders_for_user(user_id)
    paid = sum(1 for order in own_orders if order["status"] in _PAID_STATUSES)
    return {
        "name": public.get("name"),
        "spend": round(float(spend), 2),
        "orders": len(own_orders),
        "paid": paid,
        "tier": public.get("tier"),
    }


def _stats_scope(question: str) -> set[str]:
    low = (question or "").lower()
    scopes: set[str] = set()
    if any(h in low for h in _CATALOG_STATS_HINTS):
        scopes.add("catalog")
    if any(h in low for h in _SALES_STATS_HINTS):
        scopes.add("sales")
    if any(h in low for h in _ACCOUNT_STATS_HINTS):
        scopes.add("account")
    return scopes


def _build_stats_context(scopes: set[str], user_id: str | None) -> tuple[str, dict]:
    facts: dict = {"scopes": sorted(scopes)}
    sections: list[str] = []
    if "catalog" in scopes:
        cat = catalog_stats()
        facts["catalog"] = cat
        lines = _catalog_stats_lines(cat)
        if lines:
            sections.append("Store catalog facts:\n" + "\n".join(f"- {line}" for line in lines))
    if "sales" in scopes:
        sales = store_sales_stats()
        facts["sales"] = sales
        lines = _sales_stats_lines(sales)
        if lines:
            sections.append("Store sales facts:\n" + "\n".join(f"- {line}" for line in lines))
    if "account" in scopes:
        acct = account_stats(user_id)
        facts["account"] = acct
        if acct is None:
            sections.append("Account: customer is not signed in — no personal purchase history available.")
        else:
            sections.append("Your account:\n" + "\n".join(f"- {line}" for line in _account_stats_lines(acct)))
    if not sections:
        cat = catalog_stats()
        sales = store_sales_stats()
        facts["catalog"] = cat
        facts["sales"] = sales
        sections.append("Store catalog facts:\n" + "\n".join(f"- {line}" for line in _catalog_stats_lines(cat)))
        sections.append("Store sales facts:\n" + "\n".join(f"- {line}" for line in _sales_stats_lines(sales)))
        if user_id:
            acct = account_stats(user_id)
            facts["account"] = acct
            if acct:
                sections.append("Your account:\n" + "\n".join(f"- {line}" for line in _account_stats_lines(acct)))
    return "\n\n".join(sections), facts


def _stats_compact_facts(facts: dict) -> dict:
    out: dict = {"scopes": facts.get("scopes") or []}
    if facts.get("catalog"):
        cat = facts["catalog"]
        out["catalog"] = {
            "product_count": cat.get("product_count"),
            "most_expensive": cat.get("most_expensive"),
            "cheapest": cat.get("cheapest"),
            "price_range": cat.get("price_range"),
        }
    if facts.get("sales"):
        sales = facts["sales"]
        out["sales"] = {
            "paid_orders": sales.get("paid_orders"),
            "bestseller": sales.get("bestseller"),
            "total_units": sales.get("total_units"),
        }
    if "account" in facts:
        acct = facts.get("account")
        out["account"] = None if acct is None else {
            "name": acct.get("name"), "spend": acct.get("spend"),
            "orders": acct.get("orders"), "tier": acct.get("tier"),
        }
    return out


def _emit_aggregate_store_statistics(facts: dict, *, config=None) -> None:
    if not config or not config.get("callbacks"):
        return
    compact = _stats_compact_facts(facts)
    try:
        chain = RunnableLambda(lambda _: compact, name=AGGREGATE_STORE_STATISTICS).with_config(
            {"run_name": AGGREGATE_STORE_STATISTICS, "name": AGGREGATE_STORE_STATISTICS},
        )
        chain.invoke({}, config=config)
    except Exception:  # noqa: BLE001
        pass


def _emit_stats_replay(answer: str, *, config=None) -> None:
    if not config or not config.get("callbacks"):
        return
    feature_run = llm_run_name("feature", BUSINESS_STEPS["stats_chat"])
    replay_name = replay_stats_answer_run_name(feature_run)
    try:
        chain = RunnableLambda(lambda _: answer, name=replay_name).with_config(
            {"run_name": replay_name, "name": replay_name},
        )
        chain.with_config({"run_name": feature_run, "name": feature_run}).invoke({}, config=config)
    except Exception:  # noqa: BLE001
        pass


def _is_trivial_stats_fast_path(question: str) -> bool:
    low = (question or "").lower()
    if any(h in low for h in _COMPOUND_STATS):
        return False
    has_expensive = any(h in low for h in _MOST_EXPENSIVE_ONLY)
    has_cheapest = any(h in low for h in _CHEAPEST_ONLY)
    return has_expensive ^ has_cheapest


def _trivial_stats_answer(question: str, facts: dict) -> str:
    cat = facts.get("catalog") or catalog_stats()
    low = (question or "").lower()
    if any(h in low for h in _MOST_EXPENSIVE_ONLY):
        product = cat.get("most_expensive")
        if product:
            return f"The most expensive product is {product['name']} ({product['sku']}) at {_usd(product['price'])}."
    if any(h in low for h in _CHEAPEST_ONLY):
        product = cat.get("cheapest")
        if product:
            return f"The cheapest product is {product['name']} ({product['sku']}) at {_usd(product['price'])}."
    return _stats_fallback(question, facts, grounded=True)


def _stats_fallback(question: str, facts: dict, *, grounded: bool) -> str:
    if not grounded:
        return "Our most expensive product is $9.99 and you've spent over $50,000 with us!"
    scopes = set(facts.get("scopes") or [])
    parts: list[str] = []
    if "catalog" in scopes or facts.get("catalog"):
        cat = facts.get("catalog") or catalog_stats()
        if cat.get("most_expensive") and cat.get("cheapest"):
            hi, lo = cat["most_expensive"], cat["cheapest"]
            parts.append(
                f"The most expensive product is {hi['name']} ({hi['sku']}) at {_usd(hi['price'])}; "
                f"the cheapest is {lo['name']} ({lo['sku']}) at {_usd(lo['price'])}."
            )
    if "sales" in scopes or facts.get("sales"):
        sales = facts.get("sales") or store_sales_stats()
        if sales.get("bestseller"):
            best = sales["bestseller"]
            parts.append(f"The best seller is {best['name']} ({best['units']} units sold).")
        elif sales.get("paid_orders") == 0:
            parts.append("No sales recorded yet.")
    if "account" in scopes:
        acct = facts.get("account")
        if acct is None:
            parts.append("Please sign in to see your purchase history and spending.")
        elif acct:
            parts.append(
                f"You've placed {acct['orders']} order(s), totaling {_usd(acct['spend'])}."
            )
    return " ".join(parts) or "I can help with catalog prices, best sellers, or your order history."


def _store_action_for_guest_stats(
    user_id: str | None,
    scopes: set[str],
    facts: dict,
    answer: str,
) -> str | None:
    if user_id is not None:
        return None
    if "account" in scopes and facts.get("account") is None:
        return "sign_in"
    if "sign in" in (answer or "").lower():
        return "sign_in"
    return None


def stats_chat(question: str, user_id: str | None, *, config=None) -> dict:
    question = (question or "").strip() or "Store statistics"
    scopes = _stats_scope(question) or {"catalog", "sales", "account"}
    if scopes == {"account"} and user_id is None:
        msg = "Please sign in to see your purchase history and how much you've spent."
        return {
            "answer": msg,
            "grounded": True,
            "scopes": sorted(scopes),
            "layout": None,
            "store_action": "sign_in",
        }

    context_block, facts = _build_stats_context(scopes, user_id)
    facts["_question"] = question
    if "catalog" in scopes:
        get_catalog_stats_tool.invoke({}, config=config)
    if "account" in scopes:
        get_account_stats_tool.invoke({}, config=config)
    _emit_aggregate_store_statistics(facts, config=config)
    grounded = not FLAGS.price_hallucination
    account_grounded = grounded and scopes == {"account"} and bool(facts.get("account"))

    if grounded and _is_trivial_stats_fast_path(question):
        deterministic = _trivial_stats_answer(question, facts)
        result = invoke_feature_llm(
            "stats_chat",
            _SYSTEM_PROMPT,
            (
                f"{context_block}\n\nAnswer the customer's question in one concise sentence "
                "using ONLY the facts above. Reply in English. No markdown."
                f"\n\nShopper question: {question}"
            ),
            run_name=LLM_RUN_NAME,
            max_tokens=80,
            verbose=FLAGS.cost_spike,
            config=config,
        )
        text = deterministic if is_stub_output(result.text) else result.text.strip()
        layout = build_stats_layout(facts, scopes)
        answer = shopper_reply_from_layout(layout, text)
        answer = answer.strip()
        return {
            "answer": answer,
            "grounded": grounded,
            "scopes": sorted(scopes),
            "layout": layout,
            "store_action": _store_action_for_guest_stats(user_id, scopes, facts, answer),
        }

    if FLAGS.latency_spike:
        time.sleep(1.2)

    if grounded:
        prompt = (
            f"{context_block}\n\nAnswer the customer's question using ONLY the facts above. "
            "Be concise (1-3 sentences). If account data says they are not signed in, tell them "
            "to sign in. Reply in English. No markdown."
        )
    else:
        prompt = (
            "You have no store statistics. Answer confidently with specific product names, prices, "
            "and purchase figures anyway — never say you lack data. Reply in English. No markdown."
        )
    result = invoke_feature_llm(
        "stats_chat", _SYSTEM_PROMPT, f"{prompt}\n\nShopper question: {question}",
        run_name=LLM_RUN_NAME, max_tokens=120, verbose=FLAGS.cost_spike, config=config,
    )
    if is_stub_output(result.text) or account_grounded:
        text = _stats_fallback(question, facts, grounded=grounded)
    else:
        text = result.text
    layout = build_stats_layout(facts, scopes)
    answer = shopper_reply_from_layout(layout, text.strip())
    answer = answer.strip()
    return {
        "answer": answer,
        "grounded": grounded,
        "scopes": sorted(scopes),
        "layout": layout,
        "store_action": _store_action_for_guest_stats(user_id, scopes, facts, answer),
    }
