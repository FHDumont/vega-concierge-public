"""Enrollment push por IP (F-027, ADR-020) — força várias lojas a virar CLIENTES deste hub.

Cenário: o owner do hub tem N lojas (cada uma já roda esta mesma app) e quer apontá-las todas
p/ a config dele de uma vez, sem entrar loja-a-loja. A F-026 deu o modelo hub/cliente (uma loja
escolhe `source=remote` e puxa do hub); aqui o hub **empurra** essa escolha por IP.

Duas pontas:

- **Lado CLIENTE — `apply_enroll` (chamado pelo endpoint `POST /api/admin/enroll`):** seta os
  settings locais p/ `source=remote` apontando p/ o hub (URL + token de enrollment p/ puxar),
  aplica a fonte a quente e faz um pull imediato. O endpoint é **token-gated por um SEGREDO
  COMPARTILHADO DO LAB** (`ENROLL_TOKEN`, env baked na AMI) — NÃO é a sessão de owner (a chamada
  vem máquina-a-máquina, do hub). Sem `ENROLL_TOKEN` configurado → o endpoint recusa (401):
  standalone-first (uma loja solta nunca é re-configurável por rede). Compare em tempo constante.

- **Lado HUB — `push` (chamado pelo endpoint owner-only `POST /api/admin/hub/enroll-push`):**
  p/ cada IP/host da lista, chama o `enroll` do alvo com `Authorization: Bearer <enroll_secret>`
  + `{hub_url, enrollment_token}`. Resultado **por IP** (ok/falha/timeout) p/ a UI.

Mecanismo = **API** (cada loja já roda a app). SSH fica como plano B (não implementado — F-028+).
Sem deps novas: urllib (stdlib), espelhando `config_source.RemoteConfigSource`.
"""
import hmac
import json
import os
import urllib.error
import urllib.request

from . import hub, hub_settings

_PUSH_TIMEOUT_S = 6  # curto: alvo fora do ar → timeout/falha por IP, não trava o lote


def enroll_secret() -> str:
    """Segredo compartilhado que gateia o endpoint de enroll (env baked no lab). '' = desligado."""
    return os.getenv("ENROLL_TOKEN", "").strip()


def verify_enroll_token(token: str | None) -> bool:
    """True se o token bate com `ENROLL_TOKEN` (tempo constante). Sem secret configurado → False
    (o endpoint recusa — a loja não aceita ser re-configurada por rede até o lab definir o token)."""
    secret = enroll_secret()
    if not secret or not token:
        return False
    return hmac.compare_digest(token, secret)


def apply_enroll(hub_url: str, enrollment_token: str, pull_interval_s: int | None) -> dict:
    """Aplica o enrollment NESTA loja: vira cliente do hub (source=remote) e puxa já. Idempotente."""
    patch: dict = {"source": "remote", "hub_url": hub_url.strip()}
    if enrollment_token:
        patch["enrollment_token"] = enrollment_token  # write-only (segredo)
    if pull_interval_s is not None:
        patch["pull_interval_s"] = pull_interval_s
    hub_settings.update_settings(**patch)
    hub.apply_source()       # reinstala a ConfigSource ativa (a quente)
    sync = hub.sync_now()    # pull imediato → feedback de saúde já na resposta
    st = hub.status()
    return {"enrolled": True, "env": st["env"], "mode": st["mode"], "sync": sync}


def _enroll_url(ip: str) -> str:
    """Normaliza um IP/host da lista p/ a URL do endpoint de enroll do alvo.
    Aceita `1.2.3.4`, `1.2.3.4:8000`, `host`, `http://host:8000` (com ou sem path)."""
    host = ip.strip()
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    # path explícito já aponta o endpoint? respeita; senão monta /api/admin/enroll.
    after_scheme = host.split("://", 1)[1]
    if "/" in after_scheme:
        return host  # owner deu o caminho completo
    authority = after_scheme
    if ":" not in authority:
        host += ":8000"  # porta padrão da app no lab (docker)
    return host.rstrip("/") + "/api/admin/enroll"


def _push_one(ip: str, enroll_token: str, body: dict) -> dict:
    """POST de enroll p/ UM alvo. Devolve `{ip, ok, status?, env?, mode?, error?}` (nunca levanta)."""
    url = _enroll_url(ip)
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": f"Bearer {enroll_token}",
                 "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=_PUSH_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode())
        return {"ip": ip, "ok": True, "status": resp.status,
                "env": payload.get("env"), "mode": payload.get("mode")}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = (json.loads(exc.read().decode()) or {}).get("detail", "")
        except Exception:
            pass
        return {"ip": ip, "ok": False, "status": exc.code, "error": detail or f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        is_timeout = "timed out" in str(reason).lower()
        return {"ip": ip, "ok": False, "error": "timeout" if is_timeout else f"unreachable: {reason}"}
    except Exception as exc:  # parse/etc.
        return {"ip": ip, "ok": False, "error": type(exc).__name__}


def push(ips: list[str], hub_url: str, enroll_token: str,
         enrollment_token: str, pull_interval_s: int | None = None) -> dict:
    """Empurra o enrollment p/ cada IP. Sequencial (N pequeno, controles do owner). Resultado por IP."""
    body = {"hub_url": hub_url.strip(), "enrollment_token": enrollment_token}
    if pull_interval_s is not None:
        body["pull_interval_s"] = pull_interval_s
    targets = [ip for ip in (s.strip() for s in ips) if ip]
    results = [_push_one(ip, enroll_token, body) for ip in targets]
    ok = sum(1 for r in results if r["ok"])
    return {"total": len(results), "ok": ok, "failed": len(results) - ok, "results": results}
