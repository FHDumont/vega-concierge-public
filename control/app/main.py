"""Vega Ops Console — manutenção da infra da VM pelo navegador, SEM SSH (F-047, ADR-025).

Roda como serviço de HOST (systemd, watchdog nativo — ver control/systemd/), NÃO como container:
assim segue de pé mesmo quando o stack Docker está sendo derrubado/subido/quebrado (é a hora em que
mais se precisa da ferramenta de manutenção) e evita expor o socket do Docker dentro de um container.

O que faz:
  - Estado do stack (o que está no ar, saúde dos containers).
  - Escotilha de terminal (ttyd local + proxy /shell/ no painel) — uma senha só, sem Basic Auth duplo.

Segurança (decisão do dono): o PAINEL é ABERTO (estado do stack) — sem login. A ÚNICA superfície
protegida por senha é o TERMINAL: senha na aba Terminal → cookie de sessão → proxy /shell/ para o
ttyd (localhost only, sem Basic Auth duplo). Ações são auditadas (arquivo de log).
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, parse_qsl

import httpx
import websockets
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import Response as StarletteResponse

# --- paths / config -----------------------------------------------------------------------------
STATIC_DIR = Path(__file__).parent / "static"
# Diretório do repo (onde vivem os compose files). Default = /opt/vega-concierge (AMI); overridável.
REPO_DIR = Path(os.getenv("VEGA_REPO_DIR", "/opt/vega-concierge")).resolve()
PLAIN_FILE = "compose.plain.yml"
AUDIT_LOG = Path(os.getenv("CONTROL_AUDIT_LOG", str(REPO_DIR / "control-audit.log")))

# ttyd escuta só em localhost; o painel faz proxy autenticado em /shell/ (mesma porta — sem 2º popup).
TTYD_HOST = os.getenv("CONTROL_TTYD_HOST", "127.0.0.1")
TTYD_BASE_PATH = "/shell"
TERMINAL_SESSION_TTL = 300  # segundos
_terminal_sessions: dict[str, float] = {}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("vega-control")

app = FastAPI(title="Vega Ops Console", docs_url=None, redoc_url=None, openapi_url=None)


class TerminalOpenBody(BaseModel):
    password: str


def _check_terminal_password(candidate: str) -> bool:
    expected = _control_password()
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _issue_terminal_session() -> str:
    token = secrets.token_urlsafe(32)
    _terminal_sessions[token] = time.time() + TERMINAL_SESSION_TTL
    return token


def _valid_terminal_session(token: str | None) -> bool:
    if not token or token not in _terminal_sessions:
        return False
    if time.time() > _terminal_sessions[token]:
        _terminal_sessions.pop(token, None)
        return False
    return True


def _terminal_token_from_request(request: Request) -> str | None:
    return request.cookies.get("terminal_session") or request.query_params.get("t")


def _upstream_ttyd_url(path: str, query: str = "") -> str:
    base = TTYD_BASE_PATH.rstrip("/")
    path = path.lstrip("/")
    url = f"http://{TTYD_HOST}:{_ttyd_port()}{base}/"
    if path:
        url = f"http://{TTYD_HOST}:{_ttyd_port()}{base}/{path}"
    if query:
        url = f"{url}?{query}"
    return url


def _strip_session_query(query: str) -> str:
    if not query:
        return ""
    pairs = [(k, v) for k, v in parse_qsl(query) if k != "t"]
    return urlencode(pairs)


async def _ttyd_reachable() -> bool:
    """ttyd roda como processo separado (control.sh ou systemd). Testa TCP na porta configurada."""
    return await _port_open(TTYD_HOST, _ttyd_port())


async def _port_open(host: str, port: int) -> bool:
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=1.0,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def _backend_health() -> dict:
    """GET /api/health do backend Vega (localhost:8000)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get("http://127.0.0.1:8000/api/health")
            if r.status_code == 200:
                return r.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        pass
    return {}


