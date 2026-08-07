"""Tests for local agent-runtime registration metadata."""
from __future__ import annotations

import pytest

from app.obs import galileo_control
from app.platform import agent_runtime


@pytest.fixture
def empty_registry(monkeypatch):
    monkeypatch.setattr(agent_runtime, "_registrations", {})


def test_registration_preserves_local_declaration_order_and_control_metadata(empty_registry):
    search = agent_runtime.AgentRegistration(
        name="search-agent",
        control_steps=(
            agent_runtime.AgentControlStep(type="llm", name="search", phase="pre"),
        ),
    )
    fulfillment = agent_runtime.AgentRegistration(
        name="fulfillment-agent",
        control_steps=(
            agent_runtime.AgentControlStep(type="tool", name="orders.fulfill"),
            agent_runtime.AgentControlStep(type="llm", name="shipping_copy", phase="post"),
        ),
    )

    agent_runtime.register_agent(search)
    agent_runtime.register_agent(fulfillment)

    assert [agent.name for agent in agent_runtime.registered_agents()] == [
        "search-agent",
        "fulfillment-agent",
    ]
    assert [step.as_dict() for step in agent_runtime.registered_control_steps()] == [
        {"type": "llm", "name": "search"},
        {"type": "tool", "name": "orders.fulfill"},
        {"type": "llm", "name": "shipping_copy"},
    ]
    assert agent_runtime.control_features("pre") == frozenset({"search"})
    assert agent_runtime.control_features("post") == frozenset({"shipping_copy"})


def test_registration_is_idempotent_but_rejects_conflicting_agent_metadata(empty_registry):
    original = agent_runtime.AgentRegistration(
        name="catalog-agent",
        control_steps=(
            agent_runtime.AgentControlStep(type="llm", name="product_qa", phase="pre"),
        ),
    )
    changed = agent_runtime.AgentRegistration(
        name="catalog-agent",
        control_steps=(
            agent_runtime.AgentControlStep(type="llm", name="product_qa", phase="post"),
        ),
    )

    assert agent_runtime.register_agent(original) is original
    assert agent_runtime.register_agent(original) is original
    with pytest.raises(ValueError, match="already registered differently"):
        agent_runtime.register_agent(changed)


def test_llm_boundary_requires_an_explicit_control_phase(empty_registry):
    registration = agent_runtime.AgentRegistration(
        name="invalid-agent",
        control_steps=(agent_runtime.AgentControlStep(type="llm", name="missing-phase"),),
    )

    with pytest.raises(ValueError, match="must declare a control phase"):
        agent_runtime.register_agent(registration)


def test_agent_control_consumes_the_local_agent_registration():
    assert galileo_control.register_steps() == [
        {"type": "llm", "name": "delete_product"},
        {"type": "llm", "name": "list_recent_customers"},
        {"type": "tool", "name": "returns.finalize"},
        {"type": "llm", "name": "product_qa"},
        {"type": "llm", "name": "search"},
        {"type": "llm", "name": "notification_copy"},
    ]
