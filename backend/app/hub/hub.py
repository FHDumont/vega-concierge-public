"""Hub/peer model in SAME app (F-026, ADR-019). No 2nd application.

Connects three sides:

- **Apply source:** reads `hub_settings` and installs active `ConfigSource` (`local` or
  `remote`) via `config_source.set_active_source`. Called on boot and on every owner change.
- **Hub side (serve):** `serve_config(token, chain, client)` authenticates by enrollment token,
  **tracks the client** and returns resolved cascade config (with keys —
  **DT-013: keys travel on network**). Anti-loop via `X-Hub-Chain` chain.
- **Status:** `status()` summarizes mode (standalone | serving as hub | client of hub),
  target, health/last-sync and — on hub side — connected clients, for owner screen.

This store's identity = `DEPLOYMENT_ENVIRONMENT` (`user-NN`), reused in anti-loop and tracking.
Owner-only in everything (API gates with `_require_owner`). Client registry lives in memory
(resets on restart — same rule as DT-010).
"""
import hmac
import threading
import time
from datetime import datetime, timezone

from . import config_source, hub_settings
from ..llm import llm_config
from ..settings import settings


def _env() -> str:
    return settings.deployment_environment


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Apply active source (boot + owner change) ---------------------------

_remote: config_source.RemoteConfigSource | None = None  # current remote instance (if source=remote)


def apply_source() -> None:
    """Reads settings and installs active ConfigSource. Idempotent; callable hot."""
    global _remote
    s = hub_settings.get_settings()
    if s["source"] == "remote" and s["hub_url"]:
        _remote = config_source.RemoteConfigSource(
            hub_url=s["hub_url"], token=s["enrollment_token"],
            env=_env(), interval_s=s["pull_interval_s"],
        )
        config_source.set_active_source(_remote)
    else:
        _remote = None
        config_source.set_active_source(config_source.LocalConfigSource())


def sync_now() -> dict:
    """On-demand pull ('sync now' button). No effect if not in remote mode."""
    if _remote is None:
        return {"synced": False, "reason": "not in remote mode"}
    st = _remote.sync_now()
    return {"synced": True, **st}


# --- Hub side: client tracking ------------------------------------------

_clients: dict[str, dict] = {}  # client env → {env, last_pull, ip, agent, pulls}
_lock = threading.Lock()


def _track_client(env: str, ip: str | None, agent: str | None) -> None:
    key = env or (ip or "unknown")
    now = _now_iso()
    with _lock:
        c = _clients.get(key)
        if c is None:  # first time this client appears → mark "connected since"
            c = {"env": env or "(unknown)", "pulls": 0, "first_seen": now}
        c["ip"] = ip or c.get("ip")
        c["agent"] = agent or c.get("agent")
        c["last_pull"] = now
        c["pulls"] = c.get("pulls", 0) + 1
        _clients[key] = c


def list_clients() -> list[dict]:
    with _lock:
        return sorted(_clients.values(), key=lambda c: c.get("last_pull", ""), reverse=True)


class HubError(Exception):
    """Error serving config. `status` becomes HTTP (401 token, 409 loop)."""
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def serve_config(token: str | None, chain: str | None, client_env: str | None,
                 ip: str | None, agent: str | None) -> dict:
    """Delivers cascade config to a client. Raises `HubError` on token/loop.

    - **Token-gated:** without `serve_token` configured, or wrong token → 401.
    - **Anti-loop:** if our identity is already in `X-Hub-Chain` → 409 (cycle).
    - **Tracks** client (env/last-pull/ip/agent).
    - Returns `providers` from active source (local: local rows; remote: upstream cache —
      chaining by cache, no network recursion). **Keys go in payload (DT-013).**
    """
    settings = hub_settings.get_settings()
    serve_token = settings["serve_token"]
    # No token configured = we don't serve (independent). Compare in constant time
    # (hmac.compare_digest) to not leak token by timing — DT-013 / ADR-019.
    if not serve_token or not token or not hmac.compare_digest(token, serve_token):
        raise HubError(401, "invalid enrollment token")

    me = _env()
    chain_ids = [c.strip() for c in (chain or "").split(",") if c.strip()]
    if me in chain_ids:
        raise HubError(409, "loop detected: this store is already in the hub chain")

    _track_client(client_env, ip, agent)
    active = config_source.get_active_source()
    providers = active.get_llm_config()
    flags = active.get_flags()  # F-033: propagates menu flags alongside config (no secret)
    return {"hub_env": me, "served_at": _now_iso(), "providers": providers, "flags": flags,
            "chain": chain_ids + [me]}


# --- Status (owner screen) -------------------------------------------------

def status() -> dict:
    """Mode/target/health summary + clients. No secrets (tokens never go out)."""
    s = hub_settings.get_settings()
    serving = bool(s["serve_token"])
    clients = list_clients()
    if s["source"] == "remote" and s["hub_url"]:
        mode = "client"  # client of a hub
        remote_status = _remote.status() if _remote is not None else None
    elif serving and clients:
        mode = "hub"     # serving as hub (has clients)
        remote_status = None
    elif serving:
        mode = "hub-idle"  # ready to serve, no clients yet
        remote_status = None
    else:
        mode = "standalone"  # independent
        remote_status = None
    return {
        "env": _env(),
        "mode": mode,
        "source": s["source"],
        "hub_url": s["hub_url"],
        "has_enrollment_token": bool(s["enrollment_token"]),
        "pull_interval_s": s["pull_interval_s"],
        "serving": serving,
        "remote": remote_status,
        "clients": clients,
        "local_providers": len(llm_config.list_enabled_with_keys()),
    }


def settings_public() -> dict:
    """Settings for front — NO secrets (tokens become `has_*` flags)."""
    s = hub_settings.get_settings()
    return {
        "source": s["source"],
        "hub_url": s["hub_url"],
        "pull_interval_s": s["pull_interval_s"],
        "has_enrollment_token": bool(s["enrollment_token"]),
        "has_serve_token": bool(s["serve_token"]),
    }


def test_connection() -> dict:
    """Pull (if remote) and summarize EFFECTIVE cascade for UI — without keys."""
    s = hub_settings.get_settings()
    sync_meta: dict | None = None
    if s["source"] == "remote" and _remote is not None:
        sync_meta = _remote.sync_now()
    active = config_source.get_active_source()
    providers = active.get_llm_config()
    summary = [
        {"name": p.get("name", ""), "model": p.get("model", ""), "kind": p.get("kind", "openai")}
        for p in providers if isinstance(p, dict)
    ]
    flags = active.get_flags()
    remote = _remote.status() if _remote is not None else None
    ok = remote["last_ok"] if remote else True
    if s["source"] == "remote" and not summary and remote and not remote.get("has_cache"):
        ok = False
    return {
        "source": active.name,
        "mode": status()["mode"],
        "ok": ok,
        "provider_count": len(summary),
        "providers": summary,
        "flags": flags,
        "remote": remote,
        "sync": sync_meta,
    }
