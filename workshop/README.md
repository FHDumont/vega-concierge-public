# Vega Concierge — Workshop guide (Hugo)

Hands-on guide for the Vega Concierge workshop, built with [Hugo](https://gohugo.io) and the **Splunk Workshop** theme (`splunk/hugo-theme-splunk-workshop`, vendored under `themes/`).

> **Scope:** this directory is **guide only** (Hugo site). It is not app infra. Dev vs EC2:
> - **Dev (laptop):** `hugo server` here; app via `../scripts/dev.sh`; Ops Console via `../scripts/control.sh` on demand.
> - **EC2 (workshop):** app via `../scripts/boot-workshop.sh` (`vega-boot.service`); guide at **`http://<VM-IP>:1313`** (`vega-workshop.service`); Ops Console `:9000`.

## Run locally

Requires **Hugo extended ≥ 0.161**:

```bash
cd workshop
hugo server        # http://localhost:1313
```

## Build

```bash
cd workshop
hugo --minify      # output in workshop/public/
```

## Structure (Healthcare-style chapters)

Entry: `content/_index.md` → **`/workshops/vega/`**

| Chapter | Path | Content |
|---|---|---|
| **Intro** | `workshops/vega/_index.md` | Scenario, objectives, teaching arc |
| **1. Get connected** | `1-get-connected/` | VM/local access, enable Splunk Agent Observability |
| **2. Use the store** | `2-use-the-store/` | Browse, concierge, checkout, account |
| **3. Workshop panel** | `3-workshop-panel/` | Owner login, five UCs, Advanced toggles |
| **4. Enable Splunk Agent Observability** | `4-enable-galileo/` | Verify key, callback path (read-only) |
| **5. Trace & investigate** | `5-trace-and-investigate/` | Console walkthrough, five failures |
| **6. Evaluators** | `6-evaluators/` | Enable metrics, map UCs to scores |
| **7. Signals** | `7-signals/` | Generate and explore Signals |
| **8. Protect** | `8-protect/` | Agent Control Block/Steer |
| **9. Wrap-up** | `9-wrap-up/` | Recap and next steps |
| **Appendix** | `appendix/` (hidden) | LLM, agent editor, simulator, hub |

Legacy flat URLs (`/workshops/vega/01-access/`, …) redirect via Hugo **aliases**.

## Export PDF

**macOS + Safari only.** Each page is exported via **Safari → File → Export as PDF** (AppleScript — same as manual), then merged. Playwright only discovers page order.

```bash
# one-time
pip install -r workshop/requirements-export.txt
playwright install chromium

# Accessibility (required for AppleScript to click Safari menus)
# System Settings → Privacy & Security → Accessibility → enable Terminal or Cursor

./workshop/scripts/export-pdf.sh
./workshop/scripts/export-pdf.sh --include-appendix
```

While export runs, **keep Safari in front** (don't switch apps).

Output default: `workshop/export/vega-concierge-workshop.pdf`. Skips headless bundle pages (e.g. `images/`).

## Screenshots

Assets live in `content/workshops/vega/images/`. Replace **`vega-store-*.png`** and **`vega-use-cases.png`** with captures from a running instance (`:3000`) before delivery. Console assets (`galileo-*`, `sao-*`) should match your org/project if it differs from `vega-concierge` / `default`. Requires stack + `GALILEO_API_KEY` for Console captures after UC Simulate runs.

## Publish

`.github/workflows/pages.yml` (repo root) builds this folder on push to `main`.

Canonical technical reference: [`../docs/reference/galileo-readiness.md`](../docs/reference/galileo-readiness.md).
