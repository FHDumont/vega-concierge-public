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
        """GalileoAsyncCallback + Vega Trace UX (never alters store execution).

        Three things on top of SDK:

        1. **I/O compaction** of LangGraph workflows (trace root only).
        2. **Suppression + reparenting** of plumbing spans (`galileo_span_policy`): suppressed span
           is not emitted and its children are repainted to effective parent — which promotes
           `[retriever]`/`[tool]` near root instead of orphaning them (that's exactly what
           SDK's `langsmith:hidden` tag does NOT do: it hides and breaks subtree).
        3. **Live trace** (D.3): when `session_scope` already opened request trace
           (`start_new_trace=False`), LangGraph tree is hung on it instead of becoming own
           trace on `commit()`, and root — which is born empty — inherits compact input from
           first workflow. It's this open trace that gives `current_parent()` to Agent Control and
           makes `[control]` span exist.

        `_dropped` is map `suppressed run_id → effective parent run_id`. It stores already-resolved chain,
        so suppressed parent whose parent was also suppressed resolves in one step. Callback
        instance is per request (`galileo_obs.callbacks()`), so map has trace lifetime.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self._dropped: dict[UUID, UUID] = {}

        # --- reparenting helpers -----------------------------------------

        @staticmethod
        def _convert(run_id: UUID | None) -> UUID | None:
            """Same id normalization SDK does before indexing `_nodes` (UUID7 → UUID4)."""
            if run_id is None:
                return None
            if convert_uuid_if_uuid7 is None:
                return run_id
            return convert_uuid_if_uuid7(run_id) or run_id

        def _effective_parent(self, parent_run_id: UUID | None) -> UUID | None:
            """Resolve chain of suppressed parents to first really-emitted span."""
            current = self._convert(parent_run_id)
            seen: set[UUID] = set()
            while current is not None and current in self._dropped:
                if current in seen:  # defensive: cycle impossible, but don't hang request
                    return None
                seen.add(current)
                current = self._dropped[current]
            return current

        def _node_name(self, run_id: UUID | None) -> str | None:
            """Name of already-emitted span — `None` when no span (root/unknown parent)."""
            if run_id is None:
                return None
            node = self._handler.get_node(run_id)
            if node is None:
                return None
            return str(node.span_params.get("name") or "")

        def _reparent(self, parent_run_id: UUID | None) -> UUID | None:
            """Parent to use in `super()` — original when nothing was suppressed above."""
            try:
                return self._effective_parent(parent_run_id)
            except Exception as exc:  # noqa: BLE001 — reparenting fails better than no trace
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
                return False  # trace root (or lost parent): always emit
            if not self._handler.get_nodes():
                return False  # nothing emitted yet — this span becomes root
            name = GalileoCallback._get_node_name(node_type, serialized, kwargs)
            if not name:
                name = _workflow_name(kwargs, serialized)
            # Effective parent with no registered node → `None` → `suppress` returns False (tree already
            # strange, not time to prune).
            return suppress(name, self._node_name(effective_parent))

        def _merge_cache_metadata(
            self, run_id: UUID | None, metadata: dict[str, Any] | None,
        ) -> None:
            """F-022 becomes metadata (D.2): when tool span `check_response_cache` is suppressed,
            cache decision can't simply disappear from Console — it's recorded in
            ancestor span (`effective_parent`) as `response_cache`/`cache_hit`, with
            model/provider/tokens when available (hit). Acts only when `metadata` environment
            carries `response_cache` — i.e., only in F-022 path, not any tool span suppression."""
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
            except Exception as exc:  # noqa: BLE001 — observability never breaks store
                _logger.warning("trace cache metadata merge skipped (%s)", exc)

        # --- live trace (D.3) ---------------------------------------------------

        def _live_trace(self) -> Any | None:
            """Trace opened by `session_scope` — `None` in batch mode.

            In batch mode (`_start_new_trace=True`) SDK's `commit()` creates root,
            and there's nothing to enrich here."""
            if getattr(self._handler, "_start_new_trace", True):
                return None
            parent = self._handler._galileo_logger.current_parent()
            if parent is None or getattr(parent, "_parent", None) is not None:
                return None  # not trace root (span opened mid-way) — don't touch
            return parent

        def _seed_live_trace_input(
            self, serialized: dict[str, Any] | None, inputs: dict[str, Any], kwargs: dict[str, Any],
        ) -> None:
            """Lend trace root the input from first LangGraph workflow of request.

            With live trace Console root becomes trace opened by `session_scope`,
            born without input (request hasn't processed anything when it opens). Payload
            only appears in graph's `on_chain_start` — compacted by same output rules
            (`compact_trace_payload`), else entire LangGraph state goes to root, which is
            exactly what compaction exists to avoid. Root output SDK inherits from
            last child on `conclude` (workflow itself, already compact)."""
            trace = self._live_trace()
            if trace is None or getattr(trace, "input", None):
                return
            name = GalileoCallback._get_node_name("chain", serialized, kwargs)
            if not name:
                name = _workflow_name(kwargs, serialized)
            trace.input = serialize_to_str(compact_trace_payload(inputs, name=name))

        # --- chain --------------------------------------------------------

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
                # LangChain root: I/O compaction stays anchored here (`parent_run_id is
                # None`), regardless of live trace existing above — `session_scope` trace
                # is not LangChain run and never appears as `parent_run_id`.
                try:
                    self._seed_live_trace_input(serialized, inputs, kwargs)
                except Exception as exc:  # noqa: BLE001 — root without input beats 500
                    _logger.warning("trace root input seeding skipped (%s)", exc)
            effective_parent = parent_run_id
            try:
                effective_parent = self._effective_parent(parent_run_id)
                hidden = bool(tags) and _HIDDEN_TAG in (tags or [])
                drop = hidden or self._should_drop(serialized, kwargs, parent_run_id, effective_parent)
                if drop and effective_parent is not None:
                    # Doesn't emit; registers effective parent for children to climb one level.
                    self._dropped[self._convert(run_id) or run_id] = effective_parent
                    return None
            except Exception as exc:  # noqa: BLE001 — fallback WITHOUT suppression
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
            except Exception as exc:  # noqa: BLE001 — observability must not break store
                _logger.warning("trace compact skipped on chain_end (%s)", exc)
            try:
                # Suppressed span never became node: `end_node` just logs debug and returns. We keep
                # call anyway to not diverge from SDK lifecycle.
                await super().on_chain_end(
                    outputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning("galileo on_chain_end failed (%s)", exc)

        # --- leaves: reparenting only (never suppression) -----------------------

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
            # Leaf, but D.2 opens an exception for the tool span `check_response_cache` (F-022): it
            # is pure observability plumbing (the cache decision is not the shopper's business) and
            # disappears from the trace — the decision survives as metadata on the effective parent (`_merge_cache_metadata`).
            # Any other tool span keeps only reparenting, never suppression.
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
            except Exception as exc:  # noqa: BLE001 — fallback WITHOUT suppression
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
            # Pass document list through to the SDK unchanged — Chunk Relevance/Attribution
            # need N individual chunks, not a compact preview dict. BTS/UI compaction lives in
            # galileo_trace_compact at read/export time, not here (F-WORKSHOP-RAG-1, ADR-031).
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
