"""Shared formatting for catalog, sales, and account (F-BACKEND-1).

Exists to break the cycle `ai_features` ↔ `response_layout`: both needed these same
helpers, and `response_layout` could only reach them via deferred imports inside functions.
(F-BACKEND-2 sliced `ai_features` into `app/features/*`; the guard applies to all slices.)

Only PURE formatting enters here — dict of facts to text. Nothing that queries catalog, orders, or
LLM: that stays in the `app/features/` slices.
"""
from __future__ import annotations

from .users import GOLD_THRESHOLD, PLATINUM_THRESHOLD


def _usd(v: float) -> str:
    return f"${v:,.2f}"


def _availability(p: dict) -> str:
    return "out of stock" if p["stock"] == 0 else ("low stock" if p["stock"] <= 3 else "in stock")


def _catalog_stats_lines(cat: dict) -> list[str]:
    lines: list[str] = []
    if cat.get("most_expensive"):
        p = cat["most_expensive"]
        lines.append(f"Most expensive: {p['name']} ({p['sku']}) — {_usd(p['price'])}")
    if cat.get("cheapest"):
        p = cat["cheapest"]
        lines.append(f"Cheapest: {p['name']} ({p['sku']}) — {_usd(p['price'])}")
    pr = cat.get("price_range") or {}
    if pr:
        lines.append(
            f"Price range: {_usd(pr['min'])} – {_usd(pr['max'])} ({cat.get('product_count', 0)} products)"
        )
    if cat.get("out_of_stock"):
        names = ", ".join(p["name"] for p in cat["out_of_stock"][:3])
        lines.append(f"Out of stock: {names}")
    if cat.get("low_stock"):
        names = ", ".join(f"{p['name']} ({p['stock']} left)" for p in cat["low_stock"][:3])
        lines.append(f"Low stock: {names}")
    return lines


def _sales_stats_lines(sales: dict) -> list[str]:
    lines: list[str] = []
    if sales.get("bestseller"):
        b = sales["bestseller"]
        lines.append(f"Best seller (all-time units): {b['name']} ({b['units']} sold)")
    top = sales.get("top_products") or []
    if len(top) > 1:
        rest = ", ".join(f"{n} ({q})" for n, q in top[1:3])
        lines.append(f"Also popular: {rest}")
    if sales.get("total_units"):
        lines.append(f"Total units sold (paid orders): {sales['total_units']}")
    if sales.get("avg_ticket"):
        lines.append(f"Store avg ticket: {_usd(sales['avg_ticket'])}")
    if not lines and sales.get("paid_orders") == 0:
        lines.append("No paid orders yet — no sales data.")
    return lines


def _account_stats_lines(acct: dict) -> list[str]:
    top = ", ".join(f"{n} (×{q})" for n, q in acct.get("top_products") or []) or "—"
    lines = [
        f"Customer: {acct['name']}",
        f"Total spent: {_usd(acct['spend'])}",
        f"Orders placed: {acct['orders']} (paid {acct['paid']})",
        f"Most bought by this customer: {top}",
        f"Membership tier: {acct['tier']}",
    ]
    if acct.get("last"):
        lines.append(f"Latest order: {acct['last']}")
    if acct["tier"] == "STANDARD":
        lines.append(f"Next tier: GOLD at {_usd(GOLD_THRESHOLD)} total spend.")
    elif acct["tier"] == "GOLD":
        lines.append(f"Next tier: PLATINUM at {_usd(PLATINUM_THRESHOLD)} total spend.")
    return lines
