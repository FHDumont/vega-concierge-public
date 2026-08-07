"""Política de supressão/reparenting de spans (F-BACKEND-3, Etapa D.1).

Duas camadas:

* `app.obs.galileo_span_policy` — função pura `suppress(name, parent_name)`. Aqui mora a
  **denylist congelada**: os spans que as UC-1..UC-5 mandam abrir no Console
  (`docs/reference/workshop-use-cases.md`) não podem sumir do trace por causa de uma regra
  genérica futura.
* `app.obs.galileo_callback.VegaGalileoCallback` — aplica a decisão e faz o reparenting. Testado
  contra um `FakeHandler` que reproduz a contabilidade de nós do SDK (`galileo==2.6.0`): mesmo
  `Node`, mesma indexação por `str(run_id)`, mesma lista de `children`. É o que permite afirmar
  que o `[retriever]` sobe de nível em vez de virar órfão.
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
# 1. Política pura
# =============================================================================

@pytest.mark.parametrize("name", [
    # arestas condicionais do LangGraph
    "fulfillment.route_after_checkout_tools",
    "fulfillment.route_after_fraud_decision",
    "fulfillment.route_after_coordinator_tools",
    "returns.route_after_abuse_screen",
    "route_after_coordinator_tools",
    "_route_after_payment",
    "chat_pick_next_specialist",
    "concierge_pick_next_specialist",
    "tools_condition",
    # classes LCEL cruas
    "ChatPromptTemplate",
    "StrOutputParser",
    "RunnableSequence",
    "RunnableLambda",
    "RunnableAssign",
    "RunnableParallel<context,question>",
    # wrappers de preparação/RAG
    "feature.merge_policy_context",
    "feature.merge_catalog_context",
    "feature.merge_static_context",
    "feature.retrieve_policies_for_context",
    "feature.retrieve_catalog_for_context",
    "feature.answer_store_policy.prepare_messages",
    "feature.answer_store_policy.replay_cached_response",
    # F-022 (D.2) — cache vira metadata no ancestral, o span some
    "check_response_cache",
    "feature.answer_store_policy.check_response_cache",
    # Wrappers estruturais do grafo `fulfillment` (D.4 — medição ao vivo: 19 nós, meta ≤14).
    "fulfillment.run_checkout_tools",
    "fulfillment.resolve_checkout_quote",
    "fulfillment.decrement_catalog_stock",
    "fulfillment.persist_order_status",
])
def test_plumbing_spans_are_suppressed(name):
    assert suppress(name, "chat.workflow") is True


def test_identical_nesting_keeps_only_the_outermost_span():
    # O LCEL repete o mesmo `run_name` 3-4× em profundidade; o primeiro da cadeia sobrevive.
    assert suppress("chat.assemble_shopper_reply", "chat.assemble_shopper_reply") is True


def test_last_segment_match_collapses_namespace_prefix_duplicates():
    # D.4: `feature.answer_store_policy` (LCEL da feature) sob `chat.answer_store_policy` (nó do
    # grafo) é o MESMO passo de negócio com prefixo de namespace diferente — sem isto o
    # `[retriever]` fica a 3 níveis da raiz (meta é ≤2). Nomes de UC continuam imunes (checado
    # em `test_use_case_spans_are_never_suppressed`, que já cobre `suppress(name, name)`).
    assert suppress("feature.answer_store_policy", "chat.answer_store_policy") is True
    assert suppress("feature.answer_store_policy", "feature.answer_store_policy") is True
    # Segmentos diferentes não colidem por engano.
    assert suppress("feature.answer_store_policy.invoke_llm", "feature.answer_store_policy") is False
    assert suppress("chat.route_decision", "chat.route_shopper_request") is False


@pytest.mark.parametrize("name", [
    "chat.workflow",
    "fulfillment.workflow",
    "chat.route_shopper_request",
    "chat.route_decision",          # decisão de negócio — não é `route_after_*`
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
    # Sem raiz o SDK não tem onde pendurar a árvore: o trace inteiro se perde.
    assert suppress("RunnableSequence", None) is False
    assert suppress("chat_pick_next_specialist", None) is False


@pytest.mark.parametrize("name", ["", "   ", None])
def test_empty_names_are_not_suppressed(name):
    assert suppress(name, "chat.workflow") is False


# --- denylist congelada -------------------------------------------------------

# Fonte: `docs/reference/workshop-use-cases.md` (spans primários + step names do Agent Control).
# Congelado de propósito: mexer aqui é mexer no workshop.
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
    assert name in PROTECTED_SPAN_NAMES, f"{uc}: {name} saiu da denylist"
    assert is_protected(name), name


@pytest.mark.parametrize("uc,name", UC_SPAN_NAMES)
def test_use_case_spans_are_never_suppressed(uc, name):
    # Nem como nó de grafo pontuado, nem sob a regra genérica de nome igual ao pai, nem se um dia
    # alguém batizar um nó de UC com prefixo de plumbing.
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
        raise RuntimeError("política quebrada")

    monkeypatch.setattr(policy, "_last_segment", boom)
    # Sem o fallback isto levantaria e derrubaria o `on_chain_start` (e o request junto).
    assert policy.suppress("RunnableSequence", "chat.workflow") is False
    assert policy.suppress("chat_pick_next_specialist", "chat.workflow") is False


def test_is_protected_errs_on_the_side_of_protection(monkeypatch):
    def boom(_name):
        raise RuntimeError("política quebrada")

    monkeypatch.setattr(policy, "_last_segment", boom)
    assert policy.is_protected("check_inventory") is True


# =============================================================================
# 2. Callback — supressão + reparenting
# =============================================================================

class FakeHandler:
    """Contabilidade de nós do `GalileoBaseHandler` (galileo==2.6.0), sem rede nem logger.

    Só o suficiente pro `GalileoAsyncCallback` rodar: `_nodes` indexado por `str(run_id)`,
    `children` por ordem de chegada, raiz = primeiro nó iniciado.
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

    # --- consultas de teste ---------------------------------------------------

    def name_of(self, run_id) -> str:
        node = self._nodes.get(str(run_id))
        return "" if node is None else str(node.span_params.get("name") or "")

    def emitted_names(self) -> list[str]:
        return [str(n.span_params.get("name") or "") for n in self._nodes.values()]

    def tree(self) -> list[tuple[int, str]]:
        """(profundidade, nome) percorrendo a partir da raiz — só o que o commit exportaria."""
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
    """Reproduz `(kind, name, key, parent_key[, tags[, metadata]])` como o LangChain entregaria."""
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
        else:  # pragma: no cover — erro de escrita de teste
            raise AssertionError(kind)
    return ids


