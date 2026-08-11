"""Span suppression/reparenting policy (F-BACKEND-3, Step D.1).

Two layers:

* `app.obs.galileo_span_policy` — pure function `suppress(name, parent_name)`. This is home to
  the **frozen denylist**: the spans that UC-1..UC-5 require to be open in the Console
  (`docs/reference/workshop-use-cases.md`) must not disappear from the trace because of some
  future generic rule.
* `app.obs.galileo_callback.VegaGalileoCallback` — applies the decision and does the reparenting.
  Tested against a `FakeHandler` that reproduces the SDK's node accounting (`galileo==2.6.0`):
  same `Node`, same indexing by `str(run_id)`, same `children` list. This is what lets us assert
  that `[retriever]` gets promoted a level instead of becoming an orphan.
"""
from __future__ import annotations

import uuid

import pytest

from app.obs import galileo_span_policy as policy
from app.obs.galileo_span_policy import PROTECTED_SPAN_NAMES, is_protected, suppress

galileo = pytest.importorskip("galileo")

from app.obs.galileo_callback import VegaGalileoCallback  # noqa: E402
from galileo.schema.handlers import Node  # noqa: E402


# =============================================================================
# 1. Pure policy
# =============================================================================

@pytest.mark.parametrize("name", [
    # LangGraph conditional edges
    "fulfillment.route_after_checkout_tools",
    "fulfillment.route_after_fraud_decision",
    "fulfillment.route_after_coordinator_tools",
    "returns.route_after_abuse_screen",
    "route_after_coordinator_tools",
    "_route_after_payment",
    "chat_pick_next_specialist",
    "concierge_pick_next_specialist",
    "tools_condition",
    # raw LCEL classes
    "ChatPromptTemplate",
    "StrOutputParser",
    "RunnableSequence",
    "RunnableLambda",
    "RunnableAssign",
    "RunnableParallel<context,question>",
    # prep/RAG wrappers
    "feature.merge_policy_context",
    "feature.merge_catalog_context",
    "feature.merge_static_context",
    "feature.retrieve_policies_for_context",
    "feature.retrieve_catalog_for_context",
    "feature.answer_store_policy.prepare_messages",
    "feature.answer_store_policy.replay_cached_response",
    # F-022 (D.2) — cache turns into metadata on the ancestor, the span disappears
    "check_response_cache",
    "feature.answer_store_policy.check_response_cache",
    # Structural wrappers of the `fulfillment` graph (D.4 — live measurement: 19 nodes, target ≤14).
    "fulfillment.run_checkout_tools",
    "fulfillment.resolve_checkout_quote",
    "fulfillment.decrement_catalog_stock",
    "fulfillment.persist_order_status",
])
def test_plumbing_spans_are_suppressed(name):
    assert suppress(name, "chat.workflow") is True


def test_identical_nesting_keeps_only_the_outermost_span():
    # LCEL repeats the same `run_name` 3-4x in depth; the first one in the chain survives.
    assert suppress("chat.assemble_shopper_reply", "chat.assemble_shopper_reply") is True


def test_last_segment_match_collapses_namespace_prefix_duplicates():
    # D.4: `feature.answer_store_policy` (the feature's LCEL) under `chat.answer_store_policy`
    # (the graph node) is the SAME business step with a different namespace prefix — without this
    # `[retriever]` ends up 3 levels from the root (target is ≤2). UC names remain immune (checked
    # in `test_use_case_spans_are_never_suppressed`, which already covers `suppress(name, name)`).
    assert suppress("feature.answer_store_policy", "chat.answer_store_policy") is True
    assert suppress("feature.answer_store_policy", "feature.answer_store_policy") is True
    # Different segments do not collide by accident.
    assert suppress("feature.answer_store_policy.invoke_llm", "feature.answer_store_policy") is False
    assert suppress("chat.route_decision", "chat.route_shopper_request") is False


@pytest.mark.parametrize("name", [
    "chat.workflow",
    "fulfillment.workflow",
    "chat.route_shopper_request",
    "chat.route_decision",          # business decision — not a `route_after_*`
    "chat.answer_store_policy",
    "chat.assemble_shopper_reply",
    "feature.answer_store_policy",
    "feature.answer_store_policy.invoke_llm",
    "returns.resolve_policy_and_calc",
    "retrieve_store_policies",
    "aggregate_store_statistics",
])
def test_business_spans_survive(name):
    assert suppress(name, "some.other.parent") is False


