"""Router delegation for UC-2 gift_recommend when cost_spike is ON."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field

import pytest

from app import runnable_config
from app.problems import FLAGS
from tests.spans import SpanSpy, has

DEMO_QUESTION = "a birthday gift under $300"
OFF_TOPIC_REDIRECT = "use the concierge chat"


@dataclass
class RequestTrace:
    spy: SpanSpy = field(default_factory=SpanSpy)
    sessions: list[tuple[str | None, str | None]] = field(default_factory=list)


@pytest.fixture
def request_trace(monkeypatch) -> RequestTrace:
    trace = RequestTrace()

    @contextmanager
    def session_scope(session_id: str | None = None, *, feature: str | None = None):
        trace.sessions.append((session_id, feature))
        yield session_id

    monkeypatch.setattr(runnable_config.galileo_obs, "callbacks", lambda: [trace.spy])
    monkeypatch.setattr(runnable_config.galileo_obs, "session_scope", session_scope)
    return trace


@pytest.fixture
def cost_spike_on(reset_problem_flags):
    reset_problem_flags.cost_spike = True
    return reset_problem_flags


def test_chat_cost_spike_demo_delegates_to_gift_recommend(api_client, request_trace, cost_spike_on):
    response = api_client.post(
        "/api/chat",
        headers={"X-Vega-Session": "gift-chat-delegation"},
        json={"messages": [{"role": "user", "content": DEMO_QUESTION}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["error"] is None
    assert body["intent"] == "recommend"
    assert body["artifacts"].get("recommended")
    assert has("gift_recommend.workflow", request_trace.spy.chain_names), request_trace.spy.chain_names
    assert request_trace.spy.retriever_queries, request_trace.spy.retriever_queries
    assert "search_catalog" in request_trace.spy.tool_names, request_trace.spy.tool_names
    assert "get_price" in request_trace.spy.tool_names, request_trace.spy.tool_names


def test_product_qa_cost_spike_demo_delegates_to_gift_recommend(api_client, request_trace, cost_spike_on):
    response = api_client.post(
        "/api/product/qa",
        headers={"X-Vega-Session": "gift-pdp-delegation"},
        json={"sku": "NS-001", "question": DEMO_QUESTION},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert OFF_TOPIC_REDIRECT not in body["answer"].lower()
    assert body["grounded"] is True
    assert has("gift_recommend.workflow", request_trace.spy.chain_names), request_trace.spy.chain_names
    assert request_trace.spy.retriever_queries, request_trace.spy.retriever_queries
    assert "search_catalog" in request_trace.spy.tool_names, request_trace.spy.tool_names
    assert "get_price" in request_trace.spy.tool_names, request_trace.spy.tool_names


def test_product_qa_cost_spike_off_still_redirects_off_topic(api_client, reset_problem_flags):
    reset_problem_flags.cost_spike = False
    response = api_client.post(
        "/api/product/qa",
        json={"sku": "NS-001", "question": DEMO_QUESTION},
    )

    assert response.status_code == 200, response.text
    assert OFF_TOPIC_REDIRECT in response.json()["answer"].lower()


def test_product_qa_uc1_price_question_unaffected(api_client, reset_problem_flags, request_trace):
    reset_problem_flags.cost_spike = False
    reset_problem_flags.price_hallucination = True
    response = api_client.post(
        "/api/product/qa",
        headers={"X-Vega-Session": "gift-uc1-price"},
        json={"sku": "NS-001", "question": "how much does it cost?"},
    )

    assert response.status_code == 200, response.text
    assert has("product_qa.workflow", request_trace.spy.chain_names), request_trace.spy.chain_names
    assert not has("gift_recommend.workflow", request_trace.spy.chain_names), request_trace.spy.chain_names


def test_uc2_preset_sets_cost_spike_not_inventory_outage(api_client, reset_problem_flags):
    body = api_client.post("/api/problems/preset/uc-2").json()
    assert body["cost_spike"] is True
    assert body["inventory_outage"] is False
    assert body["active_scenario"] == "uc-2"
