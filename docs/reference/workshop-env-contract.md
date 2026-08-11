# Contrato env — AMI golden vs clone EC2

Referência para o **playbook Ansible externo** (Splunk Show) e para preparação manual da AMI
golden. O clone **herda** a AMI; o playbook só injeta o que muda por VM.

## Modelo

| Camada | Quem configura | O quê |
| --- | --- | --- |
| AMI golden | Dono (manual, uma vez) | **`scripts/bootstrap-workshop-host.sh`** ou bake manual; reboot testado |
| Clone EC2 | Playbook externo | `.env` por participante (ADR-039 — fonte canônica de config da VM) |
| Pós-clone | Ops Console :9000 | Pull de imagens novas (`up.sh update` + fresh-state ADR-035) |

**Não pedir no clone:** wizard, `docker login`, `git clone` com token, install Ollama (já na AMI).

## Contrato de `.env` (ADR-039)

O `.env` da raiz é a **fonte canônica de config por VM** — tokens de LLM inclusive (revoga a
regra antiga "tokens cloud nunca no `.env`"). A precedência é **SO > `.env` > default** em TODAS
as camadas (F-REAL-ENV-2):

- **Scripts shell** (`up.sh`, `control.sh`, `lib/validate-prod-env.sh`, `lib/preflight-prod.sh`):
  carregam via `scripts/lib/env-load.sh` — o `.env` preenche só o que o ambiente do SO não trouxe.
- **Units systemd** (`control/systemd/*.service`): `EnvironmentFile=-…/.env` seguido de
  `EnvironmentFile=-/etc/environment` — o arquivo do SO, carregado por último, vence.
- **Container backend** (`compose.plain.yml`): recebe `.env.runtime` via `env_file` — arquivo
  gerado pelo `up.sh` a cada start com o ambiente **efetivo** (merge SO > `.env`); variável nova
  no `.env`/SO não precisa de edição no compose. Dentro do processo, `app/settings.py` (ADR-037)
  mantém a mesma precedência nativa.

| Var | Obrigatória? | Onde definir | Efeito |
| --- | --- | --- | --- |
| `DEPLOYMENT_ENVIRONMENT` | **sim** | `.env` por clone | Rótulo único da instância; `/api/health`, Ops Console |
| `CONTROL_PASSWORD` | **sim** (vault) | `.env` por clone | Senha do terminal web (:7681 via proxy :9000) |
| `GALILEO_API_KEY` | não (opt-in) | `.env` por clone ou AMI | Habilita o Splunk Agent Observability (ADR-032); vazia = app "base", zero rede |
| `GALILEO_PROJECT` / `GALILEO_LOG_STREAM` / `GALILEO_CONSOLE_URL` | não | `.env` | Só têm efeito com `GALILEO_API_KEY` presente; defaults servem pro workshop |
| `LLM_PROVIDER_PRIORITY` | não (default `BEDROCK,OPENAI,ANTHROPIC,OLLAMA`) | `.env` por clone ou AMI | Ordem da cascata a cada restart; pula alias sem credencial até cair no Ollama |
| `OPENAI_API_KEY` | não | `.env` por clone ou AMI | **Auto-cadastro** de provider `OpenAI` na cascata no boot (`seed_providers_from_env`, ADR-039) |
| `ANTHROPIC_API_KEY` | não | `.env` por clone ou AMI | **Auto-cadastro** de provider `Claude` na cascata no boot |
| `AWS_BEARER_TOKEN_BEDROCK` | não | `.env` por clone ou AMI | **Auto-cadastro** de provider `Bedrock` na cascata no boot |
| `AWS_DEFAULT_REGION` | não (default `us-east-1`) | `.env` | Região do provider Bedrock auto-cadastrado; já exportada pro boto3 (`Settings.export_to_environ`) |
| `OPENAI_CHAT_MODEL` / `ANTHROPIC_CHAT_MODEL` / `BEDROCK_CHAT_MODEL` / `OLLAMA_CHAT_MODEL` | não | `.env` | Modelo usado no **auto-cadastro** (só na criação); Admin prevalece depois |
| `OLLAMA_*`, `RAG_*`, `IMAGE_*`, `API_INTERNAL_URL`, `PUBLIC_API_BASE` | não | baked na AMI | Pull GHCR público; `IMAGE_TAG=latest` ou pin CalVer |

Os tokens de LLM cadastram um provider **por nome fixo** (`OpenAI`/`Claude`/`Bedrock`/`Ollama Local`),
idempotente: presença do token (ou `OLLAMA_BASE_URL` para o local) garante chave e **ordem** a cada
boot conforme `LLM_PROVIDER_PRIORITY`; aliases sem credencial são pulados. **UI vence** `model` e
`base_url` editados no Admin; env vence chave, ordem e enabled a cada restart.

