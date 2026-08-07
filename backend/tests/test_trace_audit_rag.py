"""F-WORKSHOP-RAG-1 Etapa 1 — diagnóstico tool/RAG vs Console Galileo (trace ac2de978…).

Trace de referência do dono: sessão 403fee40…, span focal f1be360d… — fluxo equivalente offline:
POST /api/chat com "What are the policies of Vega?" → ``chat.answer_store_policy`` +
``retrieve_store_policies`` (UC chat policy, não product_qa).

Hipóteses (Etapa 1):
- H1 CONFIRMED: ``VegaGalileoCallback.on_retriever_end`` aplicava ``_compact_retriever_output``
  antes do SDK — colapsava ``list[Document]`` num dict ``{document_count, previews}``.
- H2 CONFIRMED: ``stub.py`` linhas 249/267 chamavam ``search_catalog()``/``get_price()`` direto
  (bypass ``StructuredTool.invoke``) no fallback do loop ReAct simulado.
- H3 CONFIRMED: retriever span LangChain existe (``SpanSpy.retriever_queries``), mas o SDK via
  callback recebia blob compactado (H1) — tool ``search_policies`` devolve chunks no output da tool
  separadamente do retriever filho.
- H4 REJECTED: ``search_policies_tool.invoke(..., config=config)`` propaga config ao retriever
  (``test_tools.test_search_policies_emits_a_retriever_span`` + asserts abaixo).
- H5 REJECTED: ``search_catalog``/``get_price`` estão em ``PROTECTED_SPAN_NAMES`` — supressão não
  esconde tools de catálogo/preço nos fluxos concierge/gift.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

from app.ai_agents.product_qa import answer_product_question
from app.obs.galileo_span_policy import PROTECTED_SPAN_NAMES
from app.obs.galileo_trace_compact import _compact_retriever_output
from app.runnable_config import build_runnable_config, make_thread_id
from app.store.langchain_tools import TOOLS_BY_NAME
from tests.spans import SpanSpy, has

pytestmark = pytest.mark.trace_audit


def test_h1_compact_retriever_output_collapses_document_list():
    """Mecanismo de H1: compactação pré-SDK destrói N entradas individuais."""
    docs = [
        Document(page_content="Return window is 30 days.", metadata={"section": "Returns"}),
        Document(page_content="Free shipping over $50.", metadata={"section": "Shipping"}),
    ]
    compact = _compact_retriever_output(docs)
    assert isinstance(compact, dict), "H1: list[Document] vira dict antes do SDK"
    assert compact["document_count"] == 2
    assert len(compact["previews"]) == 2
    assert not any(hasattr(item, "page_content") for item in compact.values() if isinstance(item, list))


def test_retriever_top_k_matches_document_count_at_langchain_layer():
    """Etapa 2 critério: retriever devolve k documentos (top_k efetivo), não blob compactado."""
    from app.settings import settings

    spy = SpanSpy()
    TOOLS_BY_NAME["search_policies"].invoke(
        {"question": "how many days do I have to return an order?"},
        config={"callbacks": [spy]},
    )
    docs = spy.retriever_outputs[0]
    assert isinstance(docs, list)
    k = len(docs)
    assert k >= 1
    assert k <= settings.rag_top_k
    assert all(hasattr(doc, "page_content") for doc in docs)


@pytest.mark.asyncio
async def test_h1_callback_passes_retriever_documents_to_sdk(monkeypatch):
    """Pós-fix Etapa 2: SDK recebe list[Document] com N chunks (top_k efetivo)."""
    galileo = pytest.importorskip("galileo")
    from app.obs.galileo_callback import VegaGalileoCallback

    passed: list[object] = []
    original_end = galileo.handlers.langchain.GalileoAsyncCallback.on_retriever_end

    async def capture_sdk_retriever_end(self, documents, **kwargs):  # noqa: ANN001
        passed.append(documents)

    monkeypatch.setattr(
        galileo.handlers.langchain.GalileoAsyncCallback, "on_retriever_end", capture_sdk_retriever_end,
    )

    cb = VegaGalileoCallback.__new__(VegaGalileoCallback)
    handler = AsyncMock()
    handler.get_node.return_value = None
    handler._start_new_trace = True
    cb._handler = handler
    cb._dropped = {}

    docs = [Document(page_content="chunk one"), Document(page_content="chunk two")]
    run_id = uuid.uuid4()
    await cb.on_retriever_end(docs, run_id=run_id, parent_run_id=None)

    assert len(passed) == 1, passed
    sdk_docs = passed[0]
    assert isinstance(sdk_docs, list), f"H1 fix: SDK must receive list, got {type(sdk_docs)}"
    assert len(sdk_docs) == 2
    assert all(hasattr(item, "page_content") for item in sdk_docs)

    monkeypatch.setattr(
        galileo.handlers.langchain.GalileoAsyncCallback, "on_retriever_end", original_end,
    )


def test_h2_stub_invoke_tool_emits_tool_spans():
    """H2 fix: helper de fallback usa StructuredTool.invoke com callbacks."""
    from app.llm.stub import _stub_invoke_tool

    spy = SpanSpy()
    result = _stub_invoke_tool(
        "search_catalog",
        {"query": "birthday gift", "budget": 300.0},
        None,
        config={"callbacks": [spy]},
    )
    assert isinstance(result, list) and result
    assert "search_catalog" in spy.tool_names, spy.tool_names


def test_h2_stub_react_fallback_emits_tool_spans_via_invoke():
    """H2: loop ReAct do stub dispara invoke quando o payload da ToolMessage é inválido."""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    from app.llm.stub import VegaStubChatModel
    from app.store.langchain_tools import CONCIERGE_TOOLS

    spy = SpanSpy()
    stub = VegaStubChatModel().bind_tools(CONCIERGE_TOOLS)
    messages = [
        SystemMessage(content="You are a concierge."),
        HumanMessage(content="a birthday gift under $300"),
        ToolMessage(content="unparseable-payload", name="search_catalog", tool_call_id="call_1"),
        ToolMessage(content="unparseable-payload", name="get_price", tool_call_id="call_2"),
    ]
    stub.invoke(messages, config={"callbacks": [spy]})
    assert "search_catalog" in spy.tool_names, spy.tool_names
    assert "get_price" in spy.tool_names, spy.tool_names


def test_h3_chat_policy_flow_emits_retriever_with_document_list(api_client, monkeypatch):
    """Fluxo do trace ac2de978… — retriever LangChain entrega list[Document] ao callback chain."""
    from app import runnable_config
    from contextlib import contextmanager

    spy = SpanSpy()

    @contextmanager
    def session_scope(session_id=None, *, feature=None):
        yield session_id

    monkeypatch.setattr(runnable_config.galileo_obs, "callbacks", lambda: [spy])
    monkeypatch.setattr(runnable_config.galileo_obs, "session_scope", session_scope)

    response = api_client.post(
        "/api/chat",
        headers={"X-Vega-Session": "trace-audit-chat-policy"},
        json={"messages": [{"role": "user", "content": "What are the policies of Vega?"}]},
    )
    assert response.status_code == 200, response.text
    assert has("chat.answer_store_policy", spy.chain_names), spy.chain_names
    assert spy.retriever_queries, spy.retriever_queries
    assert spy.retriever_outputs, spy.retriever_outputs
    first = spy.retriever_outputs[0]
    assert isinstance(first, list), f"H3: retriever output must be list at LangChain layer, got {type(first)}"
    assert len(first) >= 1
    assert all(hasattr(doc, "page_content") for doc in first)


def test_h4_search_policies_invoke_propagates_config_to_retriever():
    spy = SpanSpy()
    result = TOOLS_BY_NAME["search_policies"].invoke(
        {"question": "how many days do I have to return an order?"},
        config={"callbacks": [spy]},
    )
    assert spy.retriever_queries, "H4 REJECTED if config missing — no retriever span"
    assert spy.retriever_outputs, spy.retriever_outputs
    assert isinstance(spy.retriever_outputs[0], list)
    assert result["chunks"]


def test_h5_catalog_tools_are_protected_from_suppression():
    for name in ("search_catalog", "get_price"):
        assert name in PROTECTED_SPAN_NAMES, f"H5: {name} must stay visible in Console traces"


def test_product_qa_policy_retriever_emits_document_list():
    """UC-1 policy path — mesmo contrato de chunks individuais no retriever."""
    spy = SpanSpy()
    config = {
        **build_runnable_config(thread_id=make_thread_id(), feature="product_qa"),
        "callbacks": [spy],
    }
    result = answer_product_question(
        "NS-001", "how many days do I have to return this?", config=config,
    )
    assert result["grounded"] is True
    assert "search_policies" in spy.tool_names, spy.tool_names
    assert spy.retriever_outputs, spy.retriever_outputs
    docs = spy.retriever_outputs[0]
    assert isinstance(docs, list) and len(docs) >= 1
    assert all(hasattr(doc, "page_content") for doc in docs)
