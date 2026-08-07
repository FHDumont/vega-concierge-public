"""Standalone fraud-hold explanation (`feature.explain_fraud_hold`)."""
from __future__ import annotations

import time

from ..llm.agent_llm_invoke import LLMResult, invoke_feature_llm, is_stub_output
from ..problems import FLAGS
from ..store.catalog_format import _usd

CONTROL_STEP_NAME = "fraud_explain"
LLM_RUN_NAME = "feature.explain_fraud_hold"
_SYSTEM_PROMPT = (
    "You explain a routine fraud-review hold calmly and concisely. Do not invent specific fraud, "
    "payment, or customer details. Reply in English without markdown."
)


def is_unavailable(result: LLMResult) -> bool:
    return result.system in {"stub", "error"} or is_stub_output(result.text)


def invoke_llm(system: str, prompt: str, *, run_name: str, max_tokens: int, verbose: bool = False, config=None) -> LLMResult:
    """Observable provider cascade for fraud-hold explanations."""
    return invoke_feature_llm(
        CONTROL_STEP_NAME,
        system,
        prompt,
        run_name=run_name,
        max_tokens=max_tokens,
        verbose=verbose,
        config=config,
    )


def _fallback(order: dict, grounded: bool) -> str:
    if not grounded:
        return (
            "Your card was declined because the billing ZIP didn't match — please update it "
            "and the order will go through."
        )
    return (
        f"Order {order.get('id', 'unknown')} was held for a quick security review and wasn't "
        "charged. This is just a routine precaution — you can try again or reach out to support."
    )


def _invoke_llm(prompt: str, *, config=None) -> LLMResult:
    return invoke_llm(
        _SYSTEM_PROMPT, prompt, run_name=LLM_RUN_NAME, max_tokens=140,
        verbose=FLAGS.cost_spike, config=config,
    )


def explain_fraud_hold(order: dict, *, config=None) -> dict:
    """Return the legacy fraud-explanation payload without legacy feature dependencies."""
    fraud = FLAGS.fraud_false_positive
    grounded = not FLAGS.price_hallucination
    if FLAGS.latency_spike:
        time.sleep(1.2)
    prompt = (
        f"An order ({order.get('id', 'unknown')}, total {_usd(order.get('total', 0))}) was held for "
        "a routine security review and was NOT charged. Reassure the customer in 1-2 sentences: "
        "explain calmly that this is a precaution and that they can retry or contact support. Do "
        "not invent a specific reason. Reply in English. No markdown."
        if grounded else
        f"An order ({order.get('id', 'unknown')}) could not be completed. Tell the customer the "
        "exact reason in 1-2 sentences and how to fix it. Reply in English. No markdown."
    )
    result = _invoke_llm(prompt, config=config)
    text = _fallback(order, grounded) if is_unavailable(result) else result.text
    return {"explanation": text.strip(), "fraud": fraud}


fraud_explain = explain_fraud_hold
