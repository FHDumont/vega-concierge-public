"""Modelo hub/peer no MESMO app (F-026, ADR-019). Sem 2º aplicativo.

Liga as três pontas:

- **Aplicar a fonte:** lê `hub_settings` e instala a `ConfigSource` ativa (`local` ou
  `remote`) via `config_source.set_active_source`. Chamado no boot e a cada mudança do owner.
- **Lado hub (servir):** `serve_config(token, chain, client)` autentica pelo token de
  enrollment, **rastreia o cliente** e devolve a config da cascata resolvida (com chaves —
  **DT-013: chaves trafegam na rede**). Anti-loop pela cadeia `X-Hub-Chain`.
- **Status:** `status()` resume o modo (independente | servindo como hub | cliente de um hub),
  alvo, saúde/last-sync e — no hub — os clientes conectados, p/ a tela do owner.

Identidade desta loja = `DEPLOYMENT_ENVIRONMENT` (`user-NN`), reusada no anti-loop e no rastreio.
Owner-only em tudo (a API gateia com `_require_owner`). Registro de clientes vive em memória
(reseta no restart — mesma régua de DT-010).
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


# --- Aplicar a fonte ativa (boot + mudança do owner) ------------------------

_remote: config_source.RemoteConfigSource | None = None  # instância remota corrente (se source=remote)


def apply_source() -> None:
    """Lê os settings e instala a ConfigSource ativa. Idempotente; chamável a quente."""
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
    """Pull sob demanda (botão 'sync agora'). Sem efeito se não estamos em modo remote."""
    if _remote is None:
        return {"synced": False, "reason": "not in remote mode"}
    st = _remote.sync_now()
    return {"synced": True, **st}


# --- Lado hub: rastreio de clientes -----------------------------------------

_clients: dict[str, dict] = {}  # env do cliente → {env, last_pull, ip, agent, pulls}
_lock = threading.Lock()


def _track_client(env: str, ip: str | None, agent: str | None) -> None:
    key = env or (ip or "unknown")
    now = _now_iso()
    with _lock:
        c = _clients.get(key)
        if c is None:  # 1ª vez que este cliente aparece → marca "conectado desde"
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
    """Erro de servir config. `status` vira o HTTP (401 token, 409 loop)."""
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def serve_config(token: str | None, chain: str | None, client_env: str | None,
                 ip: str | None, agent: str | None) -> dict:
    """Entrega a config da cascata a um cliente. Levanta `HubError` em token/loop.

    - **Token-gated:** sem `serve_token` configurado, ou token errado → 401.
    - **Anti-loop:** se nossa identidade já está na cadeia `X-Hub-Chain` → 409 (ciclo).
    - **Rastreia** o cliente (env/last-pull/ip/agent).
    - Devolve `providers` da fonte ativa (local: rows locais; remote: cache do upstream —
      encadeamento por cache, sem recursão de rede). **Chaves vão no payload (DT-013).**
    """
    settings = hub_settings.get_settings()
    serve_token = settings["serve_token"]
    # Sem token configurado = não servimos (independente). Compare em tempo constante
    # (hmac.compare_digest) p/ não vazar o token por timing — DT-013 / ADR-019.
    if not serve_token or not token or not hmac.compare_digest(token, serve_token):
        raise HubError(401, "invalid enrollment token")

    me = _env()
    chain_ids = [c.strip() for c in (chain or "").split(",") if c.strip()]
    if me in chain_ids:
        raise HubError(409, "loop detected: this store is already in the hub chain")

    _track_client(client_env, ip, agent)
    active = config_source.get_active_source()
    providers = active.get_llm_config()
    flags = active.get_flags()  # F-033: propaga as flags de menu junto com a config (sem segredo)
    return {"hub_env": me, "served_at": _now_iso(), "providers": providers, "flags": flags,
            "chain": chain_ids + [me]}


# --- Status (tela do owner) -------------------------------------------------

def status() -> dict:
    """Resumo do modo/alvo/saúde + clientes. Sem segredos (tokens nunca saem)."""
    s = hub_settings.get_settings()
    serving = bool(s["serve_token"])
    clients = list_clients()
    if s["source"] == "remote" and s["hub_url"]:
        mode = "client"  # cliente de um hub
        remote_status = _remote.status() if _remote is not None else None
    elif serving and clients:
        mode = "hub"     # servindo como hub (há clientes)
        remote_status = None
    elif serving:
        mode = "hub-idle"  # pronto p/ servir, sem clientes ainda
        remote_status = None
    else:
        mode = "standalone"  # independente
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
    """Settings p/ o front — SEM segredos (tokens viram flags `has_*`)."""
    s = hub_settings.get_settings()
    return {
        "source": s["source"],
        "hub_url": s["hub_url"],
        "pull_interval_s": s["pull_interval_s"],
        "has_enrollment_token": bool(s["enrollment_token"]),
        "has_serve_token": bool(s["serve_token"]),
    }
