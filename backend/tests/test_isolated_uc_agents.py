"""Contract, behavior, and import-boundary tests for the isolated UC-1/UC-5 workflows."""
from __future__ import annotations

import ast
from pathlib import Path

from app.ai_agents import fraud_explanation, notification_copy, product_qa
from app.llm.llm import LLMResult
from app.problems import FLAGS
from app.store.tools import CATALOG


AGENT_DIR = Path(__file__).parents[1] / "app" / "ai_agents"
WORKFLOW_SOURCES = (
    "product_qa.py",
    "notification_copy.py",
    "fraud_explanation.py",
)


def _result(text: str, system: str = "test") -> tuple[LLMResult, str]:
    return LLMResult(text, 1, 1, "test-model", system=system), "miss"


def test_isolated_workflows_export_galileo_step_callables():
    assert product_qa.product_qa is product_qa.answer_product_question
    assert product_qa.answer_product_question.__name__ == "answer_product_question"
    assert notification_copy.notification_copy is notification_copy.compose_notification_text
    assert notification_copy.compose_notification_text.__name__ == "compose_notification_text"
    assert fraud_explanation.fraud_explain is fraud_explanation.explain_fraud_hold
    assert fraud_explanation.explain_fraud_hold.__name__ == "explain_fraud_hold"


