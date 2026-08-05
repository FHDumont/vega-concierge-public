+++
title     = "Access the store"
linkTitle = "1. Access the store"
weight    = 1
time      = "5 minutes"
aliases   = ["/workshops/vega/01-access/"]
+++

{{< lead >}}
Everything in this workshop happens in the browser. If your instructor handed you a URL, you're one click away. To run locally, it's two commands — with Ollama + RAG enabled by default on the workshop path.
{{< /lead >}}

{{< exercise title="Open Vega and verify health" >}}

{{< step title="Open the storefront" >}}
**Option A — workshop VM (EC2 lab)**

| Service | Port | URL |
|---|---|---|
| Store (frontend) | **3000** | `http://<VM-IP>:3000` |
| API (backend) | **8000** | `http://<VM-IP>:8000/api/health` |
| Ops Console (maintenance) | **9000** | `http://<VM-IP>:9000` |
| **This guide (Hugo)** | **1313** | `http://<VM-IP>:1313` |

Each participant gets an isolated instance (`DEPLOYMENT_ENVIRONMENT=user-XX` in health). Boot is automatic via `vega-boot.service` — no SSH required for normal use.
{{< /step >}}

{{< step title="Or run locally" >}}
Vega is standalone-first: with no cloud API keys it falls back to an offline **stub** so every screen still works.

{{< tabs >}}

{{< tab "Dev (hot reload)" >}}
From the repo root:

```bash {file="run.sh"}
./scripts/dev.sh              # RAG + Postgres on by default
./scripts/dev.sh --no-rag     # keyword-only retrieval, no Postgres
./scripts/dev.sh --o11y       # optional Splunk o11y stack
```

Starts backend (`:8000`) and frontend (`:3000`). Open **http://localhost:3000**.

Requires **Ollama on the host** for default embeddings/chat (`OLLAMA_BASE_URL`, `nomic-embed-text`).
{{< /tab >}}

{{< tab "Docker" >}}
```bash {file="run.sh"}
cp .env.example .env
./scripts/up.sh --build       # or pull-only on a tagged AMI
```

Frontend `:3000`, backend `:8000`. Add `--o11y` for the OTel Collector profile.
{{< /tab >}}

{{< /tabs >}}
{{< /step >}}

{{< step title="Verify the API" >}}
{{< terminal title="bash" >}}
$ curl -s http://localhost:8000/api/health | jq
{
  "status": "ok",
  "environment": "local-dev",
  "rag": { "enabled": true, "backend": "pgvector", "embedding_provider": "ollama" },
  "ollama": { "reachable": true, "models": ["llama3.2", "nomic-embed-text"] },
  "llm_providers": 1
}
{{< /terminal >}}

What to notice:

- **`rag.enabled`** — `true` when pgvector is live (default); `false` with `--no-rag`.
- **`ollama.reachable`** — host daemon for chat + embeddings on the workshop path.
- No in-app instrumentation wizard — backend tracing is **Splunk Agent Observability SDK** (opt-in key) plus optional **process o11y** (`--o11y`).
{{< /step >}}

{{< /exercise >}}

{{< notice tip "Demo accounts" >}}
- **Shopper:** `demo@vega.test` / `demo1234` (Gold tier, order history for UC-3/5)
- **Owner:** `fernando@fernando.com.br` / `OWNER_PASSWORD` from `.env`

Login has **Fill credentials** for both.
{{< /notice >}}

When the storefront loads, continue to **Use the store** or enable Splunk Agent Observability in the next page if your instructor has already distributed keys.

{{< checkpoint "The Vega storefront loads at port 3000 and `/api/health` returns ok" >}}
