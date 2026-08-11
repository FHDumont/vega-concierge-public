"""Pure span suppression/reparenting policy (F-BACKEND-3, Stage D.1).

Workshop trace must **fit on screen**: today chat exports ~22 spans and fulfillment
~27, with LangGraph/LCEL plumbing (conditional edge routers, `RunnableSequence`,
RAG wrappers, 3-4× nesting of same name) pushing `[retriever]`/`[tool]` to 4-5
levels deep — far from reference example patterns.

This module decides **only** this: given span name and already-emitted parent name, does span
enter trace or not. Who applies decision (and reparents children of suppressed span) is
`VegaGalileoCallback`. No I/O, no SDK, no state — can freeze in test.

Two non-negotiable guarantees:

1. **UC denylist.** Spans that UC 1-5 of workshop tell participant to open in
   Console (`docs/reference/workshop-use-cases.md`) are **never** suppressible, no matter what
   generic rules say. Pretty trace that lost UC-2's `check_inventory` is broken
   workshop.
2. **Fail = no suppression.** Any exception here returns `False` (emits span). Observability
   doesn't break store, and doesn't even degrade trace: worst case it goes back to before.

`langsmith:hidden` tag does NOT substitute this: in galileo==2.6.0 it hides span but **orphans**
subtree (child points to parent that never entered tree and disappears on commit), so only works
for leaf. Hence suppression done here, with explicit reparenting in callback.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


# --- frozen denylist -------------------------------------------------------

# Source: `docs/reference/workshop-use-cases.md` (primary spans of UC-1..UC-5 and Agent Control
# appendix step names) + corresponding labels in `app/galileo_span.py`. Covers both
# graph node name (`returns.check_refund_eligibility`) and tool name (`check_refund_eligibility`),
# because check also looks at last dotted segment.
PROTECTED_SPAN_NAMES: frozenset[str] = frozenset({
    # UC-1 — invented price
    "product_qa",
    "answer_product_question",
    # UC-2 — token waste (gift recommendation)
    "gift_recommend.workflow",
    "gift_recommend.retrieve_catalog_context",
    "gift_recommend.rescan_catalog_context",
    "gift_recommend.search_catalog",
    "gift_recommend.rescan_catalog",
    "gift_recommend.confirm_catalog_search",
    "gift_recommend.quote_selected_product",
    "gift_recommend.verify_price_quote",
    "feature.compose_gift_recommendation",
    "gift_recommend.polish_recommendation",
    "search_catalog",
    "get_price",
    # UC-2 Advanced — inventory failure at checkout (toggle inventory_outage, not preset UC-2)
    "check_inventory",
    "confirm_cart_stock",
    "verify_cart_inventory_and_price",
    # UC-3 — refund wrongly denied
    "returns.finalize",
    "coordinate_refund_request",
    "check_refund_eligibility",
    "screen_refund_abuse",
    "process_refund",
    "assess_refund_eligibility",
    # UC-4 — prompt injection (destructive mutation and PII leak on the shopper's path)
    "delete_product",
    "list_recent_customers",
    "search",
    "semantic_product_search",
    # UC-5 — PII in notification copy
    "notification_copy",
    "compose_notification_text",
    "send_order_notification",
    # Fraud/payment — checkout business spans referenced in UC-2/UC-3
    "decide_fraud_allow_or_block",
    "charge_payment",
})

# --- suppression rules -------------------------------------------------------

# Raw LCEL class names — Console shows class, not business step.
_RAW_LCEL_NAMES: frozenset[str] = frozenset({
    "ChatPromptTemplate",
    "StrOutputParser",
})
_RAW_LCEL_PREFIXES: tuple[str, ...] = ("Runnable",)

# Graph plumbing and preparation wrappers — last segment of dotted name.
_SUPPRESSED_SEGMENTS: frozenset[str] = frozenset({
    "tools_condition",             # ReAct conditional edge
    "prepare_messages",            # `feature.<step>.prepare_messages`
    "replay_cached_response",      # `feature.<step>.replay_cached_response`
    # F-022 becomes metadata in ancestor span (D.2) instead of own span — covers both
    # LCEL wrapper (`feature.<step>.check_response_cache`) and raw tool invoked inside;
    # `VegaGalileoCallback` records `cache_hit`/`response_cache` on effective parent before
    # suppressing (see `_merge_cache_metadata`).
    "check_response_cache",
    # Structural wrappers of `fulfillment` graph (D.4 — live measurement: 19 nodes, goal ≤14).
    # None of these is a business decision nor appears in `docs/reference/workshop-use-cases.md`;
    # glue/bookkeeping around protected nodes (check_inventory, decide_fraud_allow_or_block,
    # confirm_cart_stock, charge_payment, send_order_notification), which stay intact.
    "run_checkout_tools",          # Pure ReAct `ToolNode` — tool calls (get_price/
                                    # check_inventory) promote to effective parent, don't disappear.
    "resolve_checkout_quote",      # normalizes inventory/quote from message history; when
                                    # triggers wrong-SKU fallback, tool called again
                                    # still appears (reparented), just wrapper label vanishes.
    "decrement_catalog_stock",     # post-payment bookkeeping, no business branch.
    "persist_order_status",        # saves final status; decision that created it (fraud/stock/
                                    # payment) already visible in corresponding protected nodes.
})

_ROUTE_PREFIXES: tuple[str, ...] = ("route_after_", "_route_after_")
# `chat_pick_next_specialist` and twin `concierge_pick_next_specialist` — spec names chat one,
# but same conditional edge; suffix catches both (and next graph copying pattern)
# without hunting literal names.
_ROUTE_SUFFIXES: tuple[str, ...] = ("_pick_next_specialist",)


def _last_segment(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def is_protected(name: str | None) -> bool:
    """True when span is one workshop UCs need to see in Console."""
    try:
        raw = (name or "").strip()
        if not raw:
            return False
        return raw in PROTECTED_SPAN_NAMES or _last_segment(raw) in PROTECTED_SPAN_NAMES
    except Exception as exc:  # noqa: BLE001 — policy never raises
        _logger.warning("span policy: is_protected failed for %r (%s)", name, exc)
        return True  # when in doubt, protect


def _is_raw_lcel(name: str) -> bool:
    return name in _RAW_LCEL_NAMES or name.startswith(_RAW_LCEL_PREFIXES)


def _is_graph_plumbing(segment: str) -> bool:
    if segment in _SUPPRESSED_SEGMENTS:
        return True
    return segment.startswith(_ROUTE_PREFIXES) or segment.endswith(_ROUTE_SUFFIXES)


def _is_context_wrapper(segment: str) -> bool:
    """`feature.merge_*_context` / `feature.retrieve_*_for_context` — only wrap retriever.

    Suppressing them is what promotes `[retriever]` span near root.
    """
    if segment.startswith("merge_") and segment.endswith("_context"):
        return True
    return segment.startswith("retrieve_") and segment.endswith("_for_context")


def suppress(name: str | None, parent_name: str | None = None) -> bool:
    """Decide if span `name`, child of already-emitted span `parent_name`, should leave trace.

    `parent_name is None` = trace root (or unknown effective parent): **never** suppress — without
    root SDK has nowhere to hang tree and entire trace is lost.
    """
    try:
        raw = (name or "").strip()
        if not raw:
            return False
        if parent_name is None:
            return False
        if is_protected(raw):
            return False

        segment = _last_segment(raw)
        if _is_raw_lcel(raw):
            return True
        if _is_graph_plumbing(segment):
            return True
        if _is_context_wrapper(segment):
            return True
        # Identical nesting to parent (LCEL repeats same `run_name` 3-4× in depth):
        # first in chain survives, ones below vanish. D.4 extends comparison to the LAST
        # SEGMENT of the name: `feature.answer_store_policy` under `chat.answer_store_policy` is the
        # same business step with a different namespace prefix (graph vs. the feature's LCEL) —
        # without this the retriever ends up 3 levels from the root (goal is ≤2). Protected names already
        # left before (`is_protected` above), so this never reduces a node from UCs 1-5.
        if not parent_name:
            return False
        parent_raw = parent_name.strip()
        if raw == parent_raw:
            return True
        return bool(segment) and segment == _last_segment(parent_raw)
    except Exception as exc:  # noqa: BLE001 — fallback WITHOUT suppression
        _logger.warning("span policy: suppress failed for %r (%s)", name, exc)
        return False
