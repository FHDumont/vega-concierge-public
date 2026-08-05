# Runbook — deploy pull-only (Mac mini + AMI golden + EC2)

Fluxo manual **CI → GHCR público → pull** para ambientes reais. **Sem** `docker login`, **sem** wizard de produção. Ollama roda no **host**; tokens cloud entram só pelo **Admin** (F-026).

## GHCR público (dono — UI GitHub, uma vez)

**Repo git público ≠ imagens GHCR públicas.** Mesmo com o repo público, cada **package de container** nasce privado até você mudar na UI.

### Quais packages tornar public

São **três imagens distintas** (não existe um package genérico “vega”):

| Package | Obrigatório? |
| --- | --- |
| `ghcr.io/<owner>/vega-backend` | **sim** (stack default) |
| `ghcr.io/<owner>/vega-frontend` | **sim** |
| `ghcr.io/<owner>/vega-backend-browser` | só se `BACKEND_IMAGE=vega-backend-browser` |

`<owner>` = usuário/org GitHub em minúsculas — igual a `IMAGE_OWNER` no `.env` (default `fhdumont`). O CI publica em [`.github/workflows/build-images.yml`](../../.github/workflows/build-images.yml).

### Pré-requisito

O workflow **build-images** deve ter rodado pelo menos uma vez (push na `main` com código, tag `v*`, ou *Actions → build-images → Run workflow*). Sem publish, os packages não aparecem para tornar públicos.

### Passo a passo (UI GitHub)

Para **cada** package (`vega-backend`, `vega-frontend`, e opcionalmente `vega-backend-browser`):

1. GitHub → seu **perfil** (ou org) → **Packages** — ou pelo repo, aba **Packages** após o primeiro publish.
2. Abrir o package (ex.: **vega-backend**).
3. **Package settings** (engrenagem).
4. **Change package visibility** → **Public**.
5. Confirmar (GitHub pede digitar o nome do package).

### Validar pull anônimo

Script no repo (recomendado):

```bash
./scripts/verify-ghcr-public.sh
./scripts/verify-ghcr-public.sh --browser   # se usa imagem Playwright
```

Manual:

```bash
docker logout ghcr.io   # opcional — garante teste anônimo
docker manifest inspect ghcr.io/<owner>/vega-backend:latest
docker manifest inspect ghcr.io/<owner>/vega-frontend:latest
```

- **OK:** JSON com `linux/amd64` e `linux/arm64`.
- **`denied` / unauthorized:** package ainda **private** — repetir passo UI acima.
- **Not found:** CI ainda não publicou ou `IMAGE_OWNER`/`IMAGE_TAG` errados.

### Checklist GHCR (critério 1, F-DEPLOY-PROD-1)

- [ ] CI publicou backend + frontend (e browser se usar)
- [ ] Visibilidade **Public** em cada package usado
- [ ] `./scripts/verify-ghcr-public.sh` passa **sem** `docker login`
- [ ] `.env` com `IMAGE_OWNER=<owner>` e `IMAGE_TAG=latest` (ou pin CalVer)

**Ordem de validação sugerida:** Mac mini (arm64) primeiro → EC2/AMI depois. Ver [validation-F-DEPLOY-PROD-1.md](validation-F-DEPLOY-PROD-1.md).

## AMI golden (manual, uma vez)

1. Ubuntu + Docker + compose plugin + **hugo extended** + **ttyd** + **Ollama** (+ modelos `nomic-embed-text`, `llama3.2`). **Linux:** Ollama deve escutar em `0.0.0.0:11434` (não só `127.0.0.1`) — ver troubleshooting abaixo.
2. Clone público do repo em `/opt/vega-concierge` (sem token git):
   ```bash
   git clone https://github.com/FHDumont/vega-concierge-public.git /opt/vega-concierge
   ```
3. `.env` baked — ver `docs/reference/workshop-env-contract.md` e `.env.example` (bloco workshop).
4. `sudo REPO_DIR=/opt/vega-concierge ./control/systemd/install.sh`
5. Reboot → loja :3000, API :8000, Ops :9000, guia :1313, Ollama OK.
6. Opcional: configurar LLM cloud no Admin; providers sobrevivem fresh-state (ADR-035).

## Clone EC2 (playbook externo)

O playbook real é **externo** (Splunk Show). Este repo só documenta o contrato (`workshop-env-contract.md`). O externo injeta tipicamente:

- `DEPLOYMENT_ENVIRONMENT=user-<hostname>`
- `CONTROL_PASSWORD` (vault)

Boot: units systemd herdados da AMI — **não** rodar wizard.

## Mac mini (arm64)

Mesmo fluxo da AMI golden (sem playbook):

```bash
cd /opt/vega-concierge   # ou path local do clone
./scripts/up.sh          # validate-prod-env + preflight + pull + fresh-state + up
```

Atualizar: `./scripts/up.sh update` ou Ops Console → **Atualizar imagens**.

## Fluxo de deploy (qualquer host)

1. **CI publica** — push `main` (código) → CalVer `YYYY.MM.DD-<sha>` + `latest` (ADR-036).
2. **Pull anônimo** — `docker compose -f compose.plain.yml pull` (via `up.sh`).
3. **Subir:** `./scripts/up.sh` — preflight → fresh-state → pull → up → rag-init → `/api/health`.
4. **Atualizar:** `./scripts/up.sh update` ou Ops Console (mesmo fresh-state).

> **O start é destrutivo (ADR-035).** Pedidos/usuários/RAG zeram; sobrevivem só providers LLM em `backend/.vega-persist/llm_providers.json`.

## Verificação

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
# version, git_sha, ollama.reachable, llm_providers
```

## Troubleshooting

| Sintoma | Causa provável | Ação |
| --- | --- | --- |
| manifest inspect falha | Tag inexistente ou package ainda privado | Aguardar CI; tornar package **public** |
| `ollama.reachable: false` | Ollama parado | `systemctl start ollama` ou `ollama serve` |
| RAG dim mismatch | Volume indexado com embedding errado | `up.sh` refaz fresh-state (volume pgvector) |
| Resposta `[stub:` | Provider LLM ausente | Admin → providers |
| Wizard pedido em prod | Fluxo antigo | Use `.env` baked + `validate-prod-env.sh` |
| Ollama OK no host, ConnectionError no container (rag-init) | Linux: Ollama só em `127.0.0.1`; container usa `host.docker.internal` → host-gateway | `sudo mkdir -p /etc/systemd/system/ollama.service.d` · `printf '[Service]\nEnvironment=OLLAMA_HOST=0.0.0.0:11434\n' \| sudo tee /etc/systemd/system/ollama.service.d/bind-all.conf` · `sudo systemctl daemon-reload && sudo systemctl restart ollama` · validar: `ss -tlnp \| grep 11434` mostra `0.0.0.0:11434` |

## Tags de imagem

| Tag | Quando |
| --- | --- |
| `latest` | Default no `.env` — último build da main |
| `YYYY.MM.DD-<sha>` | CalVer do CI — pin opcional |
| `sha-abc1234` | Rastreio por commit |
