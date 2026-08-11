# Vega Ops Console (`control/`) — F-047 · ADR-025

Local panel for **infra maintenance from the browser, no SSH** (F-047). Lives on the SAME
machine as Vega and **talks to the host's Docker** (not a container).

## Two environments, two run modes

| Environment | How it runs | Lifecycle |
| --- | --- | --- |
| **Dev (laptop)** | **on-demand** via `./scripts/control.sh` (foreground; Ctrl+C stops it) | you bring it up when you need it; it does **not** stay running all the time |
| **EC2 (workshop)** | **host service (systemd)** installed by Ansible | always up + **watchdog** (`Restart=always`, enabled on boot) |

The systemd mode is what gives the maintenance tool itself a **watchdog**: it stays up even
while the Docker stack is being torn down/brought up or broken. On the laptop that's
unnecessary (and unwanted) — hence the on-demand launcher.

## What it does

> **F-049 (ADR-026):** the real-instrumentation *swap* (Instrument/Explain tabs + RUN button)
> was **removed** with the observability reset. The panel is now just **stack status + terminal**.

- **Stack status** (what's up, container health).
- **Web terminal (ttyd)** embedded in the Terminal tab — password → iframe at `/shell/` (proxy on the same port).
  **Renew SSH** / **Lock SSH** in the header renew or end the session. ttyd runs only on
  `localhost` (`-b /shell`); on EC2 **don't expose :7681** in the Security Group.

## Configuration (env)

| Variable | Role |
| --- | --- |
| `CONTROL_PASSWORD` | password for the **SSH terminal** (Terminal tab). The status panel itself is open |
| `VEGA_REPO_DIR` | repo dir with the compose files (default `/opt/vega-concierge`) |
| `CONTROL_TTYD_PORT` | ttyd port for the iframe (default `7681`) |
| `CONTROL_AUDIT_LOG` | audit log path (default `<repo>/control-audit.log`) |

## Dev (laptop) — on-demand

Recommended flow: run Vega and the Ops Console **separately**, each when you need it.

```bash
brew install ttyd          # macOS — required for the SSH terminal
./scripts/dev.sh          # backend :8000 + frontend :3000 (hot reload) — leave it running
# in another terminal, ONLY when you want to work on/test the panel:
./scripts/control.sh      # Ops Console :9000 + ttyd :7681. Ctrl+C stops it.
```

`control.sh` creates the panel's venv, reads the repo's `.env`, points `VEGA_REPO_DIR` at the
root, and uses the `CONTROL_PASSWORD` from the environment/`.env` (fallback `dev` on the laptop
only) — which only protects the SSH terminal; the panel opens directly. Options: `--no-terminal`
(no ttyd) and `--port N`. In dev, without Docker (`dev.sh`), the panel falls back to a TCP port
probe to show status (3000/8000).

Run by hand (equivalent to the script), if you prefer:

```bash
cd control
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
CONTROL_PASSWORD=change-me VEGA_REPO_DIR="$(cd .. && pwd)" \
  uvicorn app.main:app --host 0.0.0.0 --port 9000
ttyd -b /shell -i lo0 -p 7681 -W bash   # local terminal (proxied by the panel at /shell/)
```

## EC2 (workshop) — host service, always up

On the VM both processes run as **systemd** (`control/systemd/`), enabled on boot with
`Restart=always` (watchdog) — installed by `control/systemd/install.sh`, which
`ansible/playbook.yml` calls during provisioning. `control.sh` is **not** run on EC2. Port on
the VM: panel `:9000` (open in the Security Group; **do not** expose `:7681` — ttyd is
localhost-only).

## Security

Owner's call: **open panel** (read-only stack status) — only the **SSH terminal** requires a
password (`CONTROL_PASSWORD` on the Terminal tab → `/shell/` proxy). Rationale: single-user
workshop per VM; the host shell is the sensitive hatch. Main mitigation:
**restrict the Security Group** to the workshop's IP ranges (`:3000/:8000/:9000`); actions go
to the audit log. Accepted risk documented in `docs/DEBITO-TECNICO.md`.
