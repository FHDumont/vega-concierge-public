"""Abstraction of the LLM config source (F-020, stage 4 — ADR-015; remote: F-026, ADR-019).

Isolates WHERE the cascading provider config comes from, so the **remote** source (config
served by another store / hub) plugs in **without touching consumers** (`llm.get_llm` always calls
`llm._load_provider_configs`, which delegates to the active source here).

Two sources: **local** (this store's SQLite, via `llm_config`) and **remote** (`RemoteConfigSource`,
F-026: pull from a hub URL + enrollment token, with **resilient cache** — if the hub goes down,
continues with the last config). The owner chooses local|remote (persisted in `hub_settings`); the only
switchover point is `set_active_source` — nothing else changes.

`get_llm_config()` returns providers WITH keys (internal cascade use) — the API layer
never calls this directly; it uses `llm_config` (masked). Keys remain secrets.
"""
import json
import time
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable

from ..llm import llm_config

# Pull timeout to hub (s). Short: if hub is slow, falls back to cache (resilience).
_PULL_TIMEOUT_S = 10


@runtime_checkable
class ConfigSource(Protocol):
    """Contract for an LLM config source. The remote variant is `RemoteConfigSource`."""

    name: str

    def get_llm_config(self) -> list[dict]:
        """Enabled cascade providers, in order, WITH keys. `[]` = stub only."""
        ...

    def get_flags(self) -> dict:
        """Menu/surface feature flags from source (F-033). `{}` = no opinion (uses defaults)."""
        ...


class LocalConfigSource:
    """LOCAL source: providers persisted in this store's SQLite (`llm_config`)."""
    name = "local"

    def get_llm_config(self) -> list[dict]:
        return llm_config.list_enabled_with_keys()

    def get_flags(self) -> dict:
        from . import feature_flags  # lazy: avoids cycle (feature_flags imports this module)
        return feature_flags.get_local_flags()


class RemoteConfigSource:
    """REMOTE source (F-026): pulls config from a hub (another store) via HTTP + token.

    **Resilient:** keeps in memory the last successfully pulled config; if the hub becomes
    unavailable, `get_llm_config()` returns the cache (app never breaks). Refresh is
    **lazy** (on read, if interval passed) + **on-demand** (`sync_now`).

    Anti-loop (F-026): pull sends `X-Hub-Chain` with this store's identity; hub rejects
    (409) if it's already in the chain — breaks hub↔hub cycles. In-memory cache (resets on
    restart, like DT-010/DT-007); until first successful pull the cascade has stub only.
    """
    name = "remote"

    def __init__(self, hub_url: str, token: str, env: str, interval_s: int = 45):
        self.hub_url = hub_url
        self.token = token
        self.env = env  # this store's identity (deployment.environment) for anti-loop
        self.interval_s = max(5, int(interval_s))
        self._cache: list[dict] = []
        self._flags_cache: dict = {}  # feature flags served by hub (F-033); {} until first pull
        self._has_cache = False
        self._last_fetch = 0.0      # monotonic time of last successful pull
        self._last_attempt = 0.0
        self.last_ok = False
        self.last_error: str | None = None
        self.last_sync_iso: str | None = None  # wall-clock time of last success (for status screen)
        self.hub_env: str | None = None         # identity of responding hub

    def _maybe_refresh(self) -> None:
        # Lazy refresh: only pulls if interval has passed since last successful pull.
        if (time.monotonic() - self._last_fetch) >= self.interval_s:
            self._refresh()

    def get_llm_config(self) -> list[dict]:
        self._maybe_refresh()
        return list(self._cache)

    def get_flags(self) -> dict:
        # Same cascade resilience: hub flags come in the same pull and stay cached
        # (F-033). Front polling (/api/flags) keeps propagation within interval.
        self._maybe_refresh()
        return dict(self._flags_cache)

    def sync_now(self) -> dict:
        """On-demand pull ('sync now' button). Returns result status."""
        self._refresh()
        return self.status()

    def _refresh(self) -> None:
        self._last_attempt = time.monotonic()
        try:
            providers, hub_env, flags = self._pull()
            self._cache = providers
            self._flags_cache = flags
            self._has_cache = True
            self._last_fetch = time.monotonic()
            self.last_ok = True
            self.last_error = None
            self.hub_env = hub_env
            self.last_sync_iso = _now_iso()
        except Exception as exc:  # network/timeout/HTTP/parse → keeps cache (resilience)
            self.last_ok = False
            self.last_error = _short_error(exc)

    def _pull(self) -> tuple[list[dict], str | None, dict]:
        if not self.hub_url:
            raise ValueError("hub_url empty")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Hub-Chain": self.env,  # anti-loop: hub refuses if our identity is already in chain
            "X-Hub-Env": self.env,    # this store's identity → hub tracks the client
            "Accept": "application/json",
        }
        req = urllib.request.Request(self.hub_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=_PULL_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
        providers = data.get("providers", []) if isinstance(data, dict) else []
        # Normalizes only fields the cascade uses (ignores extras from wire).
        norm = [{"id": p.get("id", ""), "name": p.get("name", ""), "kind": p.get("kind", "openai"),
                 "base_url": p.get("base_url", ""), "model": p.get("model", ""),
                 "api_key": p.get("api_key", "")} for p in providers if isinstance(p, dict)]
        raw_flags = data.get("flags") if isinstance(data, dict) else None
        flags = {k: bool(v) for k, v in raw_flags.items()} if isinstance(raw_flags, dict) else {}
        return norm, (data.get("hub_env") if isinstance(data, dict) else None), flags

    def status(self) -> dict:
        """Connection health for status screen (no secrets)."""
        return {
            "hub_url": self.hub_url,
            "interval_s": self.interval_s,
            "has_cache": self._has_cache,
            "cached_providers": len(self._cache),
            "cached_flags": len(self._flags_cache),  # number of flags served by hub (F-033)
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "last_sync": self.last_sync_iso,
            "hub_env": self.hub_env,
            # Keys travel on this channel (DT-013): non-local HTTP is insecure (alerts owner).
            "insecure": self._is_insecure(),
        }

    def _is_insecure(self) -> bool:
        u = (self.hub_url or "").lower()
        if u.startswith("https://"):
            return False
        return "localhost" not in u and "127.0.0.1" not in u


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _short_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return f"unreachable: {getattr(exc, 'reason', exc)}"
    return type(exc).__name__


# Active source (swappable singleton). Default = local; F-021 switches via set_active_source.
_active: ConfigSource = LocalConfigSource()


def get_active_source() -> ConfigSource:
    return _active


def set_active_source(source: ConfigSource) -> None:
    """Extension point for F-021: owner chooses source (local|remote) and this applies it.
    Consumers (`llm.get_llm`) don't change — they now resolve the cascade from the new source."""
    global _active
    _active = source
