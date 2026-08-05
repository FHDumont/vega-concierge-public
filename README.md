# Vega Concierge — Workshop App

Loja de e-commerce com assistente **multi-agente (LangGraph)** para workshops Splunk. Observabilidade (Splunk Agent Observability + o11y) é **opt-in** via variáveis de ambiente — a app roda standalone sem credenciais.

**Guia do participante:** pasta [`workshop/`](workshop/) (Hugo, servido na VM em `:1313`) ou GitHub Pages deste repositório.

## Estrutura

- `backend/` — FastAPI + LangGraph
- `frontend/` — Next.js + Tailwind (loja, Behind the Scenes, Admin)
- `control/` — Vega Ops Console (manutenção da VM pelo navegador)
- `workshop/` — guia Hugo do workshop
- `scripts/` — dev, Docker, boot do workshop
- `docker-compose.yml`, `compose.plain.yml`, `.env.example`

## Como rodar

**Primeira execução:** copie o template de produção e suba o stack:

```bash
cp .env.example .env    # template p/ ./scripts/up.sh (pull GHCR)
./scripts/up.sh
```

Dev local (`./scripts/dev.sh` ou `./scripts/up.sh --build`) roda o wizard e ajusta URLs p/ o host — ver comentários `[dev]` no `.env.example`.

### Dev — hot reload, sem Docker

```bash
./scripts/dev.sh               # wizard (se necessário) + back (:8000) + front (:3000)
```

Standalone: não precisa de Docker nem de credenciais.

O **Vega Ops Console** não sobe junto no laptop — rode on-demand noutro terminal (Ctrl+C encerra):

```bash
./scripts/control.sh           # Ops Console :9000 (+ ttyd :7681 se instalado)
```

Na EC2 do workshop o painel é um **serviço systemd**, sempre no ar — ver *Run no workshop* abaixo.

### Lab — Docker local com build

```bash
./scripts/up.sh --build        # wizard (se necessário) + docker compose up --build
```

Frontend `:3000` · backend `:8000`.

### Produção / Workshop — imagens prontas (pull, sem build local)

Portas diretas na VM: loja `:3000` · API `:8000` · Ops Console `:9000` · guia Hugo `:1313`.

```bash
./scripts/up.sh                # validate-prod-env + pull + up -d
./scripts/up.sh logs           # acompanhar logs
./scripts/up.sh down           # parar (mantém volume SQLite)
```

**Imagens Docker:** geradas pelo CI deste repositório (GitHub Actions → GHCR público, multi-arch amd64+arm64). Na VM **nunca** use `docker build` — só pull. Runbook: [`docs/reference/runbooks/deploy-pull-only.md`](docs/reference/runbooks/deploy-pull-only.md).

## Run no workshop (EC2)

Clone deste repositório em `/opt/vega-concierge` (público, sem token git):

```bash
git clone https://github.com/FHDumont/vega-concierge-public.git /opt/vega-concierge
```

| Superfície | Porta | Acesso |
| --- | --- | --- |
| Loja (frontend) | 3000 | `http://<VM-IP>:3000` |
| API (backend) | 8000 | `http://<VM-IP>:8000` |
| Vega Ops Console | 9000 | `http://<VM-IP>:9000` |
| Guia workshop (Hugo) | 1313 | `http://<VM-IP>:1313` |

**Security Group:** abra **3000, 8000, 9000 e 1313**. O ttyd (`:7681`) fica só em `localhost` — terminal via proxy `/shell/` no painel.

Boot na AMI: `scripts/boot-workshop.sh` via systemd. Instalação dos units: `sudo REPO_DIR=/opt/vega-concierge ./control/systemd/install.sh`.

## Vega Ops Console

No workshop o SSH costuma estar bloqueado. O painel (`http://<VM-IP>:9000`) gerencia a infra pelo navegador — estado do stack + terminal SSH em iframe. Senha: `CONTROL_PASSWORD` (só para o terminal). Ver `control/README.md`.

| Ambiente | Como sobe |
| --- | --- |
| Dev (laptop) | `./scripts/control.sh` — on-demand |
| EC2 (workshop) | systemd (`control/systemd/install.sh`) |

## Só o backend (offline)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest
```

## Simulador de tráfego — `/admin/simulator`

Gera tráfego sintético pelo mesmo caminho do checkout real. Acesse **Vega → Simulator**. Modos **API** (rápido) ou **Browser** (Playwright/Chromium para RUM). Variante browser: `BACKEND_IMAGE=vega-backend-browser` no `.env` + pull.

## Splunk RUM — `/admin/connection`

Owner cola o snippet do Splunk RUM Browser Agent em **Vega → Connection** e liga o toggle. Off por default.

## LLM — `/admin/config`

Multi-provider em cascata com fallback para stub offline — funciona **sem chaves**. Provider inicial: Ollama (`OLLAMA_BASE_URL` no `.env`). Outros providers pelo Admin (owner-only). Primeira execução: `./scripts/setup-wizard.sh`.

## Observabilidade opt-in

- **Splunk Agent Observability:** `GALILEO_API_KEY` no `.env` (vazia = app base, sem traces).
- **Splunk o11y (OTel):** `./scripts/dev.sh --o11y` ou `./scripts/up.sh --o11y`.