def test_root_span_is_never_suppressed():
    # Without a root the SDK has nowhere to hang the tree: the whole trace is lost.
    assert suppress("RunnableSequence", None) is False
    assert suppress("chat_pick_next_specialist", None) is False


@pytest.mark.parametrize("name", ["", "   ", None])
def test_empty_names_are_not_suppressed(name):
    assert suppress(name, "chat.workflow") is False


# --- frozen denylist -----------------------------------------------------------

# Source: `docs/reference/workshop-use-cases.md` (primary spans + Agent Control step names).
# Frozen on purpose: touching this means touching the workshop.
UC_SPAN_NAMES = [
    ("UC-1", "product_qa"),
    ("UC-1", "answer_product_question"),
    ("UC-2", "check_inventory"),
    ("UC-2", "confirm_cart_stock"),
    ("UC-3", "check_refund_eligibility"),
    ("UC-3", "screen_refund_abuse"),
    ("UC-3", "process_refund"),
    ("UC-3", "returns.finalize"),
    ("UC-4", "delete_product"),
    ("UC-4", "list_recent_customers"),
    ("UC-4", "search"),
    ("UC-5", "notification_copy"),
    ("UC-2/3", "decide_fraud_allow_or_block"),
    ("UC-2/3", "charge_payment"),
    ("UC-2/3", "send_order_notification"),
]


@pytest.mark.parametrize("uc,name", UC_SPAN_NAMES)
def test_use_case_spans_are_in_the_frozen_denylist(uc, name):
    assert name in PROTECTED_SPAN_NAMES, f"{uc}: {name} fell out of the denylist"
    assert is_protected(name), name


@pytest.mark.parametrize("uc,name", UC_SPAN_NAMES)
def test_use_case_spans_are_never_suppressed(uc, name):
    # Not as a dotted graph node, not under the generic name-equals-parent rule, and not if
    # someone one day names a UC node with a plumbing prefix.
    dotted = f"returns.{name}" if "." not in name else name
    assert suppress(name, "chat.workflow") is False
    assert suppress(dotted, "chat.workflow") is False
    assert suppress(name, name) is False
    assert suppress(dotted, dotted) is False


def test_protection_matches_the_graph_node_names_used_today():
    for node in (
        "returns.check_refund_eligibility",
        "returns.screen_refund_abuse",
        "returns.process_refund",
        "fulfillment.decide_fraud_allow_or_block",
        "fulfillment.charge_payment",
        "fulfillment.confirm_cart_stock",
        "fulfillment.send_order_notification",
        "chat.answer_product_question",
        "chat.semantic_product_search",
    ):
        assert is_protected(node), node


# --- fallback -----------------------------------------------------------------

def test_suppress_falls_back_to_emitting_when_the_policy_explodes(monkeypatch):
    def boom(_name):
        raise RuntimeError("broken policy")

    monkeypatch.setattr(policy, "_last_segment", boom)
    # Without the fallback this would raise and take down `on_chain_start` (and the request with it).
    assert policy.suppress("RunnableSequence", "chat.workflow") is False
    assert policy.suppress("chat_pick_next_specialist", "chat.workflow") is False


def test_is_protected_errs_on_the_side_of_protection(monkeypatch):
    def boom(_name):
        raise RuntimeError("broken policy")

    monkeypatch.setattr(policy, "_last_segment", boom)
    assert policy.is_protected("check_inventory") is True


# =============================================================================
# 2. Callback — suppression + reparenting
# =============================================================================

class FakeHandler:
    """`GalileoBaseHandler` (galileo==2.6.0) node accounting, without network or logger.

    Just enough for `GalileoAsyncCallback` to run: `_nodes` indexed by `str(run_id)`,
    `children` in arrival order, root = first node started.
    """

    def __init__(self) -> None:
        self._nodes: dict[str, Node] = {}
        self._root_node: Node | None = None

    def get_node(self, run_id):
        return self._nodes.get(str(run_id))

    def get_nodes(self):
        return self._nodes

    async def async_start_node(self, node_type, parent_run_id, run_id, **kwargs):
        node = Node(node_type=node_type, span_params=kwargs, run_id=run_id, parent_run_id=parent_run_id)
        self._nodes[str(run_id)] = node
        if self._root_node is None:
            self._root_node = node
        if parent_run_id is not None:
            parent = self._nodes.get(str(parent_run_id))
            if parent is not None:
                parent.children.append(str(run_id))
        return node

    async def async_end_node(self, run_id, **kwargs):
        node = self._nodes.get(str(run_id))
        if node is not None:
            node.span_params.update(**kwargs)

    # --- test queries -----------------------------------------------------------

    def name_of(self, run_id) -> str:
        node = self._nodes.get(str(run_id))
        return "" if node is None else str(node.span_params.get("name") or "")

    def emitted_names(self) -> list[str]:
        return [str(n.span_params.get("name") or "") for n in self._nodes.values()]

    def tree(self) -> list[tuple[int, str]]:
        """(depth, name) walking from the root — only what the commit would export."""
        out: list[tuple[int, str]] = []

        def walk(node: Node, depth: int) -> None:
            out.append((depth, str(node.span_params.get("name") or "")))
            for child_id in node.children:
                child = self._nodes.get(child_id)
                if child is not None:
                    walk(child, depth + 1)

        if self._root_node is not None:
            walk(self._root_node, 0)
        return out

    def depth_of(self, name: str) -> int | None:
        for depth, node_name in self.tree():
            if node_name == name:
                return depth
        return None

    def orphans(self) -> list[str]:
        reachable = {node_name for _, node_name in self.tree()}
        return [n for n in self.emitted_names() if n not in reachable]