def test_isolated_workflows_do_not_cross_import_or_depend_on_legacy_orchestrators():
    forbidden_suffixes = (
        "features",
        "features.feature_chains",
        "hub.agent_config",
        "galileo_span",
        "obs.galileo_control",
    )
    for source_name in WORKFLOW_SOURCES:
        source_path = AGENT_DIR / source_name
        tree = ast.parse(source_path.read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            if isinstance(node, ast.ImportFrom):
                assert node.level != 1, f"{source_path.name} imports another ai_agents module"
        assert not any(
            module == blocked or module.startswith(f"{blocked}.")
            for module in imported for blocked in ("app.agents", "app.graphs")
        ), source_path.name
        assert not any(
            "ai_agents" in module for module in imported
        ), source_path.name
        assert not any(
            module == forbidden or module.endswith(f".{forbidden}")
            for module in imported for forbidden in forbidden_suffixes
        ), source_path.name


def test_product_qa_owns_the_uc1_prompt_rag_assembly_and_llm_name(monkeypatch):
    captured = {}
    monkeypatch.setattr(product_qa, "_control_is_active", lambda: False)
    monkeypatch.setattr(
        product_qa,
        "_invoke_llm",
        lambda prompt, system, **kwargs: captured.update(
            {"prompt": prompt, "system": system, **kwargs},
        ) or _result("It costs $79.99 today."),
    )
    monkeypatch.setattr(FLAGS, "price_hallucination", True)

    output = product_qa.answer_product_question(CATALOG[0]["sku"], "How much is it?")

    assert output["grounded"] is False
    assert output["answer"] == "It costs $79.99 today."
    assert captured["prompt"] == "How much is it?"
    assert captured["max_tokens"] == 160
    assert "no catalog or policy data" in captured["system"]
    assert "grounded strictly" not in captured["system"]
    assert product_qa.LLM_RUN_NAME == "feature.answer_product_question"
    assert product_qa.CONTROL_STEP_NAME == "product_qa"


def test_product_qa_ungrounded_price_with_sku_retries_after_refusal(monkeypatch):
    calls: list[str] = []

    def fake_invoke(prompt, system, **kwargs):
        calls.append(prompt)
        if len(calls) == 1:
            return _result(
                "Sorry, I don't have any information on prices for the Aura Bluetooth Headphones "
                "with product code NS-001.",
            )
        return _result("The Aura Bluetooth Headphones (NS-001) cost $9.90.")

    monkeypatch.setattr(product_qa, "_control_is_active", lambda: False)
    monkeypatch.setattr(product_qa, "_invoke_llm", fake_invoke)
    monkeypatch.setattr(FLAGS, "price_hallucination", True)

    output = product_qa.answer_product_question(CATALOG[0]["sku"], "how much does NS-001 cost?")

    assert output["grounded"] is False
    assert "$9.90" in output["answer"]
    assert len(calls) == 2
    assert calls[0] == "how much does NS-001 cost?"
    assert "costs $9.90" in calls[1]


def test_notification_copy_keeps_uc5_behavior_with_local_llm(monkeypatch):
    order = {
        "id": "ORD-TEST",
        "status": "PAID",
        "items": [{"sku": CATALOG[0]["sku"], "qty": 1}],
        "total": CATALOG[0]["price"],
        "customer": {"name": "Ada Lovelace", "email": "ada@example.test", "address": "1 Test Way"},
    }
    captured = {}
    monkeypatch.setattr(notification_copy, "_control_is_active", lambda: False)
    monkeypatch.setattr(
        notification_copy,
        "_invoke_llm",
        lambda prompt, **kwargs: captured.update({"prompt": prompt, **kwargs}) or _result(
            "{\"subject\": \"Confirmed\", \"body\": \"Your order is ready.\"}",
        ),
    )

    output = notification_copy.compose_notification_text(order)

    assert output == {
        "subject": "Confirmed", "body": "Your order is ready.", "channel": "email",
        "event": "confirmation", "grounded": True,
    }
    assert "Ada" in captured["prompt"]
    assert "ada@example.test" not in captured["prompt"].lower()
    assert "1 Test Way" not in captured["prompt"]
    assert notification_copy.LLM_RUN_NAME == "feature.compose_notification_text"
    assert notification_copy.CONTROL_STEP_NAME == "notification_copy"


def test_notification_copy_grounded_prompt_omits_sensitive_recipient_fields(monkeypatch):
    order = {
        "id": "ORD-UC5",
        "status": "DELIVERED",
        "items": [{"sku": CATALOG[0]["sku"], "qty": 1, "name": CATALOG[0]["name"]}],
        "total": CATALOG[0]["price"],
        "customer": {
            "name": "Jane Doe",
            "email": "jane@example.test",
            "address": "123 Main St, Sao Paulo",
            "ssn": "123-45-6789",
            "card_number": "4242 4242 4242 4242",
            "card_exp": "08/28",
            "card_cvv": "123",
        },
    }
    captured = {}
    monkeypatch.setattr(notification_copy, "_control_is_active", lambda: False)
    monkeypatch.setattr(
        notification_copy,
        "_invoke_llm",
        lambda prompt, **kwargs: captured.update({"prompt": prompt, **kwargs}) or _result(
            '{"subject": "Shipped", "body": "Hi Jane, your order is on its way."}',
        ),
    )
    output = notification_copy.compose_notification_text(order)
    assert output["grounded"] is True
    prompt = captured["prompt"].lower()
    assert "jane" in prompt
    assert "123-45-6789" not in prompt
    assert "4242" not in prompt
    assert "jane@example.test" not in prompt
    assert "123 main st" not in prompt


def test_fraud_explanation_keeps_its_legacy_contract_and_galileo_name(monkeypatch):
    order = {"id": "ORD-TEST", "total": CATALOG[0]["price"]}
    captured = {}
    monkeypatch.setattr(
        fraud_explanation,
        "_invoke_llm",
        lambda prompt, **kwargs: captured.update({"prompt": prompt, **kwargs}) or fraud_explanation.LLMResult(
            "Routine review.", 1, 1, "test-model", system="test",
        ),
    )
    monkeypatch.setattr(FLAGS, "fraud_false_positive", True)

    assert fraud_explanation.explain_fraud_hold(order) == {
        "explanation": "Routine review.", "fraud": True,
    }
    assert "was held for a routine security review" in captured["prompt"]
    assert fraud_explanation.LLM_RUN_NAME == "feature.explain_fraud_hold"
    assert fraud_explanation.CONTROL_STEP_NAME == "fraud_explain"
