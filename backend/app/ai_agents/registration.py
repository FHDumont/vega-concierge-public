"""Agent Control boundaries owned by the Vega agent package."""
from __future__ import annotations

from ..platform.agent_runtime import AgentControlStep, AgentRegistration, register_agent


def register_local_agents() -> None:
    """Publish the current agents' stable runtime boundaries."""
    register_agent(AgentRegistration(
        name="catalog-administration",
        control_steps=(
            AgentControlStep(type="llm", name="delete_product", phase="pre"),
            AgentControlStep(type="llm", name="list_recent_customers", phase="pre"),
        ),
    ))
    register_agent(AgentRegistration(
        name="returns",
        control_steps=(AgentControlStep(type="tool", name="returns.finalize"),),
    ))
    register_agent(AgentRegistration(
        name="catalog-assistant",
        control_steps=(
            AgentControlStep(type="llm", name="product_qa", phase="pre"),
            AgentControlStep(type="llm", name="search", phase="pre"),
        ),
    ))
    register_agent(AgentRegistration(
        name="shopper-messaging",
        control_steps=(AgentControlStep(type="llm", name="notification_copy", phase="post"),),
    ))