def _callback() -> tuple[VegaGalileoCallback, FakeHandler]:
    cb = VegaGalileoCallback.__new__(VegaGalileoCallback)
    handler = FakeHandler()
    cb._handler = handler
    cb._dropped = {}
    return cb, handler


async def _replay(cb, spans) -> dict[str, uuid.UUID]:
    """Replays `(kind, name, key, parent_key[, tags[, metadata]])` as LangChain would deliver it."""
    ids: dict[str, uuid.UUID] = {}
    for span in spans:
        kind, name, key, parent_key = span[:4]
        tags = span[4] if len(span) > 4 else None
        metadata = span[5] if len(span) > 5 else None
        ids[key] = uuid.uuid4()
        parent = ids.get(parent_key) if parent_key else None
        serialized = {"name": name}
        if kind == "chain":
            await cb.on_chain_start(serialized, {}, run_id=ids[key], parent_run_id=parent, tags=tags)
        elif kind == "chat":
            await cb.on_chat_model_start(serialized, [[]], run_id=ids[key], parent_run_id=parent)
        elif kind == "llm":
            await cb.on_llm_start(serialized, ["p"], run_id=ids[key], parent_run_id=parent)
        elif kind == "tool":
            await cb.on_tool_start(
                serialized, "{}", run_id=ids[key], parent_run_id=parent, metadata=metadata,
            )
        elif kind == "retriever":
            await cb.on_retriever_start(serialized, "q", run_id=ids[key], parent_run_id=parent)
        else:  # pragma: no cover — test-writing error
            raise AssertionError(kind)
    return ids


# Tree observed in the `chat` graph (spy callback dump over `build_chat_graph()`).
CHAT_TREE = [
    ("chain", "chat.workflow", "root", None),
    ("chain", "chat.route_shopper_request", "route1", "root"),
    ("chain", "chat.route_decision", "dec1", "route1"),
    ("chain", "chat_pick_next_specialist", "pick1", "route1"),
    ("chain", "chat.answer_store_policy", "policy", "root"),
    ("chain", "feature.answer_store_policy", "feat", "policy"),
    ("chain", "feature.merge_policy_context", "merge", "feat"),
    ("chain", "feature.retrieve_policies_for_context", "retr", "merge"),
    ("retriever", "retrieve_store_policies", "retriever", "retr"),
    ("chain", "feature.merge_policy_context", "merge2", "merge"),
    ("chain", "feature.answer_store_policy", "feat2", "feat"),
    ("chain", "feature.answer_store_policy.invoke_llm", "inv1", "feat2"),
    ("chain", "feature.answer_store_policy.invoke_llm", "inv2", "feat2"),
    ("chain", "feature.answer_store_policy.invoke_llm", "inv3", "inv2"),
    ("chain", "feature.answer_store_policy.prepare_messages", "prep", "inv3"),
    ("chat", "feature.answer_store_policy", "llm", "inv3"),
    ("chain", "chat.assemble_shopper_reply", "final", "root"),
    ("chain", "chat.assemble_shopper_reply", "final2", "final"),
]


