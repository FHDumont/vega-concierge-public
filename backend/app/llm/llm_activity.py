"""LLM Inspector (F-023) — captura LOCAL de atividade de LLM.

Buffer EM MEMÓRIA por VM que registra, por chamada de LLM, o **conteúdo completo** (system + user
prompt e resposta) + metadados (feature/agente, modelo, provider, tokens in/out, cache,
latência, timestamp). É uma lupa de inspeção/debug local.

Princípios:
- **Conteúdo local:** o conteúdo de prompt fica LOCAL (owner-only). Captura SEMPRE que ligado.
- **Owner-only:** a leitura é gated a OWNER na API (F-020). Não aparece p/ participantes.
- **Desligável = feature flag `inspector` (F-033):** o "desligável" do F-023 VIROU a feature flag
  `inspector` (ADR-021), servida pela fonte de config (local/hub). `is_enabled()` lê a flag
  EFETIVA → em modo `remote` quem desliga o Inspector é o **hub** (propaga p/ as 150 VMs).
  Desligado → `record` é no-op (buffer congela). Sem mais estado em memória próprio.
- **Ring buffer:** `deque(maxlen)` por VM (tamanho `LLM_ACTIVITY_MAX`, default 200); reseta no
  restart (como os demais estados em memória — DT-007/DT-010). Thread-safe (endpoints sync rodam
  em threadpool; o simulador grava concorrente).
"""
import threading
from collections import deque
from datetime import datetime, timezone
from ..settings import settings

# Tamanho do ring buffer (últimas N chamadas) — configurável (decisão em aberto da spec).
ACTIVITY_MAX = settings.llm_activity_max

_lock = threading.Lock()
_buf: deque = deque(maxlen=ACTIVITY_MAX)
_counter = 0  # id incremental por entrada (key estável p/ a UI + ordenação)


def is_enabled() -> bool:
    """Captura ligada? = feature flag EFETIVA `inspector` (F-033 — local ou servida pelo hub).
    Tolerante (defaults ON) antes do init_db / fora da app (smoke)."""
    from ..hub import feature_flags  # lazy: evita ciclo no import
    try:
        return bool(feature_flags.effective_flags().get("inspector", True))
    except Exception:
        return True


def set_enabled(enabled: bool) -> bool:
    """Liga/desliga a captura (owner) editando a flag LOCAL `inspector` (F-033). Em modo `remote`
    o hub vence — a flag efetiva pode não mudar (precedência ADR-021). Devolve a efetiva."""
    from ..hub import feature_flags  # lazy
    feature_flags.update_flags(inspector=bool(enabled))
    return is_enabled()


def record(*, feature: str, system: str, prompt: str, response: str, model: str,
           provider: str, family: str, input_tokens: int, output_tokens: int,
           cache: str | None = None, latency_ms: float = 0.0,
           fallback: bool = False, prompt_cache_tokens: int = 0) -> None:
    """Registra UMA chamada de LLM no buffer (no-op se desligado). Guarda o conteúdo
    completo (local). Chamado por `agents.py` após cada `complete` — pipeline (cache=None)
    e features de loja (cache=hit|miss|rate_limited)."""
    if not is_enabled():
        return
    global _counter
    with _lock:
        _counter += 1
        _buf.appendleft({
            "id": _counter,
            "ts": datetime.now(timezone.utc).isoformat(),
            "feature": feature,
            "model": model,
            "provider": provider,
            "family": family,                 # família do provider (openai|anthropic|stub)
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "prompt_cache_tokens": int(prompt_cache_tokens or 0),  # provider prompt-cache (F-COST-CACHE)
            "cache": cache,                   # hit|miss|rate_limited|None (pipeline não cacheia)
            "latency_ms": round(float(latency_ms), 1),
            "fallback": bool(fallback),       # caiu p/ fallback na cascata?
            "system": system or "",
            "prompt": prompt or "",
            "response": response or "",
        })


def entries() -> list[dict]:
    """Chamadas registradas, mais recentes primeiro (appendleft mantém a ordem)."""
    with _lock:
        return list(_buf)


def snapshot() -> dict:
    """Estado completo p/ a UI do Inspector: flag + capacidade + entradas."""
    return {"enabled": is_enabled(), "max": ACTIVITY_MAX, "entries": entries()}


def clear() -> None:
    """Esvazia o buffer (botão Clear / reset entre turmas)."""
    with _lock:
        _buf.clear()
