"""Enrollment push by IP (F-027, ADR-020) — forces multiple stores to BECOME CLIENTS of this hub.

Scenario: hub owner has N stores (each already runs this same app) and wants to point them all
to their config at once, without entering store-by-store. F-026 gave hub/client model (one store
chooses `source=remote` and pulls from hub); here the hub **pushes** that choice by IP.

Two sides:

- **CLIENT side — `apply_enroll` (called by `POST /api/admin/enroll` endpoint):** sets
  local settings to `source=remote` pointing to hub (URL + enrollment token to pull),
  applies source hot and does immediate pull. Endpoint is **token-gated by a SHARED LAB SECRET**
  (`ENROLL_TOKEN`, env baked in AMI) — NOT owner session (call is machine-to-machine, from hub).
  Without `ENROLL_TOKEN` configured → endpoint refuses (401): standalone-first (loose store never
  reconfigurable over network). Compare in constant time.

- **HUB side — `push` (called by owner-only `POST /api/admin/hub/enroll-push` endpoint):**
  for each IP/host in list, calls target's `enroll` with `Authorization: Bearer <enroll_secret>`
  + `{hub_url, enrollment_token}`. Result **per IP** (ok/fail/timeout) for UI.

Mechanism = **API** (each store already runs app). SSH stays as plan B (not implemented — F-028+).
No new deps: urllib (stdlib), mirroring `config_source.RemoteConfigSource`.
"""
import hmac
import json
import urllib.error
import urllib.request

from . import hub, hub_settings
from ..settings import settings

_PUSH_TIMEOUT_S = 6  # short: target down → timeout/fail per IP, doesn't stall batch


def enroll_secret() -> str:
    """Shared secret that gates enroll endpoint (env baked in lab). '' = disabled."""
    return settings.enroll_token.strip()


def verify_enroll_token(token: str | None) -> bool:
    """True if token matches `ENROLL_TOKEN` (constant time). No secret configured → False
    (endpoint refuses — store won't accept being reconfigured over network until lab sets token)."""
    secret = enroll_secret()
    if not secret or not token:
        return False
    return hmac.compare_digest(token, secret)


def apply_enroll(hub_url: str, enrollment_token: str, pull_interval_s: int | None) -> dict:
    """Applies enrollment to THIS store: becomes hub client (source=remote) and pulls now. Idempotent."""
    patch: dict = {"source": "remote", "hub_url": hub_url.strip()}
    if enrollment_token:
        patch["enrollment_token"] = enrollment_token  # write-only (secret)
    if pull_interval_s is not None:
        patch["pull_interval_s"] = pull_interval_s
    hub_settings.update_settings(**patch)
    hub.apply_source()       # reinstalls active ConfigSource (hot)
    sync = hub.sync_now()    # immediate pull → health feedback already in response
    st = hub.status()
    return {"enrolled": True, "env": st["env"], "mode": st["mode"], "sync": sync}


def _enroll_url(ip: str) -> str:
    """Normalizes IP/host from list to target's enroll endpoint URL.
    Accepts `1.2.3.4`, `1.2.3.4:8000`, `host`, `http://host:8000` (with or without path)."""
    host = ip.strip()
    if not host.startswith(("http://", "https://")):
        host = "http://" + host
    # explicit path already points to endpoint? respect it; otherwise build /api/admin/enroll.
    after_scheme = host.split("://", 1)[1]
    if "/" in after_scheme:
        return host  # owner gave full path
    authority = after_scheme
    if ":" not in authority:
        host += ":8000"  # default app port in lab (docker)
    return host.rstrip("/") + "/api/admin/enroll"


def _push_one(ip: str, enroll_token: str, body: dict) -> dict:
    """Enroll POST to ONE target. Returns `{ip, ok, status?, env?, mode?, error?}` (never raises)."""
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
    """Pushes enrollment to each IP. Sequential (N small, owner controls). Result per IP."""
    body = {"hub_url": hub_url.strip(), "enrollment_token": enrollment_token}
    if pull_interval_s is not None:
        body["pull_interval_s"] = pull_interval_s
    targets = [ip for ip in (s.strip() for s in ips) if ip]
    results = [_push_one(ip, enroll_token, body) for ip in targets]
    ok = sum(1 for r in results if r["ok"])
    return {"total": len(results), "ok": ok, "failed": len(results) - ok, "results": results}