async def test_chat_tree_drops_only_the_plumbing():
    cb, handler = _callback()
    await _replay(cb, CHAT_TREE)

    names = handler.emitted_names()
    assert "chat_pick_next_specialist" not in names
    assert "feature.merge_policy_context" not in names
    assert "feature.retrieve_policies_for_context" not in names
    assert "feature.answer_store_policy.prepare_messages" not in names
    # Survive: root, business nodes, and the retriever. `feature.answer_store_policy` (the LCEL
    # wrapper, `kind="chain"`) disappears via the last-segment collapse (D.4) — what's left with
    # that name is just the model's `chat`/leaf span, which is never suppressed.
    for kept in (
        "chat.workflow",
        "chat.route_shopper_request",
        "chat.route_decision",
        "chat.answer_store_policy",
        "retrieve_store_policies",
        "chat.assemble_shopper_reply",
    ):
        assert kept in names, (kept, names)


async def test_retriever_is_promoted_and_never_orphaned():
    cb, handler = _callback()
    ids = await _replay(cb, CHAT_TREE)

    # D.4: `feature.answer_store_policy` (the feature's LCEL wrapper) now disappears together
    # with `merge_policy_context`/`retrieve_policies_for_context` — same last segment as
    # `chat.answer_store_policy` (the graph node). The retriever hangs directly off the graph
    # node, 2 levels from the root (Step D target: `[retriever]`/`[tool]` at ≤2 levels).
    assert handler.get_node(ids["retriever"]).parent_run_id == ids["policy"]
    assert handler.depth_of("retrieve_store_policies") == 2
    assert handler.orphans() == []


async def test_reparenting_resolves_a_chain_of_dropped_parents():
    cb, handler = _callback()
    ids = await _replay(cb, [
        ("chain", "returns.workflow", "root", None),
        ("chain", "returns.coordinate_refund_request", "node", "root"),
        ("chain", "RunnableSequence", "a", "node"),          # suppressed
        ("chain", "feature.merge_policy_context", "b", "a"),  # suppressed, parent already suppressed
        ("chain", "ChatPromptTemplate", "c", "b"),            # suppressed, 3rd in the chain
        ("tool", "check_refund_eligibility", "tool", "c"),
        ("chat", "returns.check_refund_eligibility", "llm", "c"),
    ])

    # The tool jumps three levels at once, up to the one ancestor that survived.
    assert handler.get_node(ids["tool"]).parent_run_id == ids["node"]
    assert handler.get_node(ids["llm"]).parent_run_id == ids["node"]
    assert handler.depth_of("check_refund_eligibility") == 2
    assert handler.orphans() == []
    assert cb._dropped[ids["c"]] == ids["node"]


async def test_hidden_tag_reparents_instead_of_orphaning_the_subtree():
    # `langsmith:hidden` in the SDK hides the span but leaves the children pointing at a parent
    # that never entered the tree — everything disappears on commit. Here the child gets promoted
    # to the effective parent.
    cb, handler = _callback()
    ids = await _replay(cb, [
        ("chain", "fulfillment.workflow", "root", None),
        ("chain", "internals", "hidden", "root", ["langsmith:hidden"]),
        ("tool", "check_inventory", "tool", "hidden"),
    ])

    assert "internals" not in handler.emitted_names()
    assert handler.get_node(ids["tool"]).parent_run_id == ids["root"]
    assert handler.orphans() == []


async def test_use_case_span_survives_even_nested_under_plumbing():
    cb, handler = _callback()
    await _replay(cb, [
        ("chain", "fulfillment.workflow", "root", None),
        ("chain", "fulfillment.route_after_fraud_decision", "route", "root"),
        ("chain", "fulfillment.decide_fraud_allow_or_block", "uc", "route"),
        ("chain", "fulfillment.decide_fraud_allow_or_block", "uc2", "uc"),
    ])

    names = handler.emitted_names()
    assert "fulfillment.route_after_fraud_decision" not in names
    # The denylist wins even over the name-equals-parent rule.
    assert names.count("fulfillment.decide_fraud_allow_or_block") == 2


async def test_root_chain_is_emitted_even_with_a_plumbing_name():
    cb, handler = _callback()
    await _replay(cb, [
        ("chain", "RunnableSequence", "root", None),
        ("tool", "get_price", "tool", "root"),
    ])
    assert handler.emitted_names() == ["RunnableSequence", "get_price"]


async def test_callback_emits_the_span_when_the_policy_raises(monkeypatch):
    from app.obs import galileo_callback

    def boom(*_args, **_kwargs):
        raise RuntimeError("broken policy")

    monkeypatch.setattr(galileo_callback, "suppress", boom)
    cb, handler = _callback()
    ids = await _replay(cb, [
        ("chain", "chat.workflow", "root", None),
        ("chain", "chat_pick_next_specialist", "pick", "root"),
        ("tool", "check_inventory", "tool", "pick"),
    ])

    # No span lost: without the policy, the trace reverts to what it was before this phase.
    assert "chat_pick_next_specialist" in handler.emitted_names()
    assert handler.get_node(ids["tool"]).parent_run_id == ids["pick"]
    assert handler.orphans() == []


