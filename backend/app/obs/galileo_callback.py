"""Splunk Agent Observability LangChain callback with compact workflow trace I/O."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from .galileo_span_policy import suppress
from .galileo_trace_compact import (
    compact_trace_payload,
    should_compact_chain_io,
    should_compact_workflow_io,
    _compact_retriever_output,
    _compact_tool_output,
)

_logger = logging.getLogger(__name__)

try:
    from galileo.handlers.langchain import GalileoAsyncCallback, GalileoCallback
    from galileo.utils.serialization import serialize_to_str
    from galileo.utils.uuid_utils import convert_uuid_if_uuid7
except ImportError:  # pragma: no cover — dev without galileo installed
    GalileoAsyncCallback = None  # type: ignore[misc, assignment]
    GalileoCallback = None  # type: ignore[misc, assignment]
    convert_uuid_if_uuid7 = None  # type: ignore[misc, assignment]
    serialize_to_str = None  # type: ignore[misc, assignment]

_HIDDEN_TAG = "langsmith:hidden"


def _workflow_name(kwargs: dict[str, Any], serialized: dict[str, Any] | None = None) -> str:
    meta = kwargs.get("metadata") or {}
    if isinstance(meta, dict):
        name = meta.get("workflow_name") or meta.get("run_name") or ""
        if name:
            return str(name)
    for candidate in (kwargs.get("name"), (serialized or {}).get("name"), (serialized or {}).get("id")):
        if candidate:
            return str(candidate)
    return ""


if GalileoAsyncCallback is not None:

    class VegaGalileoCallback(GalileoAsyncCallback):
        """GalileoAsyncCallback + Trace UX do Vega (nunca altera a execução da loja).

        Três coisas em cima do SDK:

        1. **Compactação de I/O** dos workflows LangGraph (raiz do trace só).
        2. **Supressão + reparenting** de spans de plumbing (`galileo_span_policy`): o span
           suprimido não é emitido e seus filhos são repintados no pai efetivo — o que promove
           `[retriever]`/`[tool]` pra perto da raiz em vez de orfanizá-los (é exatamente isso que
           a tag `langsmith:hidden` do SDK NÃO faz: ela esconde e quebra a subárvore).
        3. **Trace vivo** (D.3): quando o `session_scope` já abriu o trace do request
           (`start_new_trace=False`), a árvore do LangGraph é pendurada nele em vez de virar um
           trace próprio no `commit()`, e a raiz — que nasce vazia — herda o input compacto do
           primeiro workflow. É esse trace aberto que dá `current_parent()` ao Agent Control e
           faz o span `[control]` existir.

        `_dropped` é o mapa `run_id suprimido → run_id do pai efetivo`. Ele guarda a cadeia já
        resolvida, então um pai suprimido cujo pai também foi suprimido resolve em um passo. A
        instância do callback é por request (`galileo_obs.callbacks()`), então o mapa tem a vida
        de um trace.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._dropped: dict[UUID, UUID] = {}

        # --- helpers de reparenting ------------------------------------------

        @staticmethod
        def _convert(run_id: UUID | None) -> UUID | None:
            """Mesma normalização de id que o SDK faz antes de indexar `_nodes` (UUID7 → UUID4)."""
            if run_id is None:
                return None
            if convert_uuid_if_uuid7 is None:
                return run_id
            return convert_uuid_if_uuid7(run_id) or run_id

        def _effective_parent(self, parent_run_id: UUID | None) -> UUID | None:
            """Resolve a cadeia de pais suprimidos até o primeiro span realmente emitido."""
            current = self._convert(parent_run_id)
            seen: set[UUID] = set()
            while current is not None and current in self._dropped:
                if current in seen:  # defensivo: ciclo impossível, mas não trava o request
                    return None
                seen.add(current)
                current = self._dropped[current]
            return current

        def _node_name(self, run_id: UUID | None) -> str | None:
            """Nome do span já emitido — `None` quando não há span (raiz/pai desconhecido)."""
            if run_id is None:
                return None
            node = self._handler.get_node(run_id)
            if node is None:
                return None
            return str(node.span_params.get("name") or "")

        def _reparent(self, parent_run_id: UUID | None) -> UUID | None:
            """Pai a usar no `super()` — o original quando nada foi suprimido acima."""
            try:
                return self._effective_parent(parent_run_id)
            except Exception as exc:  # noqa: BLE001 — sem reparenting é melhor que sem trace
                _logger.warning("trace reparenting skipped (%s)", exc)
                return parent_run_id

        def _should_drop(
            self,
            serialized: dict[str, Any] | None,
            kwargs: dict[str, Any],
            parent_run_id: UUID | None,
            effective_parent: UUID | None,
            *,
            node_type: str = "chain",
        ) -> bool:
            if parent_run_id is None or effective_parent is None:
                return False  # raiz do trace (ou pai perdido): emitir sempre
            if not self._handler.get_nodes():
                return False  # nada emitido ainda — este span vira a raiz
            name = GalileoCallback._get_node_name(node_type, serialized, kwargs)
            if not name:
                name = _workflow_name(kwargs, serialized)
            # Pai efetivo sem nó registrado → `None` → `suppress` devolve False (árvore já
            # estranha, não é hora de podar).
            return suppress(name, self._node_name(effective_parent))

        def _merge_cache_metadata(
            self, run_id: UUID | None, metadata: dict[str, Any] | None,
        ) -> None:
            """F-022 vira metadata (D.2): quando o tool span `check_response_cache` é suprimido,
            a decisão de cache não pode simplesmente desaparecer do Console — ela é gravada no
            span ancestral (`effective_parent`) como `response_cache`/`cache_hit`, com
            model/provider/tokens quando disponíveis (hit). Só age quando o `metadata` ambiente
            carrega `response_cache` — ou seja, só no caminho do F-022, não em qualquer supressão
            de tool span."""
            if run_id is None or not isinstance(metadata, dict):
                return
            cache_status = metadata.get("response_cache")
            if cache_status is None:
                return
            try:
                node = self._handler.get_node(run_id)
                if node is None:
                    return
                extra: dict[str, Any] = {
                    "response_cache": cache_status,
                    "cache_hit": cache_status == "hit",
                }
                for key in ("model", "provider", "input_tokens", "output_tokens"):
                    if key in metadata:
                        extra[key] = metadata[key]
                existing = node.span_params.get("metadata")
                merged = dict(existing) if isinstance(existing, dict) else {}
                merged.update(extra)
                node.span_params["metadata"] = merged
            except Exception as exc:  # noqa: BLE001 — observabilidade nunca derruba a loja
                _logger.warning("trace cache metadata merge skipped (%s)", exc)

        # --- trace vivo (D.3) --------------------------------------------------

        def _live_trace(self) -> Any | None:
            """Trace aberto pelo `session_scope` — `None` no modo batch.

            No modo batch (`_start_new_trace=True`) quem cria a raiz é o `commit()` do próprio
            SDK, e não há nada pra enriquecer aqui."""
            if getattr(self._handler, "_start_new_trace", True):
                return None
            parent = self._handler._galileo_logger.current_parent()
            if parent is None or getattr(parent, "_parent", None) is not None:
                return None  # não é a raiz do trace (span aberto no meio) — não mexer
            return parent

        def _seed_live_trace_input(
            self, serialized: dict[str, Any] | None, inputs: dict[str, Any], kwargs: dict[str, Any],
        ) -> None:
            """Empresta pra raiz do trace o input do primeiro workflow LangGraph do request.

            Com trace vivo a raiz do Console passa a ser o trace aberto pelo `session_scope`,
            que nasce sem input (o request ainda não processou nada quando ele abre). O payload
            só aparece no `on_chain_start` do grafo — compactado pelas mesmas regras do output
            (`compact_trace_payload`), senão o estado inteiro do LangGraph volta pra raiz, que é
            exatamente o que a compactação existe pra evitar. O output da raiz o SDK herda do
            último filho no `conclude` (o próprio workflow, já compacto)."""
            trace = self._live_trace()
            if trace is None or getattr(trace, "input", None):
                return
            name = GalileoCallback._get_node_name("chain", serialized, kwargs)
            if not name:
                name = _workflow_name(kwargs, serialized)
            trace.input = serialize_to_str(compact_trace_payload(inputs, name=name))

        # --- chain ------------------------------------------------------------

        async def on_chain_start(
            self,
            serialized: dict[str, Any],
            inputs: dict[str, Any],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            tags: list[str] | None = None,
            **kwargs: Any,
        ) -> Any:
            if parent_run_id is None:
                # Raiz LangChain: a compactação de I/O continua ancorada aqui (`parent_run_id is
                # None`), independente de existir ou não um trace vivo acima — o trace do
                # `session_scope` não é um run do LangChain e nunca aparece como `parent_run_id`.
                try:
                    self._seed_live_trace_input(serialized, inputs, kwargs)
                except Exception as exc:  # noqa: BLE001 — raiz sem input é melhor que 500
                    _logger.warning("trace root input seeding skipped (%s)", exc)
            effective_parent = parent_run_id
            try:
                effective_parent = self._effective_parent(parent_run_id)
                hidden = bool(tags) and _HIDDEN_TAG in (tags or [])
                drop = hidden or self._should_drop(serialized, kwargs, parent_run_id, effective_parent)
                if drop and effective_parent is not None:
                    # Não emite; registra o pai efetivo pros filhos subirem um nível.
                    self._dropped[self._convert(run_id) or run_id] = effective_parent
                    return None
            except Exception as exc:  # noqa: BLE001 — fallback SEM supressão
                _logger.warning("trace span policy skipped on chain_start (%s)", exc)
                effective_parent = parent_run_id

            await super().on_chain_start(
                serialized, inputs, run_id=run_id, parent_run_id=effective_parent, tags=tags, **kwargs,
            )

        async def on_chain_end(
            self,
            outputs: dict[str, Any],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> Any:
            # LangChain only forwards `metadata`/`name` to `on_chain_start`'s kwargs, not
            # `on_chain_end`'s — so `_workflow_name(kwargs)` here is always "". The SDK already
            # recorded the resolved node name on the node it created at chain_start; read it back
            # from there instead (must happen before `super().on_chain_end()`, which clears the
            # node registry once the root node commits). The SDK also rewrites UUID7 run_ids to
            # UUID4 before storing nodes (`_nodes` is keyed by the converted id) — look up with
            # the same converted id or `get_node` always misses.
            name = _workflow_name(kwargs)
            if not name:
                lookup_id = self._convert(run_id) or run_id
                node = self._handler.get_node(lookup_id)
                if node is not None:
                    name = str(node.span_params.get("name") or "")
            try:
                if should_compact_chain_io(name, parent_run_id):
                    outputs = compact_trace_payload(outputs, name=name)
            except Exception as exc:  # noqa: BLE001 — observability must not break the store
                _logger.warning("trace compact skipped on chain_end (%s)", exc)
            try:
                # Span suprimido nunca virou nó: `end_node` só loga um debug e volta. Mantemos a
                # chamada mesmo assim pra não divergir do ciclo de vida do SDK.
                await super().on_chain_end(
                    outputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning("galileo on_chain_end failed (%s)", exc)

        # --- folhas: só reparenting (nunca supressão) -------------------------

        async def on_llm_start(
            self,
            serialized: dict[str, Any],
            prompts: list[str],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> Any:
            await super().on_llm_start(
                serialized, prompts, run_id=run_id, parent_run_id=self._reparent(parent_run_id), **kwargs,
            )

        async def on_chat_model_start(
            self,
            serialized: dict[str, Any],
            messages: list[Any],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> Any:
            await super().on_chat_model_start(
                serialized, messages, run_id=run_id, parent_run_id=self._reparent(parent_run_id), **kwargs,
            )

        async def on_tool_start(
            self,
            serialized: dict[str, Any],
            input_str: str,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            tags: list[str] | None = None,
            metadata: dict[str, Any] | None = None,
            **kwargs: Any,
        ) -> Any:
            # Folha, mas D.2 abre uma exceção pro tool span `check_response_cache` (F-022): ele
            # é puro plumbing de observabilidade (a decisão de cache não é negócio do shopper) e
            # some do trace — a decisão sobrevive como metadata no pai efetivo (`_merge_cache_metadata`).
            # Qualquer outro tool span continua só reparentando, nunca suprimindo.
            effective_parent = parent_run_id
            try:
                effective_parent = self._effective_parent(parent_run_id)
                drop = self._should_drop(
                    serialized, {**kwargs, "metadata": metadata}, parent_run_id, effective_parent,
                    node_type="tool",
                )
                if drop and effective_parent is not None:
                    self._dropped[self._convert(run_id) or run_id] = effective_parent
                    self._merge_cache_metadata(effective_parent, metadata)
                    return None
            except Exception as exc:  # noqa: BLE001 — fallback SEM supressão
                _logger.warning("trace span policy skipped on tool_start (%s)", exc)
                effective_parent = parent_run_id
            await super().on_tool_start(
                serialized, input_str, run_id=run_id, parent_run_id=effective_parent,
                tags=tags, metadata=metadata, **kwargs,
            )

        async def on_tool_end(
            self,
            output: Any,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> Any:
            name = ""
            lookup_id = self._convert(run_id) or run_id
            node = self._handler.get_node(lookup_id)
            if node is not None:
                name = str(node.span_params.get("name") or "")
            try:
                output = _compact_tool_output(output, name=name)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("trace compact skipped on tool_end (%s)", exc)
            await super().on_tool_end(
                output, run_id=run_id, parent_run_id=self._reparent(parent_run_id), **kwargs,
            )

        async def on_retriever_end(
            self,
            documents: list[Any],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> Any:
            try:
                documents = _compact_retriever_output(documents)
            except Exception as exc:  # noqa: BLE001
                _logger.warning("trace compact skipped on retriever_end (%s)", exc)
            await super().on_retriever_end(
                documents, run_id=run_id, parent_run_id=self._reparent(parent_run_id), **kwargs,
            )

        async def on_retriever_start(
            self,
            serialized: dict[str, Any],
            query: str,
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> Any:
            await super().on_retriever_start(
                serialized, query, run_id=run_id, parent_run_id=self._reparent(parent_run_id), **kwargs,
            )

else:

    class VegaGalileoCallback:  # type: ignore[no-redef]
        """Stub when galileo is not installed."""

        pass
