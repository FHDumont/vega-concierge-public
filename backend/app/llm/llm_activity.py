"""LLM Inspector (F-023) — LOCAL capture of LLM activity.

Per-VM in-memory buffer recording, per LLM call, **full content** (system + user
prompt and response) + metadata (feature/agent, model, provider, tokens in/out, cache,
latency, timestamp). A local inspection/debug magnifying glass.

Principles:
- **Local content:** prompt content stays LOCAL (owner-only). Captures ALWAYS when enabled.
- **Owner-only:** read is gated to OWNER in API (F-020). Not visible to participants.
- **Toggleable = feature flag `inspector` (F-033):** the "toggleable" of F-023 BECAME the feature flag
  `inspector` (ADR-021), served by config source (local/hub). `is_enabled()` reads the EFFECTIVE flag
  → in `remote` mode the **hub** toggles the Inspector (propagates to 150 VMs).
  Off → `record` is no-op (buffer freezes). No more local in-memory state.
- **Ring buffer:** `deque(maxlen)` per VM (size `LLM_ACTIVITY_MAX`, default 200); resets on
  restart (like other in-memory state — DT-007/DT-010). Thread-safe (sync endpoints run
  in threadpool; simulator writes concurrently).
"""
import threading
from collections import deque
from datetime import datetime, timezone
from ..settings import settings

# Ring buffer size (last N calls) — configurable (spec decision pending).
ACTIVITY_MAX = settings.llm_activity_max

_lock = threading.Lock()
_buf: deque = deque(maxlen=ACTIVITY_MAX)
_counter = 0  # incremental id per entry (stable key for UI + ordering)


def is_enabled() -> bool:
    """Capture on? = effective feature flag `inspector` (F-033 — local or served by hub).
    Tolerant (defaults ON) before init_db / outside app (smoke)."""
    from ..hub import feature_flags  # lazy: evita ciclo no import
    try:
        return bool(feature_flags.effective_flags().get("inspector", True))
    except Exception:
        return True


def set_enabled(enabled: bool) -> bool:
    """Toggle capture (owner) by editing LOCAL flag `inspector` (F-033). In `remote` mode
    hub wins — effective flag may not change (precedence ADR-021). Returns the effective one."""
    from ..hub import feature_flags  # lazy
    feature_flags.update_flags(inspector=bool(enabled))
    return is_enabled()


def record(*, feature: str, system: str, prompt: str, response: str, model: str,
           provider: str, family: str, input_tokens: int, output_tokens: int,
           cache: str | None = None, latency_ms: float = 0.0,
           fallback: bool = False, prompt_cache_tokens: int = 0) -> None:
    """Record ONE LLM call to buffer (no-op if off). Stores full content (local).
    Called by `agents.py` after each `complete` — pipeline (cache=None)
    and store features (cache=hit|miss|rate_limited)."""
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
            "family": family,                 # provider family (openai|anthropic|stub)
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "prompt_cache_tokens": int(prompt_cache_tokens or 0),  # provider prompt-cache (F-COST-CACHE)
            "cache": cache,                   # hit|miss|rate_limited|None (pipeline doesn't cache)
            "latency_ms": round(float(latency_ms), 1),
            "fallback": bool(fallback),       # fell to fallback in cascade?
            "system": system or "",
            "prompt": prompt or "",
            "response": response or "",
        })


def entries() -> list[dict]:
    """Recorded calls, most recent first (appendleft maintains order)."""
    with _lock:
        return list(_buf)


def snapshot() -> dict:
    """Complete state for Inspector UI: flag + capacity + entries."""
    return {"enabled": is_enabled(), "max": ACTIVITY_MAX, "entries": entries()}


def clear() -> None:
    """Clear buffer (Clear button / reset between sessions)."""
    with _lock:
        _buf.clear()