# --- F-022 (D.2): cache turns into metadata --------------------------------------

async def test_check_response_cache_tool_span_is_suppressed_on_hit():
    cb, handler = _callback()
    ids = await _replay(cb, [
        ("chain", "feature.answer_store_policy", "root", None),
        ("tool", "check_response_cache", "tool", "root",
         None, {"response_cache": "hit", "model": "gpt-4o-mini", "provider": "openai"}),
        ("chain", "feature.answer_store_policy.replay_cached_response", "replay", "root"),
    ])

    names = handler.emitted_names()
    assert "check_response_cache" not in names
    assert "feature.answer_store_policy.replay_cached_response" not in names
    assert names == ["feature.answer_store_policy"]

    meta = handler.get_node(ids["root"]).span_params.get("metadata")
    assert meta == {
        "response_cache": "hit",
        "cache_hit": True,
        "model": "gpt-4o-mini",
        "provider": "openai",
    }


async def test_check_response_cache_tool_span_is_suppressed_on_miss():
    cb, handler = _callback()
    ids = await _replay(cb, [
        ("chain", "feature.cart_crosssell", "root", None),
        ("chain", "feature.cart_crosssell.check_response_cache", "wrapper", "root",
         None, {"response_cache": "miss"}),
        ("tool", "check_response_cache", "tool", "wrapper",
         None, {"response_cache": "miss"}),
        ("chain", "feature.cart_crosssell.invoke_llm", "inv", "root"),
        ("chat", "feature.cart_crosssell", "llm", "inv"),
    ])

    names = handler.emitted_names()
    assert "check_response_cache" not in names
    assert "feature.cart_crosssell.check_response_cache" not in names
    assert handler.orphans() == []
    # The suppressed tool promotes directly to the effective parent (the wrapper was already suppressed).
    assert handler.get_node(ids["inv"]).parent_run_id == ids["root"]

    meta = handler.get_node(ids["root"]).span_params.get("metadata")
    assert meta == {"response_cache": "miss", "cache_hit": False}


async def test_check_response_cache_without_cache_metadata_is_not_merged():
    # Without `response_cache` in the surrounding metadata, there's nothing to record — the
    # suppression itself (via `_SUPPRESSED_SEGMENTS`) still applies, but the merge is a safe no-op.
    cb, handler = _callback()
    ids = await _replay(cb, [
        ("chain", "feature.cart_crosssell", "root", None),
        ("tool", "check_response_cache", "tool", "root"),
    ])

    assert "check_response_cache" not in handler.emitted_names()
    assert handler.get_node(ids["root"]).span_params.get("metadata") is None


async def test_chain_end_survives_a_span_that_was_never_emitted():
    cb, handler = _callback()
    ids = await _replay(cb, [
        ("chain", "chat.workflow", "root", None),
        ("chain", "chat_pick_next_specialist", "pick", "root"),
    ])
    # `on_chain_end` of a suppressed span must not raise (nor turn into a root commit).
    await cb.on_chain_end({"ok": True}, run_id=ids["pick"], parent_run_id=ids["root"])
    await cb.on_chain_end({"answer": "hi"}, run_id=ids["root"], parent_run_id=None)
    assert handler.get_node(ids["root"]).span_params.get("output")


