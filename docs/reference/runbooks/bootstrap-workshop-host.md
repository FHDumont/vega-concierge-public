# Runbook — bootstrap de host workshop (Ubuntu / EC2)

Provisiona uma **VM Ubuntu 22.04+** do zero para o Vega workshop: Docker, Ollama (host), Hugo extended, ttyd, `.env`, systemd e stack pull-only (`up.sh`). Idempotente — pode re-executar após falha parcial.

## Quando usar

| Cenário | Use |
| --- | --- |
| EC2 / VM nova (sem AMI golden) | **Este runbook** |
| AMI golden já baked | `deploy-pull-only.md` — clone herda AMI |
| Laptop dev | `dev.sh` / wizard — **não** este script |

## Pré-requisitos

- Ubuntu 22.04+ (amd64), **≥ 8 GB RAM** recomendado (Ollama + containers)
- Security Group / firewall: **3000, 8000, 9000, 1313** (+ **22** SSH); **11434 (Ollama) fechado** externamente — só host local (ADR-040)
- Imagens GHCR **públicas** (`./scripts/verify-ghcr-public.sh` no laptop)
- Acesso `sudo` na VM

## Passo a passo (uma linha de clone + um script)

```bash
git clone https://github.com/FHDumont/vega-concierge-public.git /opt/vega-concierge
cd /opt/vega-concierge
sudo ./scripts/bootstrap-workshop-host.sh
```

Tempo típico: **5–15 min** (dominado por `ollama pull` + pull GHCR).

### Opções úteis

```bash
# Senha custom do terminal Ops (:9000 → /shell/)
sudo ./scripts/bootstrap-workshop-host.sh --control-password 'minha-senha'

# Rótulo da instância no /api/health
sudo ./scripts/bootstrap-workshop-host.sh --deployment-env user-participante-01

# Reinstalar deps/systemd sem subir stack de novo
sudo ./scripts/bootstrap-workshop-host.sh --skip-up

# Re-run sem re-pull Ollama (mais rápido)
sudo ./scripts/bootstrap-workshop-host.sh --skip-models

# Sobrescrever .env existente
sudo ./scripts/bootstrap-workshop-host.sh --force-env

# Só verificar portas/health (pós-boot)
sudo ./scripts/bootstrap-workshop-host.sh --check
```

## O que o script instala / configura

| Componente | Detalhe |
| --- | --- |
| Docker + compose plugin | via get.docker.com |
| Ollama (host) | `OLLAMA_HOST=0.0.0.0:11434` (containers alcançam via `host.docker.internal`) |
| Modelos | `llama3.2`, `nomic-embed-text` |
| Hugo extended | `.deb` GitHub (**não** snap — snap não acessa `/opt`) |
| ttyd | apt; desabilita unit `ttyd.service` padrão (evita conflito :7681) |
| python3-venv | venv do Ops Console |
| `.env` | workshop/production; só grava se ausente (use `--force-env` p/ sobrescrever). Defaults anti-abuso: `API_RATE_AI_MAX=12`, `API_RATE_DEFAULT_MAX=60`, `LLM_RATE_MAX=20` |
| systemd | `vega-boot`, `vega-control`, `vega-workshop`, `vega-ttyd` |
| Workshop | override `User=` + limpa `workshop/public/` root-owned |
| Stack | `./scripts/up.sh` (fresh-state + pull + rag-init + health) |

## URLs após sucesso

Substitua `<VM-IP>` pelo IPv4 público (use **Elastic IP** p/ não mudar após stop/start).

| Superfície | URL |
| --- | --- |
| Loja | `http://<VM-IP>:3000` |
| API | `http://<VM-IP>:8000` |
| Ops Console | `http://<VM-IP>:9000` |
| Guia workshop | `http://<VM-IP>:1313` |

Senha do terminal web (Ops → `/shell/`): valor de `CONTROL_PASSWORD` no `.env` (default `vega-workshop`).

## Verificação

Saída final do script (bloco `=== VEGA HOST CHECK`):

```text
:3000=up :8000=up :9000=up :1313=up :11434=up
health=ok ollama=True
vega_ws=active vega_ops=active
```

Manual:

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
sudo ./scripts/bootstrap-workshop-host.sh --check
```

Checklist completo do dono: `validation-F-DEPLOY-PROD-1.md`.

### Checklist provider (camada 0 — chave compartilhada)

Com `OPENAI_API_KEY` / `AWS_BEARER_TOKEN_BEDROCK` / etc. embutidos no `.env` de cada clone (ADR-039),
**obrigatório** no console do provider antes do evento:

- Budget diário / spending cap na conta compartilhada
- Rate limit ou quota por minuto na API
- Alertas em 50% e 80% do budget
- Plano de rotação se a key vazar (hub sync + restart)

Rate limits da app (`API_RATE_*`, `LLM_RATE_*`) **complementam** — não substituem — o teto no provider.

## Troubleshooting

| Sintoma | Causa | Ação |
| --- | --- | --- |
| `:1313=down`, `permission denied` em `workshop/public` | Hugo rodou como root antes | `sudo rm -rf workshop/public && sudo chown -R ubuntu:ubuntu workshop && sudo systemctl restart vega-workshop` |
| `:7681` / terminal Ops falha | `ttyd.service` do apt conflita com `vega-ttyd` | `sudo systemctl disable --now ttyd.service` (o bootstrap já faz) |
| `dubious ownership` no Hugo | git + systemd user | Script já roda `git safe.directory`; re-execute bootstrap |
| `ollama.reachable: false` | Ollama só em 127.0.0.1 | Confirme `/etc/systemd/system/ollama.service.d/bind-all.conf` e `systemctl restart ollama` |
| `up.sh` GHCR denied | packages ainda privados | UI GitHub → Packages → Public; ver `deploy-pull-only.md` |
| SSH timeout externo, host OK | IP mudou ou SG | Elastic IP; confirme IP atual no console EC2 |

## Relacionados

- Contrato `.env`: `workshop-env-contract.md`
- Hub fleet day-0: `hub-fleet-day0.md`
- Deploy pull-only (AMI golden manual legado): `deploy-pull-only.md`
- Boot no reboot: `scripts/boot-workshop.sh` via `vega-boot.service`
