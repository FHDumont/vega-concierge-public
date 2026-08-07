"""Fulfillment workflow coverage — inventory checkout path (Advanced toggle inventory_outage)."""
from __future__ import annotations

import ast
import inspect
import json
from types import SimpleNamespace
from unittest.mock import Mock

from langchain_core.callbacks.base import BaseCallbackHandler

from app.ai_agents import fulfillment_workflow


def test_workflow_has_no_legacy_agent_or_graph_imports():
    source = inspect.getsource(fulfillment_workflow)
    tree = ast.parse(source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    direct_imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    forbidden = {"app.agents", "app.graphs.fulfillment", "app.graphs.react"}
    assert forbidden.isdisjoint(imports | direct_imports)
    assert fulfillment_workflow.workflow.name == "fulfillment.workflow"


def test_fraud_tool_is_owned_by_fulfillment_not_store_agents(monkeypatch):
    source = inspect.getsource(fulfillment_workflow)
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "store.langchain_tools"
        for alias in node.names
    }
    invoke = Mock(return_value=SimpleNamespace(text='{"decision":"ALLOW","score":0.08}'))
    monkeypatch.setattr(fulfillment_workflow, "_invoke_fraud_llm", invoke)

    raw = fulfillment_workflow._decide_fraud_allow_or_block_tool(
        '{"sku":"NS-001","price":99}',
        99.0,
        {"metadata": {"request_id": "fraud-owned"}},
    )

    assert "decide_fraud_allow_or_block_tool" not in imported_names
    assert fulfillment_workflow.decide_fraud_allow_or_block_tool.name == "decide_fraud_allow_or_block"
    assert raw == (
        '{"decision": "ALLOW", "score": 0.08, "allow": true, "llm_decision": "ALLOW", '
        '"llm_score": 0.08, "llm_response": "{\\"decision\\":\\"ALLOW\\",\\"score\\":0.08}", '
        '"source": "workshop_default"}'
    )
    assert invoke.call_args.kwargs["config"]["metadata"]["request_id"] == "fraud-owned"


def test_inventory_exception_fails_without_later_checkout_tools(monkeypatch):
    callback = BaseCallbackHandler()
    invoke = Mock(side_effect=RuntimeError("inventory service unavailable"))
    monkeypatch.setattr(fulfillment_workflow, "check_inventory_tool", Mock(invoke=invoke))
    fraud_invoke = Mock()
    monkeypatch.setattr(
        fulfillment_workflow,
        "decide_fraud_allow_or_block_tool",
        Mock(invoke=fraud_invoke),
    )
    confirm_invoke = Mock()
    payment_invoke = Mock()
    notification_invoke = Mock()
    monkeypatch.setattr(
        fulfillment_workflow, "confirm_cart_stock_tool", Mock(invoke=confirm_invoke),
    )
    monkeypatch.setattr(
        fulfillment_workflow, "charge_payment_tool", Mock(invoke=payment_invoke),
    )
    monkeypatch.setattr(fulfillment_workflow.orders, "transition", Mock())
    monkeypatch.setattr(
        fulfillment_workflow, "send_order_notification_tool", Mock(invoke=notification_invoke),
    )
    config = {
        "callbacks": [callback],
        "metadata": {"request_id": "uc2-error"},
    }

    result = fulfillment_workflow.run_fulfillment_workflow(
        [{"sku": "NS-001"}, {"sku": "NS-002"}],
        config=config,
    )

    assert result["status"] == "FAILED"
    assert result["failure_reason"] == "inventory_unavailable"
    assert invoke.call_count == 1
    assert invoke.call_args.args == ({"sku": "NS-001"},)
    tool_config = invoke.call_args.kwargs["config"]
    assert tool_config["metadata"]["request_id"] == "uc2-error"
    assert callback in tool_config["callbacks"].handlers
    fraud_invoke.assert_not_called()
    confirm_invoke.assert_not_called()
    payment_invoke.assert_not_called()
    fulfillment_workflow.orders.transition.assert_not_called()
    notification_invoke.assert_not_called()