# =============================================================================
# 3. Span budget (D.4) — chat ≤12, fulfillment ≤14, retriever/tool ≤2 levels
# =============================================================================
#
# This block does NOT depend on the live server or the real Galileo: it reproduces, via
# `FakeHandler` + `VegaGalileoCallback` (the same pair used above), the tree the callback would
# actually produce for the `chat` and `fulfillment` graphs. `CHAT_TREE` already exists (section 2,
# spied from `build_chat_graph()`); `FULFILLMENT_TREE` below is the equivalent for
# `build_fulfillment_graph()` — measured live on 2026-08-06 via `/api/orders` +
# `galileo.search.get_spans` against a real trace (SPD-D.4), 19 nodes before this step. The
# fixture uses 1 call to `get_price` (the graph's "normal" script: 1 SKU in the cart → 1
# `check_inventory` + 1 `get_price`).
#
# The live measurement occasionally showed 2 calls to `get_price` (one of them with the SKU from
# a previous order) — attributed at the time to "ReAct agent behavior/LLM cache". F-BACKEND-4
# (#72) identified the real cause: the initial `HumanMessage` never persisted in the state
# between turns, so the stub fell back to the hardcoded `sku="NS-001"`, `resolve_quote_node`
# discarded the wrong result and redid the call with the correct SKU — 1 turn + 1 span wasted per
# checkout. With the human message seeded (`seed_initial_messages`), that fallback became an
# exception; `test_fulfillment_span_budget_with_non_default_sku` in `test_react_contract.py`
# locks in the normal script (1 `check_inventory` + 1 `get_price`, no "Resolve quote
# fallback"/"discard") via `SpanSpy` against the real graph, which this block (hardcoded fixture)
# does not cover.
FULFILLMENT_TREE = [
    ("chain", "fulfillment.workflow", "root", None),
    ("chain", "fulfillment.verify_cart_inventory_and_price", "verify", "root"),
    ("chat", "fulfillment.verify_cart_inventory_and_price", "verify_llm", "verify"),
    ("chain", "fulfillment.run_checkout_tools", "tools_wrap", "root"),
    ("tool", "get_price", "get_price", "tools_wrap"),
    ("tool", "check_inventory", "check_inventory", "tools_wrap"),
    ("chain", "fulfillment.resolve_checkout_quote", "resolve_quote", "root"),
    ("chain", "fulfillment.decide_fraud_allow_or_block", "fraud", "root"),
    ("tool", "decide_fraud_allow_or_block", "fraud_tool", "fraud"),
    ("chat", "fulfillment.decide_fraud_allow_or_block", "fraud_llm", "fraud_tool"),
    ("chain", "fulfillment.confirm_cart_stock", "stock", "root"),
    ("tool", "confirm_cart_stock", "stock_tool", "stock"),
    ("chain", "fulfillment.charge_payment", "charge", "root"),
    ("tool", "charge_payment", "charge_tool", "charge"),
    ("chain", "fulfillment.decrement_catalog_stock", "decrement", "root"),
    ("chain", "fulfillment.persist_order_status", "persist", "root"),
    ("chain", "fulfillment.send_order_notification", "notify", "root"),
    ("tool", "send_order_notification", "notify_tool", "notify"),
]

CHAT_SPAN_BUDGET = 12
FULFILLMENT_SPAN_BUDGET = 14
MAX_RETRIEVER_TOOL_DEPTH = 2

_FORBIDDEN_SURVIVING_NAMES = ("route_after_", "Runnable", "ChatPromptTemplate")


def _forbidden_names(names: list[str]) -> list[str]:
    return [n for n in names if any(bad in n for bad in _FORBIDDEN_SURVIVING_NAMES)]


@pytest.mark.parametrize("tree,budget,label", [
    (CHAT_TREE, CHAT_SPAN_BUDGET, "chat"),
    (FULFILLMENT_TREE, FULFILLMENT_SPAN_BUDGET, "fulfillment"),
])
async def test_span_budget_is_respected(tree, budget, label):
    cb, handler = _callback()
    await _replay(cb, tree)
    emitted = handler.emitted_names()
    assert len(emitted) <= budget, (
        f"{label}: {len(emitted)} spans emitted (target ≤{budget}) — {emitted}"
    )


@pytest.mark.parametrize("tree,label", [(CHAT_TREE, "chat"), (FULFILLMENT_TREE, "fulfillment")])
async def test_no_plumbing_names_survive_in_the_real_graphs(tree, label):
    cb, handler = _callback()
    await _replay(cb, tree)
    bad = _forbidden_names(handler.emitted_names())
    assert bad == [], f"{label}: plumbing names survived — {bad}"


async def test_chat_retriever_is_within_two_levels_of_the_root():
    cb, handler = _callback()
    await _replay(cb, CHAT_TREE)
    depth = handler.depth_of("retrieve_store_policies")
    assert depth is not None and depth <= MAX_RETRIEVER_TOOL_DEPTH, depth


async def test_fulfillment_tools_are_within_two_levels_of_the_root():
    cb, handler = _callback()
    await _replay(cb, FULFILLMENT_TREE)
    for tool_name in ("get_price", "check_inventory", "confirm_cart_stock", "charge_payment"):
        depth = handler.depth_of(tool_name)
        assert depth is not None and depth <= MAX_RETRIEVER_TOOL_DEPTH, (tool_name, depth)


