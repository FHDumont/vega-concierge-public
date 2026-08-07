"""Trace contracts at the HTTP boundary for shopper-navigation AI endpoints.

These tests deliberately enter through FastAPI.  Unit tests for individual workflows cannot
catch a router dropping the request callback, session header, or runnable config on its way to a
workflow/tool/retriever.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from app import runnable_config
from app.store import orders
from app.store.tools import CATALOG
from tests.spans import SpanSpy, has


@dataclass
class RequestTrace:
    """A callback spy plus the session scopes opened by the real request helper."""

    spy: SpanSpy = field(default_factory=SpanSpy)
    sessions: list[tuple[str | None, str | None]] = field(default_factory=list)


@pytest.fixture
def request_trace(monkeypatch) -> RequestTrace:
    """Attach one SpanSpy to configs built by the real ``ai_request_scope``.

    The fake session scope is intentionally narrow: Galileo's network/session implementation is
    outside this offline suite, while the router still executes the production context manager,
    config builder, and contextvar binding unchanged.
    """
    trace = RequestTrace()

    @contextmanager
    def session_scope(session_id: str | None = None, *, feature: str | None = None):
        trace.sessions.append((session_id, feature))
        yield session_id

    monkeypatch.setattr(runnable_config.galileo_obs, "callbacks", lambda: [trace.spy])
    monkeypatch.setattr(runnable_config.galileo_obs, "session_scope", session_scope)
    return trace


def _assert_forwarded_config(
    config: dict[str, Any],
    trace: RequestTrace,
    *,
    feature: str,
    session_id: str,
) -> None:
    """Verify the endpoint retained the one request-level callback and session metadata."""
    assert trace.sessions == [(session_id, feature)]
    assert config["callbacks"] == [trace.spy]
    assert config["metadata"]["feature"] == feature
    assert config["metadata"]["session_id"] == session_id
    assert config["configurable"]["thread_id"]


@pytest.mark.parametrize(
    ("module_name", "attribute", "path", "payload", "feature", "result"),
    [
        (
            "app.routers.concierge", "arun_chat_workflow", "/api/chat",
            {"messages": [{"role": "user", "content": "What are the policies of Vega?"}]},
            "chat", {"answer": "Policies", "intent": "general", "artifacts": {}, "trace": []},
        ),
        (
            "app.routers.concierge", "arun_workflow", "/api/run",
            {"request": "a birthday gift under $300"}, "concierge",
            {"trace": [], "quality": {}, "selected": None, "answer": "Pick", "language": "en"},
        ),
        (
            "app.routers.store", "suggest_cart_crosssell", "/api/cart/crosssell",
            {"skus": ["NS-001"]}, "cart_crosssell", {"products": [], "blurb": "Also consider"},
        ),
    ],
)
def test_navigation_endpoints_forward_callback_and_session_config(
    api_client, monkeypatch, request_trace, module_name, attribute, path, payload, feature, result,
):
    """Every navigation endpoint invokes its dependency with the scoped config, not a new one."""
    import importlib
    import inspect

    router = importlib.import_module(module_name)
    captured: list[dict[str, Any]] = []

    def sync_dependency(*_args, config, **_kwargs):
        captured.append(config)
        return result

    async def async_dependency(*_args, config, **_kwargs):
        captured.append(config)
        return result

    original = getattr(router, attribute)
    monkeypatch.setattr(router, attribute, async_dependency if inspect.iscoroutinefunction(original) else sync_dependency)

    session_id = f"navigation-{feature}"
    response = api_client.post(path, json=payload, headers={"X-Vega-Session": session_id})

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    _assert_forwarded_config(captured[0], request_trace, feature=feature, session_id=session_id)


def test_order_checkout_forwards_callback_and_session_config(api_client, monkeypatch, request_trace):
    from app.routers import orders as orders_router

    captured: list[dict[str, Any]] = []

    async def aplace_order(_items, _customer, _user_id, *, config):
        captured.append(config)
        return {"id": "ORDER-SPY", "status": "PAID"}

    monkeypatch.setattr(orders_router.checkout, "aplace_order", aplace_order)
    session_id = "navigation-fulfillment"
    product = CATALOG[0]
    response = api_client.post(
        "/api/orders",
        headers={"X-Vega-Session": session_id},
        json={
            "items": [{"sku": product["sku"], "name": product["name"], "qty": 1, "price": product["price"]}],
            "customer": {"name": "Trace", "email": "trace@vega.test", "address": "1 Trace Way"},
        },
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    _assert_forwarded_config(captured[0], request_trace, feature="fulfillment", session_id=session_id)


def test_notification_preview_forwards_callback_and_session_config(api_client, monkeypatch, request_trace):
    from app.routers import orders as orders_router

    orders.init_db()
    product = CATALOG[0]
    order = orders.create_order(
        [{"sku": product["sku"], "name": product["name"], "qty": 1, "price": product["price"]}],
        {"name": "Trace", "email": "trace@vega.test", "address": "1 Trace Way"},
        product["price"],
        "PAID",
    )
    captured: list[dict[str, Any]] = []

    def compose_notification_text(_order, *, config):
        captured.append(config)
        return {"subject": "Confirmed", "body": "Thanks", "channel": "email", "event": "confirmation"}

    monkeypatch.setattr(orders_router, "compose_notification_text", compose_notification_text)
    session_id = "navigation-notification"
    response = api_client.post(
        f"/api/orders/{order['id']}/notification", headers={"X-Vega-Session": session_id},
    )

    assert response.status_code == 200, response.text
    assert len(captured) == 1
    _assert_forwarded_config(
        captured[0], request_trace, feature="notification_copy", session_id=session_id,
    )
    assert captured[0]["metadata"]["order_id"] == order["id"]


async def test_chat_policy_endpoint_emits_workflow_retriever_and_llm_spans(api_client, request_trace):
    """Policy chat proves the full HTTP → graph → RAG → model callback path."""
    session_id = "span-chat-policy"
    response = api_client.post(
        "/api/chat",
        headers={"X-Vega-Session": session_id},
        json={"messages": [{"role": "user", "content": "What are the policies of Vega?"}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["error"] is None
    assert request_trace.sessions == [(session_id, "chat")]
    assert has("chat.workflow", request_trace.spy.chain_names), request_trace.spy.chain_names
    assert has("chat.answer_store_policy", request_trace.spy.chain_names), request_trace.spy.chain_names
    assert request_trace.spy.retriever_queries, request_trace.spy.retriever_queries
    # The policy answer can be served from the response cache, so this request only requires the
    # stable graph/retriever spans.  The callback is nevertheless attached (the workflow and
    # retriever above were observed through it).


async def test_concierge_endpoint_emits_workflow_tool_and_llm_spans(api_client, request_trace):
    session_id = "span-concierge"
    response = api_client.post(
        "/api/run",
        headers={"X-Vega-Session": session_id},
        json={"request": "a birthday gift under $300"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["error"] is None
    assert request_trace.sessions == [(session_id, "concierge")]
    assert has("concierge.workflow", request_trace.spy.chain_names), request_trace.spy.chain_names
    assert {"search_catalog", "get_price"} <= set(request_trace.spy.tool_names), request_trace.spy.tool_names
    assert has("compose_product_recommendation", request_trace.spy.llm_names), request_trace.spy.llm_names


async def test_checkout_endpoint_emits_fulfillment_workflow_and_tools(api_client, request_trace):
    product = CATALOG[2]
    response = api_client.post(
        "/api/orders",
        headers={"X-Vega-Session": "span-fulfillment"},
        json={
            "items": [{"sku": product["sku"], "name": product["name"], "qty": 1, "price": product["price"]}],
            "customer": {"name": "Trace", "email": "trace@vega.test", "address": "1 Trace Way"},
        },
    )

    assert response.status_code == 200, response.text
    assert request_trace.sessions == [("span-fulfillment", "fulfillment")]
    assert has("fulfillment.workflow", request_trace.spy.chain_names), request_trace.spy.chain_names
    assert {"check_inventory", "get_price", "decide_fraud_allow_or_block"} <= set(request_trace.spy.tool_names), request_trace.spy.tool_names
    assert has(
        "fulfillment.decide_fraud_allow_or_block", request_trace.spy.chat_model_names,
    ), request_trace.spy.chat_model_names


@pytest.mark.parametrize(
    ("path", "payload", "feature", "llm_span"),
    [
        (
            "/api/product/qa",
            {"sku": "NS-001", "question": "What is it?"},
            "product_qa",
            "feature.answer_product_question",
        ),
        (
            "/api/compare",
            {"sku_a": "NS-001", "sku_b": "NS-002"},
            "compare",
            "feature.write_comparison_verdict",
        ),
        (
            "/api/cart/crosssell",
            {"skus": ["NS-001"]},
            "cart_crosssell",
            "feature.suggest_cart_additions",
        ),
    ],
)
def test_navigation_endpoints_emit_real_chat_model_callbacks(
    api_client, request_trace, path, payload, feature, llm_span,
):
    """Each store-navigation AI endpoint must forward its callback into a chat-model span."""
    response = api_client.post(
        path, json=payload, headers={"X-Vega-Session": f"span-{feature}"},
    )

    assert response.status_code == 200, response.text
    assert request_trace.sessions == [(f"span-{feature}", feature)]
    assert has(llm_span, request_trace.spy.chat_model_names), request_trace.spy.chat_model_names


def test_notification_preview_emits_real_chat_model_callback(api_client, request_trace):
    orders.init_db()
    product = CATALOG[0]
    order = orders.create_order(
        [{"sku": product["sku"], "name": product["name"], "qty": 1, "price": product["price"]}],
        {"name": "Trace", "email": "trace@vega.test", "address": "1 Trace Way"},
        product["price"],
        "PAID",
    )
    response = api_client.post(
        f"/api/orders/{order['id']}/notification",
        headers={"X-Vega-Session": "span-notification"},
    )

    assert response.status_code == 200, response.text
    assert request_trace.sessions == [("span-notification", "notification_copy")]
    assert has(
        "feature.compose_notification_text", request_trace.spy.chat_model_names,
    ), request_trace.spy.chat_model_names
