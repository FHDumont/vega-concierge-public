"""Chat trace/output contracts — compact spans and minimal API payloads."""
from __future__ import annotations

from uuid import uuid4

from app.obs.galileo_trace_compact import (
    _compact_chat_node_output,
    _compact_tool_output,
    compact_trace_payload,
    should_compact_chain_io,
)


def test_chat_node_output_is_compact():
    data = {
        "answer": "Here's an overview of Vega's store policies:",
        "artifacts": {
            "grounded": True,
            "layout": {
                "lead": "Here's an overview of Vega's store policies:",
                "sections": [{"title": "Returns", "body": "30-day window."}],
            },
        },
        "messages": [{"role": "assistant", "content": "ignored"}],
        "trace": ["step"],
    }
    out = compact_trace_payload(data, name="chat.answer_store_policy")
    assert out["answer"].startswith("Here's an overview")
    assert "sections" not in out
    assert out["layout_sections"] == 1
    assert "messages" not in out


def test_search_policies_tool_output_is_compact():
    payload = {
        "question": "how works returns?",
        "chunks": [{"source": "returns", "section": "Window", "text": "x" * 900}],
    }
    out = _compact_tool_output(payload, name="search_policies")
    assert out["chunk_count"] == 1
    assert "chunks" not in out


def test_chat_graph_nodes_request_compaction():
    assert should_compact_chain_io("chat.answer_store_policy", uuid4()) is True
    assert should_compact_chain_io("chat.workflow", None) is True


def test_chat_artifacts_strip_layout_from_node_preview():
    out = _compact_chat_node_output({
        "answer": "short",
        "artifacts": {"layout": {"sections": [{"title": "A", "body": "long body"}]}},
    })
    assert out["layout_sections"] == 1
    assert "long body" not in str(out)
