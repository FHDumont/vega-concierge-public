"""Smoke da camada de controle de custo de LLM (F-022, etapa 1).

Verifica, sem rede (só StubLLM, standalone): cache miss→hit, single-flight (dedupe de
idênticas concorrentes), rate-limit (degrada p/ stub sem gasto), max_tokens, e o status
`cache` devolvido por `feature_complete`. Rodar: `.venv/bin/python run_cost_demo.py` (de backend/).

Com `LLM_CACHE_ENABLED=0`, só valida modo sempre-miss (sem hit/single-flight acumulando cache).
"""
import os
import sys
import threading

os.environ.setdefault("DEPLOYMENT_ENVIRONMENT", "user-42")

from app import llm_cache
from app.llm import get_llm
from app.agents import feature_complete

failures = []


def check(label, cond):
    print(f"  [{'ok' if cond else 'FAIL'}] {label}", file=sys.stderr)
    if not cond:
        failures.append(label)


cache_enabled = llm_cache.cache_globally_enabled()

if cache_enabled:
    print("== cache miss → hit ==", file=sys.stderr)
    llm_cache.reset_state()
    llm = get_llm()  # só stub (sem provider configurado)
    r1, s1 = llm_cache.complete_cached(llm, "demo", "sys", "qual o melhor fone?")
    r2, s2 = llm_cache.complete_cached(llm, "demo", "sys", "Qual o melhor   FONE?")  # normaliza igual
    check("1ª chamada = miss", s1 == "miss")
    check("2ª chamada (normalizada) = hit", s2 == "hit")
    check("hit reusa o mesmo texto", r1.text == r2.text)
    check("cache tem 1 entrada", len(llm_cache._cache) == 1)

    print("== single-flight (idênticas concorrentes dedupam) ==", file=sys.stderr)
    llm_cache.reset_state()
    results = []
    lock = threading.Lock()

    def worker():
        r, s = llm_cache.complete_cached(get_llm(), "sf", "sys", "mesma pergunta")
        with lock:
            results.append(s)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    misses = results.count("miss")
    check(f"exatamente 1 miss entre 8 concorrentes (got {misses})", misses == 1)
    check("as demais 7 são hit", results.count("hit") == 7)

    print("== chave inclui system / max_tokens / verbose ==", file=sys.stderr)
    llm_cache.reset_state()
    llm = get_llm()
    r_a, s_a = llm_cache.complete_cached(llm, "k", "sys-A", "mesma pergunta", max_tokens=50)
    r_b, s_b = llm_cache.complete_cached(llm, "k", "sys-B", "mesma pergunta", max_tokens=50)  # system diferente
    r_c, s_c = llm_cache.complete_cached(llm, "k", "sys-A", "mesma pergunta", max_tokens=50)  # hit de r_a
    r_d, s_d = llm_cache.complete_cached(llm, "k", "sys-A", "mesma pergunta", max_tokens=100)  # max_tokens
    r_e, s_e = llm_cache.complete_cached(llm, "k", "sys-A", "mesma pergunta", max_tokens=50, verbose=True)
    check("system diferente → miss", s_a == "miss" and s_b == "miss")
    check("mesmo system → hit", s_c == "hit" and r_a.text == r_c.text)
    check("max_tokens diferente → miss", s_d == "miss")
    check("verbose diferente → miss", s_e == "miss")
    check(f"4 entradas de chave distintas (got {len(llm_cache._cache)})", len(llm_cache._cache) == 4)

    print("== feature_complete devolve status de cache ==", file=sys.stderr)
    llm_cache.reset_state()
    _, _, s_first = feature_complete("product_qa", "Tell me about the headphones")
    _, _, s_second = feature_complete("product_qa", "Tell me about the headphones")  # idêntica → hit
    check(f"status = [miss, hit] (got {[s_first, s_second]})", [s_first, s_second] == ["miss", "hit"])
else:
    print("== cache disabled (LLM_CACHE_ENABLED=0) — skip hit/single-flight/key tests ==", file=sys.stderr)
    llm_cache.reset_state()
    llm = get_llm()
    statuses = []
    for i in range(4):
        _, s = llm_cache.complete_cached(llm, "off", "sys", f"q{i}")
        statuses.append(s)
    check(f"todas as chamadas = miss (got {statuses})", all(s == "miss" for s in statuses))
    check("_cache vazio após sequência", len(llm_cache._cache) == 0)
    llm_cache.reset_state()
    _, _, s_a = feature_complete("product_qa", "Tell me about the headphones")
    _, _, s_b = feature_complete("product_qa", "Tell me about the headphones")
    check(f"feature_complete sempre miss (got {[s_a, s_b]})", s_a == "miss" and s_b == "miss")
    check("_cache vazio após feature_complete", len(llm_cache._cache) == 0)

print("== rate-limit degrada p/ stub (sem cachear) ==", file=sys.stderr)
llm_cache.reset_state()
llm_cache._limiter = llm_cache.RateLimiter(maxn=2, window=60)
statuses = [llm_cache.complete_cached(get_llm(), f"rl{i}", "sys", f"q{i}")[1] for i in range(5)]
check(f"2 misses + 3 rate_limited (got {statuses})",
      statuses.count("miss") == 2 and statuses.count("rate_limited") == 3)

print("== max_tokens limita o tamanho ==", file=sys.stderr)
llm_cache.reset_state()
small, _ = llm_cache.complete_cached(get_llm(), "mt", "sys", "a", max_tokens=5)
big, _ = llm_cache.complete_cached(get_llm(), "mt", "sys", "b", max_tokens=200)
check(f"max_tokens menor → menos output_tokens ({small.output_tokens} < {big.output_tokens})",
      small.output_tokens < big.output_tokens)

if cache_enabled:
    print("== update_agent limpa cache + SKUs estáveis ==", file=sys.stderr)
    from app import agent_config
    from app.ai_features import _stable_sku_list
    llm_cache.reset_state()
    llm_cache.complete_cached(get_llm(), "inv", "sys", "q")
    check("cache tem 1 entrada antes do update", len(llm_cache._cache) == 1)
    agent_config.update_agent("product_qa", system_prompt=None)  # no-op de campos → não limpa? None = keep
    cur = agent_config.get_agent("product_qa")
    agent_config.update_agent("product_qa", system_prompt=cur["system_prompt"])
    check("update_agent com system_prompt limpa cache", len(llm_cache._cache) == 0)
    check("stable skus same set different order",
          _stable_sku_list(["NS-002", "NS-001"]) == _stable_sku_list(["NS-001", "NS-002"]) == ["NS-001", "NS-002"])

print(f"\n{'PASS' if not failures else 'FAIL: ' + ', '.join(failures)}", file=sys.stderr)
sys.exit(1 if failures else 0)
