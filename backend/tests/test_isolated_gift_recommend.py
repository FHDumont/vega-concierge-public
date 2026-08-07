"""Contract, behavior, and import-boundary tests for the isolated UC-2 gift workflow."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ai_agents import gift_recommend
from app.problems import FLAGS
from app.routers._common import is_gift_recommend_demo_question
from app.runnable_config import resolve_config
from tests.spans import SpanSpy, has


AGENT_PATH = Path(__file__).parents[1] / "app" / "ai_agents" / "gift_recommend.py"
DEMO_QUESTION = "a birthday gift under $300"


def test_isolated_gift_recommend_does_not_import_other_ai_agents():
    tree = ast.parse(AGENT_PATH.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            if isinstance(node, ast.ImportFrom):
                assert node.level != 1, "gift_recommend must not import sibling ai_agents modules"
    assert not any("ai_agents" in module for module in imported)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (DEMO_QUESTION, True),
        ("Birthday gift under $300", True),
        ("Can you recommend a gift?", True),
        ("how much does it cost?", False),
        ("What are your return policies?", False),
    ],
)
def test_is_gift_recommend_demo_question_heuristic(text, expected):
    assert is_gift_recommend_demo_question(text) is expected


def test_recommend_gift_export_and_workflow_name():
    assert gift_recommend.WORKFLOW_RUN_NAME == "gift_recommend.workflow"
    assert gift_recommend.recommend_gift.__name__ == "recommend_gift"


def test_cost_spike_off_runs_minimal_gift_workflow(monkeypatch):
    monkeypatch.setattr(FLAGS, "cost_spike", False)
    spy = SpanSpy()
    config = resolve_config({"callbacks": [spy]}, feature="gift_recommend")
    result = gift_recommend.recommend_gift(DEMO_QUESTION, config=config)
    names = spy.chain_names

    assert result["answer"]
    assert result["recommended"]
    assert has("gift_recommend.workflow", names), names
    assert has("gift_recommend.retrieve_catalog_context", names), names
    assert has("gift_recommend.search_catalog", names), names
    assert has("gift_recommend.quote_selected_product", names), names
    assert has("feature.compose_gift_recommendation", names), names
    assert spy.retriever_queries, spy.retriever_queries
    assert spy.tool_names.count("search_catalog") == 1, spy.tool_names
    assert spy.tool_names.count("get_price") == 1, spy.tool_names
    assert not has("gift_recommend.rescan_catalog", names), names
    assert not has("gift_recommend.rescan_catalog_context", names), names
    assert not has("gift_recommend.verify_price_quote", names), names
    assert not has("gift_recommend.polish_recommendation", names), names


def test_cost_spike_on_runs_redundant_gift_workflow(monkeypatch):
    monkeypatch.setattr(FLAGS, "cost_spike", True)
    spy = SpanSpy()
    config = resolve_config({"callbacks": [spy]}, feature="gift_recommend")
    result = gift_recommend.recommend_gift(DEMO_QUESTION, config=config)
    names = spy.chain_names

    assert result["answer"]
    assert result["recommended"]
    assert has("gift_recommend.rescan_catalog", names), names
    assert has("gift_recommend.rescan_catalog_context", names), names
    assert has("gift_recommend.confirm_catalog_search", names), names
    assert has("gift_recommend.verify_price_quote", names), names
    assert has("gift_recommend.polish_recommendation", names), names
    assert len(spy.retriever_queries) >= 2, spy.retriever_queries
    assert spy.tool_names.count("search_catalog") == 3, spy.tool_names
    assert spy.tool_names.count("get_price") == 2, spy.tool_names
