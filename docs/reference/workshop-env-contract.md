# Contrato env — AMI golden vs clone EC2

Referência para o **playbook Ansible externo** (Splunk Show) e para preparação manual da AMI golden. O clone **herda** a AMI; o playbook só injeta o que muda por VM.

## Modelo

| Camada | Quem configura | O quê |
| --- | --- | --- |
| AMI golden | Dono (manual, uma vez) | Ollama + modelos, `.env` base, `control/systemd/install.sh`, reboot testado |
| Clone EC2 | Playbook externo | Vars por participante (ex. `DEPLOYMENT_ENVIRONMENT`, `CONTROL_PASSWORD`) |
| Pós-clone | Ops Console :9000 | Pull de imagens novas (`up.sh update` + fresh-state ADR-035) |

**Não pedir no clone:** wizard, `docker login`, `git clone` com token, install Ollama (já na AMI).

## Variáveis por clone

| Var | Por clone? | Default na AMI | Notas |
| --- | --- | --- | --- |
| `DEPLOYMENT_ENVIRONMENT` | **sim** | `user-XX` | Rótulo único; `/api/health`, Ops Console |
| `CONTROL_PASSWORD` | **sim** (vault) | — | Senha do terminal web (:7681 via proxy :9000) |
| `OLLAMA_*`, `RAG_*`, `IMAGE_*`, `API_INTERNAL_URL`, `PUBLIC_API_BASE` | não | baked | Pull GHCR público; `IMAGE_TAG=latest` ou pin CalVer |
| Tokens cloud (OpenAI, etc.) | **nunca** no `.env` | — | Admin → LLM Providers (F-026) |

## Portas (sem Traefik, ADR-025)

| Serviço | Porta |
| --- | --- |
| Loja (frontend) | **3000** |
| API (backend) | **8000** |
| Ops Console | **9000** |
| Guia workshop (Hugo) | **1313** |
| Terminal ttyd | **7681** (localhost only — proxy via :9000) |

## Boot na AMI

**Entry point:** `scripts/boot-workshop.sh` via systemd `vega-boot.service` — **não** `docker compose` cru.

Ordem no boot:

1. `ollama.service` (host)
2. `vega-boot.service` → `boot-workshop.sh` → `./scripts/up.sh` (fresh-state ADR-035 + pull + up + rag-init + health)
3. `vega-control.service` + `vega-ttyd.service` (painel :9000)
4. `vega-workshop.service` (Hugo `workshop/` em `0.0.0.0:1313`)

Instalação dos units: `sudo REPO_DIR=/opt/vega-concierge ./control/systemd/install.sh`

## Fresh-state (ADR-035)

Todo start (`up.sh`, `up.sh update`, reboot, boot systemd) zera SQLite + volume pgvector. **Preserva** só providers LLM em `backend/.vega-persist/llm_providers.json`.

## Atualização pós-clone

Ops Console → **Atualizar imagens (pull)** → `./scripts/up.sh update` (GHCR público, sem login).

Pin opcional: `IMAGE_TAG=2026.08.05-abc1234` no `.env` antes do pull (CalVer ADR-036).