async def test_fulfillment_tree_keeps_only_the_denylisted_business_nodes_and_get_price():
    # Checks WHO survives, not just the count: the non-negotiable guarantees (denylist) plus
    # `get_price` (the graph's only non-protected tool, kept because it carries real business
    # data — the quoted price — not plumbing).
    cb, handler = _callback()
    await _replay(cb, FULFILLMENT_TREE)
    names = handler.emitted_names()
    for kept in (
        "fulfillment.workflow",
        "fulfillment.verify_cart_inventory_and_price",
        "get_price",
        "check_inventory",
        "fulfillment.decide_fraud_allow_or_block",
        "decide_fraud_allow_or_block",
        "fulfillment.confirm_cart_stock",
        "confirm_cart_stock",
        "fulfillment.charge_payment",
        "charge_payment",
        "fulfillment.send_order_notification",
        "send_order_notification",
    ):
        assert kept in names, (kept, names)
    for dropped in (
        "fulfillment.run_checkout_tools",
        "fulfillment.resolve_checkout_quote",
        "fulfillment.decrement_catalog_stock",
        "fulfillment.persist_order_status",
    ):
        assert dropped not in names, (dropped, names)
    assert handler.orphans() == []


# =============================================================================
# 4. Budget measured against real execution (F-BACKEND-4, Step 3)
# =============================================================================
#
# Sections 2/3 replay `FULFILLMENT_TREE` — a hardcoded tree. It locks in suppression/reparenting,
# but doesn't catch a COUNT regression: if the graph started running 1 extra turn, the fixture
# would stay the same and the test would stay green. Here we attach
# `VegaGalileoCallback(FakeHandler())` — an ordinary LangChain callback — to a REAL execution of
# the fulfillment graph (`config["callbacks"]`) and count `handler.emitted_names()` for real,
# which is `FULFILLMENT_SPAN_BUDGET` measuring the graph, not a replayed fiction.

from app.ai_agents.fulfillment_workflow import build_fulfillment_workflow  # noqa: E402
from app.runnable_config import build_runnable_config, make_thread_id  # noqa: E402
from app.store import orders  # noqa: E402
from app.store.tools import CATALOG  # noqa: E402

async def _run_fulfillment_with_real_callback(products: list[dict], *, order: dict | None = None):
    orders.init_db()
    items = [{"sku": p["sku"], "name": p["name"], "qty": 1, "price": p["price"]} for p in products]
    total = sum(p["price"] for p in products)
    cb, handler = _callback()
    cfg = {
        **build_runnable_config(thread_id=make_thread_id(), feature="fulfillment"),
        "callbacks": [cb],
    }
    payload: dict = {"items": items, "total": total, "inventory": [], "item_index": 0}
    if order is not None:
        payload["order"] = order
    result = await build_fulfillment_workflow().ainvoke(payload, config=cfg)
    return handler, result


async def test_fulfillment_measured_span_count_justifies_the_budget():
    handler, _ = await _run_fulfillment_with_real_callback([CATALOG[2]])
    emitted = handler.emitted_names()
    assert len(emitted) <= FULFILLMENT_SPAN_BUDGET, (
        f"measured {len(emitted)} spans (budget {FULFILLMENT_SPAN_BUDGET}) — {emitted}"
    )


async def test_fulfillment_measured_span_count_with_multi_sku_cart():
    """Cart with 2 items — locks in the extra turn's cost so it doesn't regress unnoticed.

    The stub extracts only the 1st SKU (`\\b(NS-\\d+)\\b`) from the human message and resolves
    inventory/quote for it; with 2 SKUs in the cart, `_cart_tools_satisfied` never closes (only
    the 1st SKU has a tool result), so the coordinator takes 1 extra turn (chain `verify` + LLM
    `VegaStubChatModel`, no tool_calls) before falling into the `tools_condition` fallback →
    `resolve_quote` — 2 more spans than the 1-SKU cart, without repeating
    `check_inventory`/`get_price` (a known stub limitation — it doesn't iterate per SKU;
    `resolve_quote_node` also only normalizes `cart_skus[0]`). This doesn't claim it's the correct
    behavior for production (a real LLM would call the tools per SKU) — only that, under the
    stub, this is the cost. A change that makes the stub iterate per SKU needs to update this test
    consciously.
    """
    single, _ = await _run_fulfillment_with_real_callback([CATALOG[2]])
    multi, _ = await _run_fulfillment_with_real_callback([CATALOG[2], CATALOG[3]])
    single_emitted, multi_emitted = single.emitted_names(), multi.emitted_names()

    assert multi_emitted.count("check_inventory") == 2, multi_emitted
    assert multi_emitted.count("get_price") == 1, multi_emitted
    assert len(multi_emitted) > len(single_emitted), (single_emitted, multi_emitted)
    assert len(multi_emitted) <= FULFILLMENT_SPAN_BUDGET, (
        f"measured {len(multi_emitted)} spans (budget {FULFILLMENT_SPAN_BUDGET}) — {multi_emitted}"
    )


