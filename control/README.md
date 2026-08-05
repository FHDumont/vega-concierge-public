# Vega Ops Console (`control/`) — F-047 · ADR-025

Painel local de **manutenção da infra pelo navegador, sem SSH** (F-047). Vive na MESMA máquina da
Vega e **fala com o Docker do host** (não é um container).

## Dois ambientes, dois modos de rodar

| Ambiente | Como roda | Ciclo de vida |
| --- | --- | --- |
| **Dev (laptop)** | **on-demand** via `./scripts/control.sh` (foreground; Ctrl+C encerra) | você sobe quando precisa; **não** fica rodando o tempo todo |
| **EC2 (workshop)** | **serviço de host (systemd)** instalado pelo Ansible | sempre no ar + **watchdog** (`Restart=always`, enable no boot) |

O modo systemd é o que dá o **watchdog** da própria manutenção: segue de pé mesmo quando o stack
Docker está sendo derrubado/subido ou quebrado. No laptop isso é desnecessário (e indesejado) — daí
o launcher on-demand.

## O que faz

> **F-049 (ADR-026):** o *swap* de instrumentação real (abas Instrumentar/Explicar + botão EXECUTAR)
> foi **removido** com o reset da observabilidade. O painel ficou como **estado do stack + terminal**.

- **Estado do stack** (o que está no ar, saúde dos containers).
- **Terminal web (ttyd)** embutido na aba Terminal — senha → iframe em `/shell/` (proxy na mesma porta).
  **Renew SSH** / **Bloquear SSH** no header renovam ou encerram a sessão. O ttyd roda só em
  `localhost` (`-b /shell`); na EC2 **não exponha :7681** no Security Group.

## Configuração (env)

| Variável | Papel |
| --- | --- |
| `CONTROL_PASSWORD` | senha do **terminal SSH** (aba Terminal). O painel de estado em si é aberto |
| `VEGA_REPO_DIR` | dir do repo com os compose files (default `/opt/vega-concierge`) |
| `CONTROL_TTYD_PORT` | porta do ttyd p/ o iframe (default `7681`) |
| `CONTROL_AUDIT_LOG` | caminho do log de auditoria (default `<repo>/control-audit.log`) |

## Dev (laptop) — on-demand

Fluxo recomendado: rode a Vega e o Ops Console **separados**, cada um quando precisar.

```bash
brew install ttyd          # macOS — necessário p/ o terminal SSH
./scripts/dev.sh          # backend :8000 + frontend :3000 (hot reload) — deixe rodando
# noutro terminal, SÓ quando quiser mexer/testar o painel:
./scripts/control.sh      # Ops Console :9000 + ttyd :7681. Ctrl+C encerra.
```

O `control.sh` cria a venv do painel, lê o `.env` do repo, aponta `VEGA_REPO_DIR` p/ a raiz e usa a
senha `CONTROL_PASSWORD` do ambiente/`.env` (fallback `dev` só no laptop) — que só protege o terminal
SSH; o painel abre direto. Opções: `--no-terminal` (sem ttyd) e `--port N`. Em dev, sem Docker
(`dev.sh`), o painel faz fallback por porta TCP p/ mostrar o estado (3000/8000).

Rodar à mão (equivalente ao script), se preferir:

```bash
cd control
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CONTROL_PASSWORD=troque VEGA_REPO_DIR="$(cd .. && pwd)" \
  uvicorn app.main:app --host 0.0.0.0 --port 9000
ttyd -b /shell -i lo0 -p 7681 -W bash   # terminal local (proxy no painel /shell/)
```

## EC2 (workshop) — serviço de host, sempre no ar

Na VM os dois processos sobem como **systemd** (`control/systemd/`), habilitados no boot e com
`Restart=always` (watchdog) — instalados pelo `control/systemd/install.sh`, que o `ansible/playbook.yml`
chama no provisionamento. **Não** se roda o `control.sh` na EC2. Porta na VM: painel `:9000` (abrir no
Security Group; **não** exponha `:7681` — ttyd é localhost-only).

## Segurança

Decisão do dono: **painel aberto** (estado read-only do stack) — só o **terminal SSH** pede
senha (`CONTROL_PASSWORD` na aba Terminal → proxy `/shell/`). Racional: workshop single-user por VM;
o shell do host é a escotilha sensível. Mitigação principal:
**restringir o Security Group** às faixas de IP do workshop (`:3000/:8000/:9000`); ações em log de
auditoria. Risco aceito no `docs/DEBITO-TECNICO.md`.
