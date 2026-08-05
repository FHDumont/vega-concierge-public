"""Grafo do Concierge sob stub — ex `run_demo.py` (smoke histórico do backend)."""
from __future__ import annotations

import pytest

from app.agents import _parse_json, build_graph
from app.runnable_config import build_runnable_config, make_thread_id


def _run(request: str = "a birthday gift under $300") -> dict:
    config = build_runnable_config(thread_id=make_thread_id(), feature="concierge")
    return build_graph().invoke(
        {"request": request, "messages": [], "trace": []},
        config=config,
    )


def test_parse_json_strips_markdown_fences():
    # C5 — o roteamento vem em ```json ... ``` de vários modelos.
    assert _parse_json('```json\n{"next_agent":"complete"}\n```') == {"next_agent": "complete"}


def test_happy_path_selects_a_grounded_candidate():
    final = _run()
    candidate_skus = {c["sku"] for c in final.get("candidates") or []}
    selected = final.get("selected")
    assert (final.get("quality") or {}).get("grounded") is True
    assert selected, "esperado um produto selecionado"
    assert selected.get("sku") in candidate_skus


def test_price_hallucination_marks_answer_ungrounded(reset_problem_flags):
    reset_problem_flags.price_hallucination = True
    final = _run()
    assert (final.get("quality") or {}).get("grounded") is False


@pytest.mark.parametrize("flag", ["cost_spike"])
def test_graph_still_completes_under_toggle(reset_problem_flags, flag):
    setattr(reset_problem_flags, flag, True)
    final = _run()
    assert final.get("trace"), f"{flag}: trace vazio"
