"""Contracts for the isolated chat and recommendation LangGraphs."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ai_agents import chat_workflow, concierge_workflow


AGENT_DIR = Path(__file__).parents[1] / "app" / "ai_agents"


def test_shopper_workflows_do_not_import_legacy_or_shared_graph_modules():
    for filename in ("chat_workflow.py", "concierge_workflow.py"):
        tree = ast.parse((AGENT_DIR / filename).read_text())
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any(
            module == "app.agents"
            or module.startswith("app.agents.")
            or module == "app.graphs"
            or module.startswith("app.graphs.")
            or module == "app.features"
            or module.startswith("app.features.")
            or module == "app.galileo_span"
            or module.startswith("app.galileo_span.")
            or module == "app.hub.agent_config"
            or module.startswith("app.hub.agent_config.")
            for module in imported
        )
        assert not any(module.startswith("app.ai_agents.") for module in imported)


def test_workflows_keep_the_public_galileo_workflow_and_node_names():
    assert concierge_workflow.workflow.name == "concierge.workflow"
    assert chat_workflow.workflow.name == "chat.workflow"
    assert "concierge.search_catalog_and_price" in concierge_workflow.workflow.get_graph().nodes
    assert "concierge.compose_product_recommendation" in concierge_workflow.workflow.get_graph().nodes
    assert "chat.route_shopper_request" in chat_workflow.workflow.get_graph().nodes
    assert "chat.assemble_shopper_reply" in chat_workflow.workflow.get_graph().nodes


@pytest.mark.asyncio
async def test_empty_chat_keeps_the_http_workflow_contract():
    assert await chat_workflow.arun_chat_workflow([]) == {
        "answer": "Please send a message.",
        "intent": "general",
        "artifacts": {},
        "language": None,
        "trace": [],
    }


@pytest.mark.asyncio
async def test_concierge_workflow_returns_a_grounded_recommendation(monkeypatch):
    monkeypatch.setattr(
        concierge_workflow,
        "search_catalog",
        lambda _request, _budget: [{
            "sku": "NS-001", "name": "Aura Bluetooth Headphones", "price": 99.0,
            "tags": ["audio"],
        }],
    )
    monkeypatch.setattr(
        concierge_workflow, "get_price",
        lambda _sku: {"sku": "NS-001", "price": 99.0},
    )

    monkeypatch.setattr(
        concierge_workflow, "complete_recommendation",
        lambda *_args, **_kwargs: "Try Aura Bluetooth Headphones.",
    )
    result = await concierge_workflow.arun_workflow("recommend headphones under $100")

    assert result["selected"]["sku"] == "NS-001"
    assert result["answer"] == "Try Aura Bluetooth Headphones."
    assert result["quality"] == {"grounded": True, "accuracy": 1.0}