| Var | Obrigatória? | Default workshop | Efeito |
| --- | --- | --- | --- |
| `API_RATE_ENABLED` | não | `1` | `0` desliga rate limit HTTP (dev local) |
| `API_RATE_AI_MAX` | não | `12` | Máx. requests/min por IP em rotas tier **ai** antes de 429 |
| `API_RATE_AI_WINDOW_S` | não | `60` | Janela (s) do bucket tier ai |
| `API_RATE_DEFAULT_MAX` | não | `60` | Máx. requests/min por IP nas demais `/api/*` |
| `API_RATE_DEFAULT_WINDOW_S` | não | `60` | Janela (s) do bucket tier default |
| `LLM_RATE_MAX` | não | `20` | Chamadas reais ao provider por janela; estoura → stub (ADR-016) |
| `LLM_RATE_WINDOW_S` | não | `60` | Janela (s) do limiter LLM por instância |

Qualquer var listada em `app/settings.py` vale após **restart** do backend (`up.sh` / reboot) — o Hub
**não** propaga `API_RATE_*` nem `LLM_RATE_*` nesta fase (ADR-040).

## Security Group (workshop)

| Porta | Recomendação |
| --- | --- |
| **11434** (Ollama) | **Fechada** externamente — só host/containers locais |
| **8000** / **3000** | Restringir à faixa IP do evento ou VPN — não `0.0.0.0/0` em produção de workshop |
| **9000** / **1313** | Mesma faixa do evento (Ops + guia) |

## Hub / Ansible — o que o fleet controla hoje (ADR-040)

| Ação | Mecanismo | Efeito imediato? |
| --- | --- | --- |
| Rotacionar cascata LLM / keys | Hub owner → clientes `remote` pull ou **Sync now** | Sim (providers) |
| Propagar flags de menu | Hub serve `flags` no pull | Sim |
| Apertar rate limits HTTP/LLM | Editar `.env` na VM (Ansible) + **restart** `vega-boot` / `up.sh` | Após restart |
| Enroll em massa | `POST /api/admin/hub/enroll-push` (owner no hub) | Clientes passam a `remote` |
| Emergência pós-vazamento | Rotacionar key no provider + hub sync; opcional só Ollama no hub | Parcial imediato + restart se mudar env |

**`ENROLL_TOKEN` (`.env`, todas as VMs):** segredo compartilhado que autentica o **enroll-push** (`POST /api/admin/enroll` nos alvos). Distinto do **serve token** (SQLite do hub, Admin → Connection) que clientes usam no pull. Vazio = ninguém reconfigura loja por rede (401). Runbook: [`runbooks/hub-fleet-day0.md`](runbooks/hub-fleet-day0.md).

**Modo `remote`:** runtime LLM usa cascata do hub, não o SQLite local de providers. Keys cloud no `.env` do clone são bootstrap opcional; fleet 100% hub pode concentrar keys só na VM hub.

**Ansible (clone):** injetar no `.env` **ou no SO** (ver "Injeção por réplica" abaixo) além das
keys: `API_RATE_AI_MAX`, `API_RATE_DEFAULT_MAX`, `LLM_RATE_MAX`, `ENROLL_TOKEN`,
`DEPLOYMENT_ENVIRONMENT`, etc.

**Camada 0 provider (chave compartilhada):** budget diário + rate limit na conta OpenAI/Bedrock/Anthropic;
alertas 50%/80%.

## Injeção por réplica (Ansible) — SO vence `.env` (F-REAL-ENV-2)

O template da VM oficial já traz `.env` baked com defaults de lab. Na réplica, o processo externo
injeta **no ambiente do SO** só o que muda por clone — sem editar o `.env`. Mecanismo **testado e
recomendado: `/etc/environment`** (formato `VAR=valor`, uma por linha): vale para shells de login
E é carregado por último nas 4 units systemd. Qualquer outro mecanismo que deixe a variável no
ambiente dos processos também funciona (os scripts preservam o env já presente).

**Vars OBRIGATÓRIAS no SO por clone** (valores definidos no momento do workshop/réplica):

| Var | Origem / uso |
| --- | --- |
| `INSTANCE` | Criada pela réplica — nome único da VM. A app deriva `DEPLOYMENT_ENVIRONMENT` dela (ver abaixo) |
| `CONTROL_PASSWORD` | Senha do terminal web (vault do evento) |
| `GALILEO_CONSOLE_URL` / `GALILEO_API_KEY` / `GALILEO_PROJECT` / `GALILEO_LOG_STREAM` | Splunk Agent Observability do evento |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `AWS_BEARER_TOKEN_BEDROCK` / `AWS_DEFAULT_REGION` | Keys cloud da cascata LLM |

