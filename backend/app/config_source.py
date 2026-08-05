"""Abstração da FONTE da config de LLM (F-020, etapa 4 — ADR-015; remota: F-026, ADR-019).

Isola DE ONDE vem a config dos provedores da cascata, para que a fonte **remota** (config
servida por outra loja / hub) plugue **sem tocar nos consumidores** (`llm.get_llm` chama
sempre `llm._load_provider_configs`, que delega para a fonte ativa daqui).

Duas fontes: **local** (SQLite desta loja, via `llm_config`) e **remota** (`RemoteConfigSource`,
F-026: pull de uma URL de hub + token de enrollment, com **cache resiliente** — se o hub cai,
segue com a última config). O owner escolhe local|remote (persistido em `hub_settings`); o único
ponto de troca é `set_active_source` — nada mais muda.

`get_llm_config()` devolve os provedores COM as chaves (uso interno da cascata) — a camada de
API nunca chama isto direto; ela usa `llm_config` (mascarado). Chaves continuam segredos.
"""
import json
import time
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable

from . import llm_config

# Timeout do pull ao hub (s). Curto: se o hub demora, cai no cache (resiliência).
_PULL_TIMEOUT_S = 10


@runtime_checkable
class ConfigSource(Protocol):
    """Contrato de uma fonte de config de LLM. A variante remota é `RemoteConfigSource`."""

    name: str

    def get_llm_config(self) -> list[dict]:
        """Provedores habilitados da cascata, em ordem, COM as chaves. `[]` = só stub."""
        ...

    def get_flags(self) -> dict:
        """Feature flags de menu/superfícies da fonte (F-033). `{}` = sem opinião (usa defaults)."""
        ...


class LocalConfigSource:
    """Fonte LOCAL: provedores persistidos no SQLite desta loja (`llm_config`)."""
    name = "local"

    def get_llm_config(self) -> list[dict]:
        return llm_config.list_enabled_with_keys()

    def get_flags(self) -> dict:
        from . import feature_flags  # lazy: evita ciclo (feature_flags importa este módulo)
        return feature_flags.get_local_flags()


class RemoteConfigSource:
    """Fonte REMOTA (F-026): puxa a config de um hub (outra loja) via HTTP + token.

    **Resiliente:** mantém em memória a última config puxada com sucesso; se o hub fica
    indisponível, `get_llm_config()` devolve o cache (a app nunca quebra). O refresh é
    **lazy** (na leitura, se passou o intervalo) + **sob demanda** (`sync_now`).

    Anti-loop (F-026): o pull envia `X-Hub-Chain` com a identidade desta loja; o hub recusa
    (409) se ela já está na cadeia — quebra ciclos hub↔hub. Cache em memória (reseta no
    restart, como DT-010/DT-007); até o 1º pull com sucesso a cascata fica só com o stub.
    """
    name = "remote"

    def __init__(self, hub_url: str, token: str, env: str, interval_s: int = 45):
        self.hub_url = hub_url
        self.token = token
        self.env = env  # identidade desta loja (deployment.environment) p/ anti-loop
        self.interval_s = max(5, int(interval_s))
        self._cache: list[dict] = []
        self._flags_cache: dict = {}  # feature flags servidas pelo hub (F-033); {} até o 1º pull
        self._has_cache = False
        self._last_fetch = 0.0      # monotonic do último pull bem-sucedido
        self._last_attempt = 0.0
        self.last_ok = False
        self.last_error: str | None = None
        self.last_sync_iso: str | None = None  # wall-clock do último sucesso (p/ a tela de status)
        self.hub_env: str | None = None         # identidade do hub que respondeu

    def _maybe_refresh(self) -> None:
        # Refresh lazy: só puxa se passou o intervalo desde o último pull bem-sucedido.
        if (time.monotonic() - self._last_fetch) >= self.interval_s:
            self._refresh()

    def get_llm_config(self) -> list[dict]:
        self._maybe_refresh()
        return list(self._cache)

    def get_flags(self) -> dict:
        # Mesma resiliência da cascata: as flags do hub vêm no mesmo pull e ficam em cache
        # (F-033). O poll do front (/api/flags) mantém a propagação dentro do intervalo.
        self._maybe_refresh()
        return dict(self._flags_cache)

    def sync_now(self) -> dict:
        """Pull sob demanda (botão 'sync agora'). Devolve o status do resultado."""
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
        except Exception as exc:  # rede/timeout/HTTP/parse → mantém o cache (resiliência)
            self.last_ok = False
            self.last_error = _short_error(exc)

    def _pull(self) -> tuple[list[dict], str | None, dict]:
        if not self.hub_url:
            raise ValueError("hub_url vazio")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-Hub-Chain": self.env,  # anti-loop: hub recusa se nossa identidade já está na cadeia
            "X-Hub-Env": self.env,    # identidade desta loja → o hub rastreia o cliente
            "Accept": "application/json",
        }
        req = urllib.request.Request(self.hub_url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=_PULL_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
        providers = data.get("providers", []) if isinstance(data, dict) else []
        # Normaliza só os campos que a cascata usa (ignora extras do wire).
        norm = [{"id": p.get("id", ""), "name": p.get("name", ""), "kind": p.get("kind", "openai"),
                 "base_url": p.get("base_url", ""), "model": p.get("model", ""),
                 "api_key": p.get("api_key", "")} for p in providers if isinstance(p, dict)]
        raw_flags = data.get("flags") if isinstance(data, dict) else None
        flags = {k: bool(v) for k, v in raw_flags.items()} if isinstance(raw_flags, dict) else {}
        return norm, (data.get("hub_env") if isinstance(data, dict) else None), flags

    def status(self) -> dict:
        """Saúde da conexão p/ a tela de status (sem segredos)."""
        return {
            "hub_url": self.hub_url,
            "interval_s": self.interval_s,
            "has_cache": self._has_cache,
            "cached_providers": len(self._cache),
            "cached_flags": len(self._flags_cache),  # nº de flags servidas pelo hub (F-033)
            "last_ok": self.last_ok,
            "last_error": self.last_error,
            "last_sync": self.last_sync_iso,
            "hub_env": self.hub_env,
            # Chaves trafegam neste canal (DT-013): HTTP não-local é inseguro (avisa o owner).
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


# Fonte ativa (singleton trocável). Default = local; F-021 troca via set_active_source.
_active: ConfigSource = LocalConfigSource()


def get_active_source() -> ConfigSource:
    return _active


def set_active_source(source: ConfigSource) -> None:
    """Ponto de extensão da F-021: o owner escolhe a fonte (local|remote) e isto a aplica.
    Os consumidores (`llm.get_llm`) não mudam — passam a resolver a cascata da nova fonte."""
    global _active
    _active = source