async def test_inventory_outage_does_not_loop_the_coordinator(reset_problem_flags):
    """UC-2 (F-WORKSHOP-STAB-4): a `check_inventory` error used to be handed back to the agent in
    a loop — in the extra rounds the LLM invents a SKU outside the cart. `_route_after_checkout_tools`
    now treats a tool error as the outcome, straight to `resolve_quote` — this test used to
    reproduce the bug before Step 5 (`get_price` > 1, budget blown)."""
    orders.init_db()
    product = CATALOG[2]
    item = {"sku": product["sku"], "name": product["name"], "qty": 1, "price": product["price"]}
    order = orders.create_order([item], {"name": "Span Demo", "email": "span@vega.sim"}, product["price"], "PENDING")

    reset_problem_flags.inventory_outage = True
    handler, result = await _run_fulfillment_with_real_callback([product], order=order)
    emitted = handler.emitted_names()

    assert emitted.count("get_price") <= 1, emitted
    assert len(emitted) <= FULFILLMENT_SPAN_BUDGET, (
        f"measured {len(emitted)} spans (budget {FULFILLMENT_SPAN_BUDGET}) — {emitted}"
    )
    assert result["status"] == "FAILED", result
    assert result["failure_reason"] == "inventory_unavailable", result


from app.ai_agents.notification_copy import compose_notification_text  # noqa: E402
from app.ai_agents.product_qa import answer_product_question  # noqa: E402
from app.ai_agents.store_compare import compare_products  # noqa: E402


def test_compare_tools_are_not_orphaned_in_the_real_callback():
    cb, handler = _callback()
    cfg = {
        **build_runnable_config(thread_id=make_thread_id(), feature="compare"),
        "callbacks": [cb],
    }
    result = compare_products("NS-001", "NS-002", config=cfg)
    assert result and result["verdict"]
    assert handler.orphans() == [], handler.emitted_names()
    assert handler.depth_of("get_price") is not None
    assert handler.emitted_names().count("get_price") >= 2
    assert handler.depth_of("retrieve_catalog") is not None
    assert handler.depth_of("compare.gather_product_context") == 1
    assert handler.depth_of("compare.retrieve_catalog_context") == 1
    assert handler.depth_of("compare.fetch_prices_for_comparison") == 1
    assert handler.depth_of("compare.compose_shopper_verdict") == 1
    assert handler.depth_of("feature.write_comparison_verdict") == 2
    assert handler.emitted_names().count("feature.write_comparison_verdict") == 1


def test_product_qa_tools_and_retrievers_are_not_orphaned_in_the_real_callback():
    cb, handler = _callback()
    cfg = {
        **build_runnable_config(thread_id=make_thread_id(), feature="product_qa"),
        "callbacks": [cb],
    }
    result = answer_product_question("NS-001", "How many days do I have to return this?", config=cfg)
    assert result and result["grounded"] is True
    assert handler.orphans() == [], handler.emitted_names()
    assert handler.depth_of("search_policies") is not None
    assert handler.depth_of("retrieve_store_policies") is not None
    assert handler.depth_of("retrieve_catalog") is not None


def test_notification_copy_trace_is_not_orphaned_in_the_real_callback():
    cb, handler = _callback()
    cfg = {
        **build_runnable_config(thread_id=make_thread_id(), feature="notification_copy"),
        "callbacks": [cb],
    }
    order = {
        "id": "ORD-NOTIFY-TRACE",
        "status": "PAID",
        "items": [{"sku": CATALOG[0]["sku"], "qty": 1, "name": CATALOG[0]["name"]}],
        "total": CATALOG[0]["price"],
        "customer": {
            "name": "Jane Doe",
            "email": "jane@example.test",
            "address": "123 Main St",
            "ssn": "123-45-6789",
            "card_number": "4242 4242 4242 4242",
        },
    }
    result = compose_notification_text(order, config=cfg)
    assert result["subject"] and result["body"]
    assert handler.orphans() == [], handler.emitted_names()
    assert handler.depth_of("notification_copy.workflow") == 0
    assert handler.depth_of("notification_copy.gather_order_context") == 1
    assert handler.depth_of("notification_copy.compose_email") == 1
    assert handler.depth_of("feature.compose_notification_text") == 2
