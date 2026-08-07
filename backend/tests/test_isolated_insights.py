"""Contract and import-boundary coverage for standalone insight workflows."""
from __future__ import annotations

import ast
import inspect

from app.ai_agents import insights
from app.llm.llm import LLMResult
from app.problems import FLAGS


def _result(text: str, system: str = "test") -> tuple[LLMResult, str]:
    return LLMResult(text, 1, 1, "test-model", system=system), "miss"


def test_insights_do_not_depend_on_legacy_features_agents_or_graphs():
    tree = ast.parse(inspect.getsource(insights))
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

    for blocked in ("app.agents", "app.features", "app.graphs"):
        assert not any(
            module == blocked or module.startswith(f"{blocked}.")
            for module in imports | direct_imports
        )


def test_admin_insights_preserves_contract_with_locally_owned_llm(monkeypatch):
    monkeypatch.setattr(insights, "_control_is_active", lambda: False)
    monkeypatch.setattr(insights.orders, "list_orders", lambda: [])
    captured = {}
    monkeypatch.setattr(
        insights,
        "_invoke_llm",
        lambda control_step, run_name, prompt, **kwargs: captured.update(
            {"control_step": control_step, "run_name": run_name, **kwargs},
        ) or _result('{"summary":"Sales are steady.","anomalies":["Inventory low."]}'),
    )

    config = {"metadata": {"request_id": "admin-insights"}}
    output = insights.admin_insights(config=config)

    assert set(output) == {"period_days", "metrics", "summary", "anomalies", "restock"}
    assert output["summary"] == "Sales are steady."
    assert output["anomalies"] == ["Inventory low."]
    assert insights.ADMIN_CONTROL_STEP_NAME == "admin_insights"
    assert insights.ADMIN_LLM_RUN_NAME == "feature.admin_insights"
    assert captured == {
        "control_step": "admin_insights",
        "run_name": "feature.admin_insights",
        "config": config,
    }


def test_account_insights_preserves_contract_with_locally_owned_llm(monkeypatch):
    monkeypatch.setattr(insights, "_control_is_active", lambda: False)
    monkeypatch.setattr(FLAGS, "price_hallucination", False)
    monkeypatch.setattr(
        insights,
        "_invoke_llm",
        lambda control_step, run_name, prompt, **kwargs: _result(
            '{"summary":"You have shopped with us.","tier_benefits":"Gold perks apply.",'
            '"repurchase":"Consider another headset."}',
        ),
    )
    user = {"name": "Ada", "tier": "GOLD", "spend": 200.0}
    user_orders = [{
        "status": "PAID",
        "items": [{"sku": "NS-001", "name": "Headset", "qty": 1}],
    }]

    output = insights.account_insights(user, user_orders)

    assert output == {
        "summary": "You have shopped with us.",
        "tier_benefits": "Gold perks apply.",
        "repurchase": "Consider another headset.",
        "grounded": True,
    }
    assert insights.ACCOUNT_CONTROL_STEP_NAME == "account_insights"
    assert insights.ACCOUNT_LLM_RUN_NAME == "feature.account_insights"


def test_simulator_concierge_path_uses_the_isolated_workflow():
    from app.sim import simulator

    source = inspect.getsource(simulator._run_concierge)

    assert "ai_agents.concierge_workflow" in source
    assert "from ..agents" not in source
