# Contrato env — AMI golden vs clone EC2

Referência para o **playbook Ansible externo** (Splunk Show) e para preparação manual da AMI
golden. O clone **herda** a AMI; o playbook só injeta o que muda por VM.

## Modelo

| Camada | Quem configura | O quê |
| --- | --- | --- |
| AMI golden | Dono (manual, uma vez) | Ollama + modelos, `.env` base, `control/systemd/install.sh`, reboot testado |
| Clone EC2 | Playbook externo | `.env` por participante (ADR-039 — fonte canônica de config da VM) |
| Pós-clone | Ops Console :9000 | Pull de imagens novas (`up.sh update` + fresh-state ADR-035) |

**Não pedir no clone:** wizard, `docker login`, `git clone` com token, install Ollama (já na AMI).

## Contrato de `.env` (ADR-039)

O `.env` da raiz é a **fonte canônica de config por VM** — o Ansible escreve tudo nele, tokens de
LLM inclusive (revoga a regra antiga "tokens cloud nunca no `.env`"). Dentro do processo, o
ambiente do SO segue vencendo o `.env` (precedência nativa de `app/settings.py`, ADR-037); nos
composes (`docker-compose.yml`/`compose.plain.yml`) o `.env` chega ao container inteiro via
`env_file` — variável nova no `.env` não precisa de edição no compose.

| Var | Obrigatória? | Onde definir | Efeito |
| --- | --- | --- | --- |
| `DEPLOYMENT_ENVIRONMENT` | **sim** | `.env` por clone | Rótulo único da instância; `/api/health`, Ops Console |
| `CONTROL_PASSWORD` | **sim** (vault) | `.env` por clone | Senha do terminal web (:7681 via proxy :9000) |
| `GALILEO_API_KEY` | não (opt-in) | `.env` por clone ou AMI | Habilita o Splunk Agent Observability (ADR-032); vazia = app "base", zero rede |
| `GALILEO_PROJECT` / `GALILEO_LOG_STREAM` / `GALILEO_CONSOLE_URL` | não | `.env` | Só têm efeito com `GALILEO_API_KEY` presente; defaults servem pro workshop |
| `OPENAI_API_KEY` | não | `.env` por clone ou AMI | **Auto-cadastro** de provider `OpenAI` na cascata no boot (`seed_providers_from_env`, ADR-039) |
| `ANTHROPIC_API_KEY` | não | `.env` por clone ou AMI | **Auto-cadastro** de provider `Claude` na cascata no boot |
| `AWS_BEARER_TOKEN_BEDROCK` | não | `.env` por clone ou AMI | **Auto-cadastro** de provider `Bedrock` na cascata no boot |
| `AWS_DEFAULT_REGION` | não (default `us-east-1`) | `.env` | Região do provider Bedrock auto-cadastrado; já exportada pro boto3 (`Settings.export_to_environ`) |
| `OLLAMA_*`, `RAG_*`, `IMAGE_*`, `API_INTERNAL_URL`, `PUBLIC_API_BASE` | não | baked na AMI | Pull GHCR público; `IMAGE_TAG=latest` ou pin CalVer |

Os 3 tokens de LLM cadastram um provider **por nome fixo** (`OpenAI`/`Claude`/`Bedrock`),
idempotente: presença do token garante a chave em dia a cada boot (rotação propaga); tudo o mais
que o instrutor editar no Admin → LLM Providers (`model`/`base_url`/ordem/enabled) fica intocado.
Sem token, nenhum provider cloud é criado — a demo roda no Ollama local (`ord=0`, sempre primeiro
na cascata).

## Portas (sem Traefik, ADR-025)

| Serviço | Porta |
| --- | --- |
| Loja (frontend) | **3000** |
| API (backend) | **8000** |
| Ops Console | **9000** |
| Guia workshop (Hugo) | **1313** |
| Terminal ttyd | **7681** (localhost only — proxy via :9000) |

## Boot na AMI

**Entry point:** `scripts/boot-workshop.sh` via systemd `vega-boot.service` — **não** `docker
compose` cru.

Ordem no boot:

1. `ollama.service` (host)
2. `vega-boot.service` → `boot-workshop.sh` → `./scripts/up.sh` (fresh-state ADR-035 + pull + up +
   rag-init + health)
3. `vega-control.service` + `vega-ttyd.service` (painel :9000)
4. `vega-workshop.service` (Hugo `workshop/` em `0.0.0.0:1313`)

Dentro do backend, `_bootstrap()` (`app/api.py`) roda `restore_providers_backup()` →
`seed_ollama_default()` → `seed_providers_from_env()`, nessa ordem — o Ollama sempre entra antes
de qualquer cloud auto-cadastrado.

Instalação dos units: `sudo REPO_DIR=/opt/vega-concierge ./control/systemd/install.sh`

## Fresh-state (ADR-035)

Todo start (`up.sh`, `up.sh update`, reboot, boot systemd) zera SQLite + volume pgvector.
**Preserva** só providers LLM em `backend/.vega-persist/llm_providers.json` — incluindo os que o
`seed_providers_from_env()` criou; a rotação de token que aconteça entre um fresh-state e o
seguinte propaga na volta (env vence a chave, ADR-039).

## Atualização pós-clone

Ops Console → **Atualizar imagens (pull)** → `./scripts/up.sh update` (GHCR público, sem login).

Pin opcional: `IMAGE_TAG=2026.08.05-abc1234` no `.env` antes do pull (CalVer ADR-036).
