"""Política pura de supressão/reparenting de spans (F-BACKEND-3, Etapa D.1).

O trace do workshop tem que **caber numa tela**: hoje o chat exporta ~22 spans e o fulfillment
~27, com plumbing do LangGraph/LCEL (roteadores de aresta condicional, `RunnableSequence`,
wrappers de RAG, aninhamentos 3-4× do mesmo nome) empurrando `[retriever]`/`[tool]` pra 4-5
níveis de profundidade — longe do padrão dos exemplos de referência.

Este módulo decide **só** isso: dado o nome do span e o nome do pai já emitido, o span entra no
trace ou não. Quem aplica a decisão (e faz o reparenting dos filhos do span suprimido) é o
`VegaGalileoCallback`. Sem I/O, sem SDK, sem estado — dá pra congelar em teste.

Duas garantias inegociáveis:

1. **Denylist dos UCs.** Os spans que os use cases 1-5 do workshop mandam o participante abrir no
   Console (`docs/reference/workshop-use-cases.md`) **nunca** são supríveis, aconteça o que
   acontecer com as regras genéricas. Um trace bonito que perdeu o `check_inventory` da UC-2 é um
   workshop quebrado.
2. **Falha = sem supressão.** Qualquer exceção aqui devolve `False` (emite o span). Observabilidade
   não derruba a loja, e nem sequer degrada o trace: no pior caso ele volta a ser o de antes.

Tag `langsmith:hidden` NÃO substitui isto: no galileo==2.6.0 ela esconde o span mas **orfaniza** a
subárvore (o filho aponta pra um pai que nunca entrou na árvore e some do commit), então só serve
pra folha. Daí a supressão ser feita aqui, com reparenting explícito no callback.
"""
from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


# --- denylist congelada -------------------------------------------------------

# Fonte: `docs/reference/workshop-use-cases.md` (spans primários das UC-1..UC-5 e os step names
# do apêndice de Agent Control) + os rótulos correspondentes em `app/galileo_span.py`. Cobre tanto
# o nome do nó do grafo (`returns.check_refund_eligibility`) quanto o da tool (`check_refund_eligibility`),
# porque a checagem também olha o último segmento pontuado.
PROTECTED_SPAN_NAMES: frozenset[str] = frozenset({
    # UC-1 — preço inventado
    "product_qa",
    "answer_product_question",
    # UC-2 — falha de estoque no checkout
    "check_inventory",
    "confirm_cart_stock",
    "verify_cart_inventory_and_price",
    # UC-3 — refund negado por engano
    "returns.finalize",
    "coordinate_refund_request",
    "check_refund_eligibility",
    "screen_refund_abuse",
    "process_refund",
    # UC-4 — prompt injection (mutação destrutiva e vazamento de PII no caminho do shopper)
    "delete_product",
    "list_recent_customers",
    "search",
    "semantic_product_search",
    # UC-5 — PII na cópia da notificação
    "notification_copy",
    "compose_notification_text",
    "send_order_notification",
    # Fraude/pagamento — spans de negócio do checkout citados nas UC-2/UC-3
    "decide_fraud_allow_or_block",
    "charge_payment",
})

# --- regras de supressão ------------------------------------------------------

# Nomes crus de classe LCEL — o Console mostra a classe, não o passo de negócio.
_RAW_LCEL_NAMES: frozenset[str] = frozenset({
    "ChatPromptTemplate",
    "StrOutputParser",
})
_RAW_LCEL_PREFIXES: tuple[str, ...] = ("Runnable",)