# Árvore observada no grafo `chat` (dump do callback espião sobre `build_chat_graph()`).
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
    # Sobrevivem: raiz, nós de negócio e o retriever. `feature.answer_store_policy` (o wrapper
    # LCEL, `kind="chain"`) some pelo colapso de último-segmento (D.4) — o que sobra com esse
    # nome é só o span `chat`/leaf do modelo, que nunca é suprimido.
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

    # D.4: `feature.answer_store_policy` (o wrapper LCEL da feature) agora some junto com
    # `merge_policy_context`/`retrieve_policies_for_context` — mesmo último segmento que
    # `chat.answer_store_policy` (o nó do grafo). O retriever pendura direto no nó do grafo,
    # a 2 níveis da raiz (meta da Etapa D: `[retriever]`/`[tool]` a ≤2 níveis).
    assert handler.get_node(ids["retriever"]).parent_run_id == ids["policy"]
    assert handler.depth_of("retrieve_store_policies") == 2
    assert handler.orphans() == []


async def test_reparenting_resolves_a_chain_of_dropped_parents():
    cb, handler = _callback()
    ids = await _replay(cb, [
        ("chain", "returns.workflow", "root", None),
        ("chain", "returns.coordinate_refund_request", "node", "root"),
        ("chain", "RunnableSequence", "a", "node"),          # suprimido
        ("chain", "feature.merge_policy_context", "b", "a"),  # suprimido, pai já suprimido
        ("chain", "ChatPromptTemplate", "c", "b"),            # suprimido, 3º da cadeia
        ("tool", "check_refund_eligibility", "tool", "c"),
        ("chat", "returns.check_refund_eligibility", "llm", "c"),
    ])

    # A tool sobe três níveis de uma vez, até o único ancestral que sobreviveu.
    assert handler.get_node(ids["tool"]).parent_run_id == ids["node"]
    assert handler.get_node(ids["llm"]).parent_run_id == ids["node"]
    assert handler.depth_of("check_refund_eligibility") == 2
    assert handler.orphans() == []
    assert cb._dropped[ids["c"]] == ids["node"]