def test_inventory_exception_persists_failed_order_immediately(monkeypatch):
    invoke = Mock(side_effect=RuntimeError("inventory service unavailable"))
    failed_order = {"id": "order-uc2", "status": "FAILED"}
    transition = Mock(return_value=failed_order)
    monkeypatch.setattr(fulfillment_workflow, "check_inventory_tool", Mock(invoke=invoke))
    monkeypatch.setattr(fulfillment_workflow.orders, "transition", transition)

    result = fulfillment_workflow.run_fulfillment_workflow(
        [{"sku": "NS-001"}],
        order={"id": "order-uc2", "status": "PENDING"},
    )

    assert result["status"] == "FAILED"
    assert result["order"] == failed_order
    transition.assert_called_once_with(
        "order-uc2", "FAILED", failure_reason="inventory_unavailable"
    )


def test_owned_fraud_tool_preserves_the_checkout_result_shape():
    result = fulfillment_workflow.run_fulfillment_workflow(
        [{"sku": "NS-001", "qty": 1, "price": 99.0}],
        99.0,
    )

    assert result["allow"] is True
    assert result["fraud"] == {
        "decision": "ALLOW",
        "score": 0.08,
        "allow": True,
        "llm_decision": "ALLOW",
        "llm_score": 0.08,
        "llm_response": '{"decision": "ALLOW", "score": 0.08}',
        "source": "workshop_default",
    }


def test_happy_path_runs_full_checkout_pipeline(monkeypatch):
    inventory_invoke = Mock(return_value={"sku": "NS-001", "ok": True})
    price_invoke = Mock(return_value={"sku": "NS-001", "price": 99.0})
    fraud_invoke = Mock(
        return_value=(
            '{"decision": "ALLOW", "llm_decision": "ALLOW", "source": "store_tool"}'
        )
    )
    order = {"id": "order-1", "status": "PENDING"}
    paid_order = {**order, "status": "PAID"}
    payment = {"paid": True, "reason": "approved", "latency_ms": 1}
    notification = {"sent": True, "latency_ms": 1}

    monkeypatch.setattr(
        fulfillment_workflow,
        "check_inventory_tool",
        Mock(invoke=inventory_invoke),
    )
    monkeypatch.setattr(
        fulfillment_workflow,
        "get_price_tool",
        Mock(invoke=price_invoke),
    )
    monkeypatch.setattr(
        fulfillment_workflow,
        "decide_fraud_allow_or_block_tool",
        Mock(invoke=fraud_invoke),
    )
    confirm_invoke = Mock(return_value='{"stock_ok": true, "item_count": 1}')
    payment_invoke = Mock(return_value=json.dumps(payment))
    notification_invoke = Mock(return_value=json.dumps(notification))
    monkeypatch.setattr(
        fulfillment_workflow, "confirm_cart_stock_tool", Mock(invoke=confirm_invoke),
    )
    monkeypatch.setattr(
        fulfillment_workflow, "charge_payment_tool", Mock(invoke=payment_invoke),
    )
    monkeypatch.setattr(fulfillment_workflow.tools, "decrement_stock", Mock())
    transition = Mock(return_value=paid_order)
    monkeypatch.setattr(fulfillment_workflow.orders, "transition", transition)
    monkeypatch.setattr(
        fulfillment_workflow, "send_order_notification_tool", Mock(invoke=notification_invoke),
    )

    result = fulfillment_workflow.run_fulfillment_workflow(
        [{"sku": "NS-001", "qty": 1, "price": 99.0}],
        99.0,
        order=order,
    )

    assert result == {
        "allow": True,
        "quote": {"sku": "NS-001", "price": 99.0},
        "fraud": {
            "decision": "ALLOW",
            "llm_decision": "ALLOW",
            "source": "store_tool",
        },
        "inventory": {"sku": "NS-001", "ok": True},
        "failure_reason": None,
        "order": paid_order,
    }
    confirm_invoke.assert_called_once()
    payment_invoke.assert_called_once()
    fulfillment_workflow.tools.decrement_stock.assert_called_once_with(
        [{"sku": "NS-001", "qty": 1, "price": 99.0}]
    )
    transition.assert_called_once_with("order-1", "PAID", failure_reason=None)
    notification_invoke.assert_called_once()