# Plumbing de grafo e wrappers de preparação — último segmento do nome pontuado.
_SUPPRESSED_SEGMENTS: frozenset[str] = frozenset({
    "tools_condition",             # aresta condicional do ReAct
    "prepare_messages",            # `feature.<step>.prepare_messages`
    "replay_cached_response",      # `feature.<step>.replay_cached_response`
    # F-022 vira metadata no span ancestral (D.2) em vez de span próprio — cobre tanto o
    # wrapper LCEL (`feature.<step>.check_response_cache`) quanto a tool crua invocada dentro
    # dele; o `VegaGalileoCallback` grava `cache_hit`/`response_cache` no pai efetivo antes de
    # suprimir (ver `_merge_cache_metadata`).
    "check_response_cache",
    # Wrappers estruturais do grafo `fulfillment` (D.4 — medição ao vivo: 19 nós, meta ≤14).
    # Nenhum destes é uma decisão de negócio nem aparece em `docs/reference/workshop-use-cases.md`;
    # são glue/bookkeeping em torno dos nós protegidos (check_inventory, decide_fraud_allow_or_block,
    # confirm_cart_stock, charge_payment, send_order_notification), que continuam intactos.
    "run_checkout_tools",          # `ToolNode` puro do ReAct — as tool calls (get_price/
                                    # check_inventory) promovem pro pai efetivo, não somem.
    "resolve_checkout_quote",      # normaliza inventory/quote do histórico de mensagens; quando
                                    # aciona o fallback de SKU errado, a tool chamada de novo
                                    # ainda aparece (reparentada), só a etiqueta do wrapper some.
    "decrement_catalog_stock",     # bookkeeping pós-pagamento, sem branch de negócio.
    "persist_order_status",        # grava o status final; a decisão que o gerou (fraude/estoque/
                                    # pagamento) já está visível nos nós protegidos correspondentes.
})

_ROUTE_PREFIXES: tuple[str, ...] = ("route_after_", "_route_after_")
# `chat_pick_next_specialist` e o gêmeo `concierge_pick_next_specialist` — a spec nomeia o do
# chat, mas é a mesma aresta condicional; o sufixo pega os dois (e o próximo grafo que copiar
# o padrão) sem virar caça a nome literal.
_ROUTE_SUFFIXES: tuple[str, ...] = ("_pick_next_specialist",)


def _last_segment(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def is_protected(name: str | None) -> bool:
    """True quando o span é um dos que os UCs do workshop precisam ver no Console."""
    try:
        raw = (name or "").strip()
        if not raw:
            return False
        return raw in PROTECTED_SPAN_NAMES or _last_segment(raw) in PROTECTED_SPAN_NAMES
    except Exception as exc:  # noqa: BLE001 — política nunca levanta
        _logger.warning("span policy: is_protected falhou para %r (%s)", name, exc)
        return True  # na dúvida, protege


def _is_raw_lcel(name: str) -> bool:
    return name in _RAW_LCEL_NAMES or name.startswith(_RAW_LCEL_PREFIXES)


def _is_graph_plumbing(segment: str) -> bool:
    if segment in _SUPPRESSED_SEGMENTS:
        return True
    return segment.startswith(_ROUTE_PREFIXES) or segment.endswith(_ROUTE_SUFFIXES)


def _is_context_wrapper(segment: str) -> bool:
    """`feature.merge_*_context` / `feature.retrieve_*_for_context` — só embrulham o retriever.

    Suprimi-los é o que promove o span `[retriever]` pra perto da raiz.
    """
    if segment.startswith("merge_") and segment.endswith("_context"):
        return True
    return segment.startswith("retrieve_") and segment.endswith("_for_context")


def suppress(name: str | None, parent_name: str | None = None) -> bool:
    """Decide se o span `name`, filho do span já emitido `parent_name`, deve sair do trace.

    `parent_name is None` = raiz do trace (ou pai efetivo desconhecido): **nunca** suprime — sem
    raiz o SDK não tem onde pendurar a árvore e o trace inteiro se perde.
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
        # Aninhamento idêntico ao pai (o LCEL repete o mesmo `run_name` 3-4× em profundidade):
        # o primeiro da cadeia sobrevive, os de baixo somem. D.4 estende a comparação pro ÚLTIMO
        # SEGMENTO do nome: `feature.answer_store_policy` sob `chat.answer_store_policy` é o
        # mesmo passo de negócio com prefixo de namespace diferente (grafo vs. LCEL da feature) —
        # sem isso o retriever fica a 3 níveis da raiz (meta é ≤2). Nomes protegidos já saíram
        # antes (`is_protected` acima), então isto nunca reduz um nó dos UCs 1-5.
        if not parent_name:
            return False
        parent_raw = parent_name.strip()
        if raw == parent_raw:
            return True
        return bool(segment) and segment == _last_segment(parent_raw)
    except Exception as exc:  # noqa: BLE001 — fallback SEM supressão
        _logger.warning("span policy: suppress falhou para %r (%s)", name, exc)
        return False