async def test_hidden_tag_reparents_instead_of_orphaning_the_subtree():
    # `langsmith:hidden` no SDK esconde o span mas deixa os filhos apontando pra um pai que nunca
    # entrou na árvore — some tudo no commit. Aqui o filho sobe pro pai efetivo.
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
    # Denylist vence até a regra de nome igual ao pai.
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
        raise RuntimeError("política quebrada")

    monkeypatch.setattr(galileo_callback, "suppress", boom)
    cb, handler = _callback()
    ids = await _replay(cb, [
        ("chain", "chat.workflow", "root", None),
        ("chain", "chat_pick_next_specialist", "pick", "root"),
        ("tool", "check_inventory", "tool", "pick"),
    ])

    # Nada de span perdido: sem política, o trace volta a ser o de antes desta fase.
    assert "chat_pick_next_specialist" in handler.emitted_names()
    assert handler.get_node(ids["tool"]).parent_run_id == ids["pick"]
    assert handler.orphans() == []


# --- F-022 (D.2): cache vira metadata --------------------------------------

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
    # A tool suprimida promove diretamente pro pai efetivo (o wrapper já foi suprimido antes).
    assert handler.get_node(ids["inv"]).parent_run_id == ids["root"]

    meta = handler.get_node(ids["root"]).span_params.get("metadata")
    assert meta == {"response_cache": "miss", "cache_hit": False}


async def test_check_response_cache_without_cache_metadata_is_not_merged():
    # Sem `response_cache` no metadata ambiente, não há o que gravar — a supressão em si
    # (via `_SUPPRESSED_SEGMENTS`) ainda vale, mas o merge é um no-op seguro.
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
    # `on_chain_end` do span suprimido não pode levantar (nem virar commit de raiz).
    await cb.on_chain_end({"ok": True}, run_id=ids["pick"], parent_run_id=ids["root"])
    await cb.on_chain_end({"answer": "hi"}, run_id=ids["root"], parent_run_id=None)
    assert handler.get_node(ids["root"]).span_params.get("output")


# =============================================================================
# 3. Orçamento de spans (D.4) — chat ≤12, fulfillment ≤14, retriever/tool ≤2 níveis
# =============================================================================
#
# Este bloco NÃO depende do servidor vivo nem do Galileo real: reproduz, via `FakeHandler` +
# `VegaGalileoCallback` (a mesma dupla usada acima), a árvore que o callback de fato produziria
# pros grafos `chat` e `fulfillment`. `CHAT_TREE` já existe (seção 2, espiada de
# `build_chat_graph()`); `FULFILLMENT_TREE` abaixo é o equivalente pro `build_fulfillment_graph()`
# — medido ao vivo em 2026-08-06 via `/api/orders` + `galileo.search.get_spans` contra um trace
# real (SPD-D.4), 19 nós antes desta etapa. O fixture usa 1 chamada de `get_price` (o roteiro
# "normal" do grafo: 1 SKU no carrinho → 1 `check_inventory` + 1 `get_price`).
#
# A medição ao vivo ocasionalmente mostrava 2 chamadas de `get_price` (uma delas com SKU de um
# pedido anterior) — na época atribuído a "comportamento do agente ReAct/cache de LLM". A
# F-BACKEND-4 (#72) identificou a causa real: o `HumanMessage` inicial nunca persistia no state
# entre turnos, então o stub caía no fallback hardcoded `sku="NS-001"`, `resolve_quote_node`
# descartava o resultado errado e refazia a chamada com o SKU certo — 1 turno + 1 span
# desperdiçados por checkout. Com o humano seedado (`seed_initial_messages`), esse fallback virou
# exceção; `test_fulfillment_span_budget_with_non_default_sku` em `test_react_contract.py` trava
# o roteiro normal (1 `check_inventory` + 1 `get_price`, sem "Resolve quote fallback"/"discard")
# via `SpanSpy` contra o grafo real, o que este bloco (fixture hardcoded) não cobre.
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
        f"{label}: {len(emitted)} spans emitidos (meta ≤{budget}) — {emitted}"
    )