def _local_image_digest(image_ref: str) -> str:
    """Digest da imagem local (docker inspect) — vazio se não pulled."""
    import subprocess

    try:
        out = subprocess.run(
            ["docker", "inspect", "--format", "{{index .RepoDigests 0}}", image_ref],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        line = (out.stdout or "").strip()
        return line if line and "@" in line else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


async def _stack_version_info() -> dict:
    """Versão runtime + digests locais das imagens em uso."""
    owner = _read_env_value("IMAGE_OWNER") or "fhdumont"
    tag = _read_env_value("IMAGE_TAG") or "latest"
    backend_name = _read_env_value("BACKEND_IMAGE") or "vega-backend"
    backend_ref = f"ghcr.io/{owner}/{backend_name}:{tag}"
    frontend_ref = f"ghcr.io/{owner}/vega-frontend:{tag}"
    health = await _backend_health()
    return {
        "image_tag": tag,
        "image_owner": owner,
        "backend_image": backend_ref,
        "frontend_image": frontend_ref,
        "backend_digest": _local_image_digest(backend_ref),
        "frontend_digest": _local_image_digest(frontend_ref),
        "vega_version": health.get("version") or "",
        "git_sha": health.get("git_sha") or "",
        "build_date": health.get("build_date"),
        "backend_health": health.get("status") == "ok",
    }


def _audit(event: str, detail: str, request: Request | None = None) -> None:
    ip = (request.client.host if request and request.client else "-")
    line = json.dumps({"ts": time.time(), "ip": ip, "event": event, "detail": detail})
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        log.warning("could not write audit log at %s", AUDIT_LOG)
    log.info("audit %s", line)


# --- docker / compose helpers -------------------------------------------------------------------
def _compose_base_cmd() -> list[str]:
    return ["docker", "compose", "-f", PLAIN_FILE]


async def _run(cmd: list[str]) -> tuple[int, str]:
    """Roda um comando no REPO_DIR, capturando stdout+stderr juntos."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(REPO_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace")


async def _running_services() -> list[dict]:
    """Lista os serviços do projeto `vega` no ar (via `docker compose ps`)."""
    code, out = await _run([*_compose_base_cmd(), "ps", "--format", "json"])
    if code != 0:
        return []
    services: list[dict] = []
    for raw in out.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        # docker compose ps --format json pode vir 1 objeto por linha OU 1 array.
        if isinstance(obj, list):
            services.extend(obj)
        else:
            services.append(obj)
    return services


def _read_env_value(key: str) -> str:
    """Lê uma chave do .env do repo (sem depender de shell)."""
    env_file = REPO_DIR / ".env"
    if not env_file.exists():
        return ""
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == key:
                return v.strip().strip('"')
    except OSError:
        return ""
    return ""


def _control_password() -> str:
    """Senha do terminal: env do processo vence; senão lê CONTROL_PASSWORD do .env do repo."""
    return os.getenv("CONTROL_PASSWORD") or _read_env_value("CONTROL_PASSWORD")


def _ttyd_port() -> int:
    raw = os.getenv("CONTROL_TTYD_PORT") or _read_env_value("CONTROL_TTYD_PORT") or "7681"
    return int(raw)


def _service_state(services: list[dict], needle: str) -> dict | None:
    for s in services:
        name = s.get("Service") or s.get("Name") or ""
        if needle in name:
            return s
    return None


def _status_cards(
    services: list[dict],
    ttyd_up: bool,
    ports: dict[int, bool],
) -> list[dict]:
    """Cards de status — Docker quando existir; senão probe de porta (dev sem container)."""

    def card(
        key: str,
        label: str,
        port: int | None,
        required: bool,
        svc_name: str,
    ) -> dict:
        svc = _service_state(services, svc_name)
        if svc:
            state = svc.get("State", "?")
            up = state == "running"
            return {
                "id": key,
                "label": label,
                "port": port,
                "required": required,
                "up": up,
                "state": state,
                "detail": svc.get("Status", "") or "Container Docker",
            }
        if port and ports.get(port):
            return {
                "id": key,
                "label": label,
                "port": port,
                "required": required,
                "up": True,
                "state": "running",
                "detail": "Porta respondendo (dev local, sem container)",
            }
        return {
            "id": key,
            "label": label,
            "port": port,
            "required": required,
            "up": False,
            "state": "ausente",
            "detail": "Fora do ar" if port else "Container não encontrado",
        }

    cards = [
        card("frontend", "Loja (frontend)", 3000, True, "frontend"),
        card("backend", "API (backend)", 8000, True, "backend"),
        {
            "id": "ops-console",
            "label": "Vega Ops Console",
            "port": 9000,
            "required": True,
            "up": True,
            "state": "running",
            "detail": "Você está usando este painel agora",
        },
        {
            "id": "ttyd",
            "label": "Terminal web (ttyd)",
            "port": _ttyd_port(),
            "required": False,
            "up": ttyd_up,
            "state": "running" if ttyd_up else "ausente",
            "detail": "Disponível na aba Terminal" if ttyd_up else "Rode ./scripts/control.sh",
        },
    ]
    return cards


# --- routes: static -----------------------------------------------------------------------------
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
def health() -> dict:
    # Painel aberto; a senha só existe para o terminal SSH (ttyd). auth_required=False.
    return {"status": "ok", "auth_required": False}


# --- routes: state / plan (ABERTOS — sem login) -------------------------------------------------
@app.get("/api/state")
async def state(request: Request) -> dict:
    services = await _running_services()
    ttyd_up = await _ttyd_reachable()
    port = _ttyd_port()
    ports = {
        3000: await _port_open("127.0.0.1", 3000),
        8000: await _port_open("127.0.0.1", 8000),
    }
    return {
        # Precedência F-REAL-ENV-2: SO (unit carrega .env + /etc/environment) > INSTANCE (nome
        # único da réplica) > .env do repo > fallback dev.
        "environment": os.getenv("DEPLOYMENT_ENVIRONMENT")
        or os.getenv("INSTANCE")
        or _read_env_value("DEPLOYMENT_ENVIRONMENT")
        or "user-local",
        "ttyd_port": port,
        "ttyd_available": ttyd_up,
        "ttyd_configured": bool(_control_password()),
        "terminal_url": "/shell/",
        "stack": await _stack_version_info(),
        "cards": _status_cards(services, ttyd_up, ports),
        "services": [
            {
                "name": s.get("Service") or s.get("Name", "?"),
                "state": s.get("State", "?"),
                "status": s.get("Status", ""),
            }
            for s in services
        ],
    }


_stack_update_lock = asyncio.Lock()


@app.post("/api/stack/update")
async def stack_update(request: Request) -> dict:
    """Pull GHCR público + up + fresh-state via up.sh update (painel aberto + audit log)."""
    if _stack_update_lock.locked():
        raise HTTPException(status_code=409, detail="Atualização já em andamento.")
    async with _stack_update_lock:
        _audit("stack_update_start", "-", request)
        code, out = await _run(["/bin/bash", "-lc", "./scripts/up.sh update"])
        ok = code == 0
        detail = out.strip()[-4000:] if out else ""
        _audit(
            "stack_update_ok" if ok else "stack_update_fail",
            f"exit={code}",
            request,
        )
        if not ok:
            raise HTTPException(
                status_code=500,
                detail=f"up.sh update falhou (exit {code}).\n{detail}",
            )
        stack = await _stack_version_info()
        return {"ok": True, "stack": stack, "log_tail": detail}


@app.post("/api/terminal/open")
async def terminal_open(body: TerminalOpenBody, request: Request, response: Response) -> dict:
    """Valida senha, emite cookie de sessão e devolve URL do proxy /shell/ (mesma porta — sem 2º popup)."""
    pwd = _control_password()
    port = _ttyd_port()
    if not pwd:
        raise HTTPException(
            status_code=503,
            detail="CONTROL_PASSWORD não configurada. Defina no .env ou rode ./scripts/control.sh.",
        )
    if not await _ttyd_reachable():
        raise HTTPException(
            status_code=503,
            detail=(
                f"Terminal web (ttyd) não está rodando na porta {port}. "
                "Use ./scripts/control.sh (requer ttyd instalado: brew install ttyd)."
            ),
        )
    if not _check_terminal_password(body.password):
        _audit("terminal_open_denied", "-", request)
        raise HTTPException(status_code=401, detail="Senha inválida.")
    token = _issue_terminal_session()
    _audit("terminal_open_ok", "-", request)
    return _terminal_session_response(token, response)


def _terminal_session_response(token: str, response: Response | StarletteResponse) -> dict:
    """Emite cookie + URL do proxy /shell/ (iframe no painel; token na query p/ WebSocket)."""
    _set_terminal_cookie(response, token)
    return {"ok": True, "url": f"/shell/?t={token}", "token": token}


def _revoke_terminal_session(token: str | None) -> None:
    if token:
        _terminal_sessions.pop(token, None)


@app.post("/api/terminal/renew")
async def terminal_renew(request: Request, response: Response) -> dict:
    """Renova sessão do terminal (sem pedir senha de novo) — útil após exit ou erro no shell."""
    old = request.cookies.get("terminal_session")
    if not _valid_terminal_session(old):
        raise HTTPException(
            status_code=401,
            detail="Sessão expirada. Desbloqueie o terminal novamente.",
        )
    if not await _ttyd_reachable():
        port = _ttyd_port()
        raise HTTPException(
            status_code=503,
            detail=f"ttyd não está rodando na porta {port}. Rode ./scripts/control.sh.",
        )
    _revoke_terminal_session(old)
    token = _issue_terminal_session()
    _audit("terminal_renew_ok", "-", request)
    return _terminal_session_response(token, response)


@app.post("/api/terminal/close")
async def terminal_close(request: Request, response: Response) -> dict:
    """Encerra sessão do terminal e remove cookie."""
    token = request.cookies.get("terminal_session")
    _revoke_terminal_session(token)
    response.delete_cookie(key="terminal_session", path="/")
    _audit("terminal_close", "-", request)
    return {"ok": True}


def _set_terminal_cookie(response: StarletteResponse, token: str) -> None:
    response.set_cookie(
        key="terminal_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=TERMINAL_SESSION_TTL,
        path="/",
    )


@app.api_route("/shell", methods=["GET", "POST", "HEAD", "OPTIONS"])
@app.api_route("/shell/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def shell_http_proxy(request: Request, path: str = "") -> StarletteResponse:
    """Proxy HTTP autenticado → ttyd local (base-path /shell)."""
    token = _terminal_token_from_request(request)
    if not _valid_terminal_session(token):
        raise HTTPException(status_code=401, detail="Desbloqueie o terminal na aba Terminal do painel.")
    upstream_url = _upstream_ttyd_url(path, _strip_session_query(request.url.query))
    hop_by_hop = {"host", "cookie", "connection", "transfer-encoding", "content-length"}
    headers = {k: v for k, v in request.headers.items() if k.lower() not in hop_by_hop}
    async with httpx.AsyncClient() as client:
        async with client.stream(
            request.method,
            upstream_url,
            headers=headers,
            content=await request.body(),
            timeout=60.0,
        ) as upstream:
            resp_headers = {
                k: v
                for k, v in upstream.headers.items()
                if k.lower() not in ("transfer-encoding", "connection", "content-encoding", "content-length")
            }
            body = await upstream.aread()
            out = StarletteResponse(
                content=body,
                status_code=upstream.status_code,
                headers=resp_headers,
                media_type=upstream.headers.get("content-type"),
            )
    if token:
        _set_terminal_cookie(out, token)
    return out


@app.websocket("/shell/ws")
async def shell_ws_proxy(websocket: WebSocket) -> None:
    """Proxy WebSocket autenticado → ttyd. ttyd exige subprotocolo 'tty' — sem isso o terminal reconecta."""
    token = websocket.cookies.get("terminal_session") or websocket.query_params.get("t")
    if not _valid_terminal_session(token):
        await websocket.close(code=4401)
        return

    upstream_uri = f"ws://{TTYD_HOST}:{_ttyd_port()}{TTYD_BASE_PATH}/ws"
    client_proto = (websocket.headers.get("sec-websocket-protocol") or "tty").split(",")[0].strip()
    try:
        async with websockets.connect(
            upstream_uri,
            subprotocols=[client_proto] if client_proto else ["tty"],
            max_size=None,
            open_timeout=10,
            ping_interval=None,
        ) as upstream:
            negotiated = upstream.subprotocol or client_proto or "tty"
            await websocket.accept(subprotocol=negotiated)

            async def client_to_server() -> None:
                try:
                    while True:
                        msg = await websocket.receive()
                        if msg["type"] == "websocket.disconnect":
                            break
                        if msg.get("bytes") is not None:
                            await upstream.send(msg["bytes"])
                        elif msg.get("text") is not None:
                            await upstream.send(msg["text"])
                except WebSocketDisconnect:
                    pass

            async def server_to_client() -> None:
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except Exception:
                    pass

            tasks = [
                asyncio.create_task(client_to_server()),
                asyncio.create_task(server_to_client()),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in done:
                if not task.cancelled() and (exc := task.exception()):
                    log.warning("terminal ws task ended: %s", exc)
    except Exception as exc:
        log.warning("terminal ws proxy error: %s", exc, exc_info=True)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


