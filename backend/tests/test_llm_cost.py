"""Camada de controle de custo de LLM (F-022) — ex `run_cost_demo.py`.

Sem rede (só o stub, standalone): cache miss→hit, single-flight, rate-limit, max_tokens e o
status `cache` devolvido por `feature_complete`. Com `LLM_CACHE_ENABLED=0` o módulo opera em
modo sempre-miss — os testes que dependem de hit são pulados nesse caso.
"""
from __future__ import annotations

import threading

import pytest

from app.llm import llm_cache
from app.ai_agents.product_qa import answer_product_question
from app.ai_agents.store_discovery import stable_skus
from app.llm.llm import get_llm

cache_on = pytest.mark.skipif(
    not llm_cache.cache_globally_enabled(),
    reason="LLM_CACHE_ENABLED=0 — sem hit/single-flight pra observar",
)


@cache_on
def test_identical_prompt_normalizes_to_a_cache_hit(clean_cache):
    llm = get_llm()  # sem provider configurado → stub
    r1, s1 = clean_cache.complete_cached(llm, "demo", "sys", "qual o melhor fone?")
    r2, s2 = clean_cache.complete_cached(llm, "demo", "sys", "Qual o melhor   FONE?")
    assert (s1, s2) == ("miss", "hit")
    assert r1.text == r2.text
    assert len(clean_cache._cache) == 1


@cache_on
def test_single_flight_dedupes_concurrent_identical_calls(clean_cache):
    results: list[str] = []
    lock = threading.Lock()

    def worker():
        _, status = clean_cache.complete_cached(get_llm(), "sf", "sys", "mesma pergunta")
        with lock:
            results.append(status)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("miss") == 1, results
    assert results.count("hit") == 7, results


@cache_on
def test_cache_key_includes_system_max_tokens_and_verbose(clean_cache):
    llm = get_llm()
    r_a, s_a = clean_cache.complete_cached(llm, "k", "sys-A", "mesma pergunta", max_tokens=50)
    _, s_b = clean_cache.complete_cached(llm, "k", "sys-B", "mesma pergunta", max_tokens=50)
    r_c, s_c = clean_cache.complete_cached(llm, "k", "sys-A", "mesma pergunta", max_tokens=50)
    _, s_d = clean_cache.complete_cached(llm, "k", "sys-A", "mesma pergunta", max_tokens=100)
    _, s_e = clean_cache.complete_cached(llm, "k", "sys-A", "mesma pergunta", max_tokens=50, verbose=True)

    assert (s_a, s_b) == ("miss", "miss"), "system diferente tem que ser chave diferente"
    assert s_c == "hit" and r_a.text == r_c.text
    assert s_d == "miss", "max_tokens diferente tem que ser chave diferente"
    assert s_e == "miss", "verbose diferente tem que ser chave diferente"
    assert len(clean_cache._cache) == 4


@cache_on
def test_product_qa_stays_available_without_the_retired_feature_dispatcher(clean_cache):
    answer = answer_product_question("NS-001", "Tell me about the headphones")
    assert answer and answer["answer"]
    assert answer["grounded"] is True


@cache_on
def test_updating_an_agent_prompt_invalidates_the_cache(clean_cache):
    from app.hub import agent_config

    agent_config.init_db()
    agent_config.seed_defaults()
    clean_cache.complete_cached(get_llm(), "inv", "sys", "q")
    assert len(clean_cache._cache) == 1
    current = agent_config.get_agent("product_qa")
    agent_config.update_agent("product_qa", system_prompt=current["system_prompt"])
    assert len(clean_cache._cache) == 0


@pytest.mark.skipif(
    llm_cache.cache_globally_enabled(), reason="só vale com LLM_CACHE_ENABLED=0",
)
def test_disabled_cache_always_misses(clean_cache):
    llm = get_llm()
    statuses = [clean_cache.complete_cached(llm, "off", "sys", f"q{i}")[1] for i in range(4)]
    assert all(s == "miss" for s in statuses), statuses
    assert len(clean_cache._cache) == 0

    first = answer_product_question("NS-001", "Tell me about the headphones")
    second = answer_product_question("NS-001", "Tell me about the headphones")
    assert first and second and first["answer"] and second["answer"]
    assert len(clean_cache._cache) == 0


def test_rate_limit_degrades_to_stub_without_caching(clean_cache):
    clean_cache._limiter = clean_cache.RateLimiter(maxn=2, window=60)
    statuses = [clean_cache.complete_cached(get_llm(), f"rl{i}", "sys", f"q{i}")[1] for i in range(5)]
    assert statuses.count("miss") == 2, statuses
    assert statuses.count("rate_limited") == 3, statuses


def test_invoke_feature_llm_rate_limits_to_stub(clean_cache):
    from app.llm.agent_llm_invoke import invoke_feature_llm
    from app.llm.llm_models import is_stub_output

    clean_cache._limiter = clean_cache.RateLimiter(maxn=2, window=60)
    invoke_feature_llm("product_qa", "sys", "q1", run_name="test.rate")
    invoke_feature_llm("product_qa", "sys", "q2", run_name="test.rate")
    third = invoke_feature_llm("product_qa", "sys", "q3", run_name="test.rate")
    assert is_stub_output(third.text), third.text


def test_max_tokens_caps_the_output(clean_cache):
    small, _ = clean_cache.complete_cached(get_llm(), "mt", "sys", "a", max_tokens=5)
    big, _ = clean_cache.complete_cached(get_llm(), "mt", "sys", "b", max_tokens=200)
    assert small.output_tokens < big.output_tokens


def test_stable_sku_list_is_order_independent():
    assert stable_skus(["NS-002", "NS-001"]) == stable_skus(["NS-001", "NS-002"]) == [
        "NS-001", "NS-002",
    ]
