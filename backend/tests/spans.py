"""Espião de callbacks LangChain — captura os rótulos que o `GalileoAsyncCallback` consumiria,
sem rede. Extraído dos smokes `run_span_names_demo.py`/`run_tools_demo.py` (F-BACKEND-1)."""
from __future__ import annotations

from langchain_core.callbacks import BaseCallbackHandler


def event_label(serialized: dict | None, **kwargs) -> str:
    for key in ("name", "run_name"):
        val = kwargs.get(key)
        if val:
            return str(val)
    if not serialized:
        return ""
    for key in ("name", "run_name"):
        val = serialized.get(key)
        if val:
            return str(val)
    run_id = serialized.get("id")
    if isinstance(run_id, list) and run_id:
        return str(run_id[-1])
    return str(run_id or "")


class SpanSpy(BaseCallbackHandler):
    """Captura rótulos visíveis de LLM spans, chains (incl. nós LangGraph), tools e retrievers.

    O span de retriever é o ponto frágil da F-GALILEO-1: ele só aparece se o `config` chegar do
    tool ao retriever, e isso é fácil de quebrar sem perceber."""

    def __init__(self) -> None:
        self.llm_names: list[str] = []
        self.chain_names: list[str] = []
        self.chain_metadata: list[dict] = []
        self.tool_names: list[str] = []
        self.chain_inputs: list[object] = []
        self.chain_outputs: list[object] = []
        self.tool_inputs: list[object] = []
        self.tool_outputs: list[object] = []
        self.retriever_queries: list[str] = []

    def on_llm_start(self, serialized, prompts, **kwargs):  # noqa: ANN001
        self.llm_names.append(event_label(serialized, **kwargs))

    def on_chain_start(self, serialized, inputs, **kwargs):  # noqa: ANN001
        self.chain_names.append(event_label(serialized, **kwargs))
        self.chain_inputs.append(inputs)
        meta = kwargs.get("metadata")
        self.chain_metadata.append(dict(meta) if isinstance(meta, dict) else {})

    def on_chain_end(self, outputs, **kwargs):  # noqa: ANN001
        self.chain_outputs.append(outputs)

    def on_tool_start(self, serialized, input_str, **kwargs):  # noqa: ANN001
        self.tool_names.append(event_label(serialized, **kwargs))
        self.tool_inputs.append(input_str)

    def on_tool_end(self, output, **kwargs):  # noqa: ANN001
        self.tool_outputs.append(output)

    def on_retriever_start(self, serialized, query, **kwargs):  # noqa: ANN001
        self.retriever_queries.append(query)

    # --- consultas ---------------------------------------------------------

    def metadata_for(self, name_substr: str) -> dict | None:
        needle = name_substr.lower()
        for chain_name, meta in zip(self.chain_names, self.chain_metadata):
            if needle in (chain_name or "").lower():
                return meta
        return None


def has(substr: str, names: list[str]) -> bool:
    needle = substr.lower()
    return any(needle in (n or "").lower() for n in names)


def is_title_case_llm_name(name: str) -> bool:
    """True se parece Title Case multi-palavra (espaço + maiúscula) — padrão legado F-GALILEO-4."""
    parts = (name or "").split()
    return len(parts) >= 2 and any(p[:1].isupper() for p in parts[1:] if p)
