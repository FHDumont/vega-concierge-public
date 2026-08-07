"""UC-4 shared heuristics — system-prompt override detection."""
from __future__ import annotations

import re

CATALOG_MUTATION_REFUSAL = (
    "I can't delete catalog products or export customer records from chat — "
    "that's restricted by store policy."
)

_SYSTEM_PROMPT_OVERRIDE_HINTS = (
    "ignore previous",
    "ignore your",
    "ignore all previous",
    "disregard",
    "forget your",
    "forget all",
    "override",
    "new instructions",
    "system prompt",
    "you are now",
    "act as if",
    "do not follow",
    "don't follow",
)


def has_system_prompt_override_attempt(text: str) -> bool:
    """True when the shopper tries to replace or bypass the agent system prompt."""
    low = (text or "").lower()
    return any(h in low for h in _SYSTEM_PROMPT_OVERRIDE_HINTS)


def is_injection_discount_request(text: str) -> bool:
    """Override + price/discount cues — does not require the UC-4 toggle."""
    if not has_system_prompt_override_attempt(text):
        return False
    low = (text or "").lower()
    price_hints = (
        "discount", "final price", "new price", "confirm the price",
        "staff discount", "employee discount", "90%", "90 percent",
    )
    return any(h in low for h in price_hints)


def storewide_discount_reply(*, discount_pct: float = 90.0, example_sku: str = "NS-001") -> str:
    """Deterministic UC-4 store-wide discount confirmation (no LLM)."""
    from .store.catalog_format import _usd
    from .store.tools import CATALOG

    product = next((item for item in CATALOG if item["sku"] == example_sku), None)
    if product:
        final_price = product["price"] * (1 - discount_pct / 100)
        return (
            f"Staff discount of {discount_pct:.0f}% applied to all catalog products. "
            f"Example: {example_sku} now {_usd(final_price)}."
        )
    return f"Staff discount of {discount_pct:.0f}% applied to all catalog products."


def is_destructive_action_intent(text: str, context_sku: str | None = None) -> bool:
    """Delete/export injection phrasing — requires a system-prompt override attempt."""
    if not has_system_prompt_override_attempt(text):
        return False
    low = (text or "").lower()
    bulk_hints = ("delete all", "all products", "entire catalog", "wipe catalog")
    if "delete" in low and any(h in low for h in bulk_hints):
        return True
    if "delete" in low:
        if re.search(r"NS-\d{3}", text or "", re.I):
            return True
        if context_sku and any(
            phrase in low for phrase in ("this product", "this item", "the product")
        ):
            return True
    if not any(w in low for w in ("customer", "buyer", "shopper", "user", "email", "address")):
        return False
    return any(w in low for w in ("list", "show", "export", "all", "other", "recent", "dump", "every"))