**Mapeamento `INSTANCE` → `DEPLOYMENT_ENVIRONMENT`:** a réplica não fornece
`DEPLOYMENT_ENVIRONMENT`; fornece `INSTANCE` — e `INSTANCE`, quando presente, **sempre vence**
qualquer `DEPLOYMENT_ENVIRONMENT` (o das units vem do `.env` via `EnvironmentFile` e é
indistinguível de env do SO). Resolvido em `env-load.sh` (stack/containers) e no painel :9000.
Sem `INSTANCE` (dev/laptop), vale a precedência normal SO > `.env` > default.

**Reservadas do ambiente (o11y Splunk, NÃO consumidas pela app hoje):** `REALM` e
`ACCESS_TOKEN` já estarão no SO junto com `INSTANCE`. A app não as lê nem as repassa ao
container (não estão no contrato `.env.example`); ficam disponíveis pra uma futura fase de
o11y sem mudança na réplica.

**Opcionais** (existir no `.env.example` ⇒ o valor do SO **sobrepõe** o do `.env`):

| Var | Uso na réplica |
| --- | --- |
| `ENROLL_TOKEN` | mesmo valor em todas as VMs do lab (enroll-push do hub) |
| `IMAGE_TAG` | pin CalVer do evento |
| `OWNER_PASSWORD` | atenção: **vazia ≠ ausente** (vazia zera a senha demo) |

Validação numa VM: `scripts/tests/test-env-precedence.sh` + `curl :8000/api/health` (o
`DEPLOYMENT_ENVIRONMENT` retornado deve ser o do SO quando divergente do `.env`).

### Mudei uma variável — o que fazer?

Regras que valem SEMPRE, independente de onde você mudou (`/etc/environment`/SO **ou** `.env`):

1. **Onde mudar nunca muda o que reiniciar.** O que define a ação é **qual serviço consome** a
   variável — toda fonte é lida no **start** do serviço, nunca em runtime.
2. **SO vence `.env`.** Se a var existe nos dois, editar o `.env` não tem efeito até remover a
   var do SO. Pra "voltar ao `.env`", apague a linha do `/etc/environment` e reinicie o serviço.
3. **Pro container, a chave precisa ser conhecida**: descomentada no `.env` OU listada no
   `.env.example` (mesmo comentada). Var fora do contrato não entra no `.env.runtime`.

| Variável que você mudou | Quem consome | Ação |
| --- | --- | --- |
| `DEPLOYMENT_ENVIRONMENT`, `GALILEO_*`, `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `AWS_*`, `LLM_*`, `RAG_*`, `OLLAMA_*`, `IMAGE_OWNER` / `IMAGE_TAG` / `BACKEND_IMAGE`, `PUBLIC_API_BASE`, `API_RATE_*`, `OWNER_PASSWORD`, `ENROLL_TOKEN`, `TIER_*` | **Containers** (loja :3000 · API :8000 · postgres) | `./scripts/up.sh` **ou** `sudo systemctl restart vega-boot` (mesmo caminho: relê SO+`.env`, regenera `.env.runtime`, recria containers). Sem SSH: Ops Console :9000 → "Atualizar imagens (pull)". |
| `CONTROL_PASSWORD` | **Painel** (host) | `sudo systemctl restart vega-control` — o `up.sh` NÃO ajuda aqui (a unit lê `.env` + `/etc/environment` direto pelo systemd). |
| `CONTROL_TTYD_PORT` | **Terminal web** (host) | `sudo systemctl restart vega-ttyd` |
| (vars do guia, se um dia existirem) | **Hugo** (host) | `sudo systemctl restart vega-workshop` |

**Atalho universal: `sudo reboot`** — todas as units releem tudo. É o caminho natural do clone:
Ansible injeta as vars e a VM boota em seguida; ninguém reinicia nada na mão.

Notas da VM oficial (template): Docker **via apt (docker-ce, get.docker.com)** — docker via
snap é confinado e NÃO lê `/opt` (compose/env_file/bind-mounts falham); o bootstrap remove o
snap e instala docker-ce, e o `vega-boot.service` ordena `After=` para os dois nomes de unit.
Hugo extended instalado via **.deb** (snap não lê `/opt`).

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
`seed_providers_from_env()` — monta a cascata na ordem de `LLM_PROVIDER_PRIORITY`, pulando providers
sem credencial até o Ollama local.

Instalação dos units: `sudo REPO_DIR=/opt/vega-concierge ./control/systemd/install.sh`

## Fresh-state (ADR-035)

Todo start (`up.sh`, `up.sh update`, reboot, boot systemd) zera SQLite + volume pgvector.
**Preserva** só providers LLM em `backend/.vega-persist/llm_providers.json` — incluindo os que o
`seed_providers_from_env()` criou; a rotação de token que aconteça entre um fresh-state e o
seguinte propaga na volta (env vence a chave, ADR-039).

## Atualização pós-clone

Ops Console → **Atualizar imagens (pull)** → `./scripts/up.sh update` (GHCR público, sem login).

Pin opcional: `IMAGE_TAG=2026.08.05-abc1234` no `.env` antes do pull (CalVer ADR-036).