@pytest.mark.parametrize("tree,label", [(CHAT_TREE, "chat"), (FULFILLMENT_TREE, "fulfillment")])
async def test_no_plumbing_names_survive_in_the_real_graphs(tree, label):
    cb, handler = _callback()
    await _replay(cb, tree)
    bad = _forbidden_names(handler.emitted_names())
    assert bad == [], f"{label}: nomes de plumbing sobreviveram — {bad}"


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
    # Confere QUEM sobrevive, não só a contagem: as garantias inegociáveis (denylist) mais
    # `get_price` (a única tool não-protegida do grafo, mantida porque carrega dado de negócio
    # real — preço cotado — não plumbing).
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
# 4. Orçamento medido contra execução real (F-BACKEND-4, Etapa 3)
# =============================================================================
#
# As seções 2/3 replay `FULFILLMENT_TREE` — uma árvore hardcoded. Ela trava supressão/reparenting,
# mas não pega regressão de CONTAGEM: se o grafo passasse a rodar 1 turno a mais, a fixture
# continuaria a mesma e o teste continuaria verde. Aqui anexamos `VegaGalileoCallback(FakeHandler())`
# — um callback LangChain comum — a uma execução REAL do fulfillment graph (`config["callbacks"]`)
# e contamos `handler.emitted_names()` de verdade, o que é o `FULFILLMENT_SPAN_BUDGET` medindo o
# grafo, não uma ficção replayada.

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
        f"medido {len(emitted)} spans (orçamento {FULFILLMENT_SPAN_BUDGET}) — {emitted}"
    )


async def test_fulfillment_measured_span_count_with_multi_sku_cart():
    """Carrinho com 2 itens — congela o custo do turno extra pra não regredir sem ninguém notar.

    O stub extrai só o 1º SKU (`\\b(NS-\\d+)\\b`) do humano e resolve inventory/quote pra ele; com
    2 SKUs no carrinho, `_cart_tools_satisfied` nunca fecha (só o 1º SKU tem tool result), então o
    coordinator dá 1 turno extra (chain `verify` + LLM `VegaStubChatModel`, sem tool_calls) antes
    de cair no fallback do `tools_condition` → `resolve_quote` — 2 spans a mais que o carrinho de
    1 SKU, sem repetir `check_inventory`/`get_price` (limitação conhecida do stub — não itera por
    SKU; `resolve_quote_node` também só normaliza `cart_skus[0]`). Não afirma que é o comportamento
    correto pra produção (LLM real chamaria as tools por SKU) — só que, sob stub, o custo é este.
    Uma mudança que fizer o stub iterar por SKU precisa atualizar este teste conscientemente.
    """
    single, _ = await _run_fulfillment_with_real_callback([CATALOG[2]])
    multi, _ = await _run_fulfillment_with_real_callback([CATALOG[2], CATALOG[3]])
    single_emitted, multi_emitted = single.emitted_names(), multi.emitted_names()

    assert multi_emitted.count("check_inventory") == 2, multi_emitted
    assert multi_emitted.count("get_price") == 1, multi_emitted
    assert len(multi_emitted) > len(single_emitted), (single_emitted, multi_emitted)
    assert len(multi_emitted) <= FULFILLMENT_SPAN_BUDGET, (
        f"medido {len(multi_emitted)} spans (orçamento {FULFILLMENT_SPAN_BUDGET}) — {multi_emitted}"
    )


async def test_inventory_outage_does_not_loop_the_coordinator(reset_problem_flags):
    """UC-2 (F-WORKSHOP-STAB-4): erro de `check_inventory` era devolvido pro agente em loop —
    nas rodadas extras o LLM inventa SKU fora do carrinho. `_route_after_checkout_tools` agora
    trata erro de tool como o desfecho, direto pro `resolve_quote` — este teste reproduzia o bug
    antes da Etapa 5 (`get_price` > 1, orçamento estourado)."""
    orders.init_db()
    product = CATALOG[2]
    item = {"sku": product["sku"], "name": product["name"], "qty": 1, "price": product["price"]}
    order = orders.create_order([item], {"name": "Span Demo", "email": "span@vega.sim"}, product["price"], "PENDING")

    reset_problem_flags.inventory_outage = True
    handler, result = await _run_fulfillment_with_real_callback([product], order=order)
    emitted = handler.emitted_names()

    assert emitted.count("get_price") <= 1, emitted
    assert len(emitted) <= FULFILLMENT_SPAN_BUDGET, (
        f"medido {len(emitted)} spans (orçamento {FULFILLMENT_SPAN_BUDGET}) — {emitted}"
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
