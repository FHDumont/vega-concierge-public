"""Splunk Agent Observability LangChain callback with compact workflow trace I/O."""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from .galileo_trace_compact import compact_trace_payload, should_compact_workflow_io

_logger = logging.getLogger(__name__)

try:
    from galileo.handlers.langchain import GalileoAsyncCallback
    from galileo.utils.uuid_utils import convert_uuid_if_uuid7
except ImportError:  # pragma: no cover — dev without galileo installed
    GalileoAsyncCallback = None  # type: ignore[misc, assignment]
    convert_uuid_if_uuid7 = None  # type: ignore[misc, assignment]


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
        """GalileoAsyncCallback — compacta só I/O de workflows LangGraph (nunca altera execução)."""

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
                lookup_id = convert_uuid_if_uuid7(run_id) or run_id if convert_uuid_if_uuid7 else run_id
                node = self._handler.get_node(lookup_id)
                if node is not None:
                    name = str(node.span_params.get("name") or "")
            try:
                if should_compact_workflow_io(name, parent_run_id):
                    outputs = compact_trace_payload(outputs, name=name)
            except Exception as exc:  # noqa: BLE001 — observability must not break the store
                _logger.warning("trace compact skipped on chain_end (%s)", exc)
            try:
                await super().on_chain_end(
                    outputs, run_id=run_id, parent_run_id=parent_run_id, **kwargs,
                )
            except Exception as exc:  # noqa: BLE001
                _logger.warning("galileo on_chain_end failed (%s)", exc)

else:

    class VegaGalileoCallback:  # type: ignore[no-redef]
        """Stub when galileo is not installed."""

        pass
