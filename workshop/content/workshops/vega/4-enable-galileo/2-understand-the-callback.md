+++
title     = "Understand the callback (read-only)"
linkTitle = "2. The callback path"
weight    = 2
time      = "5 minutes"
+++

Unlike the Healthcare workshop, you do **not** edit `agent.py` here — Vega already wires the callback. Use this page to know *where* traces originate when teaching or debugging.

## Trace anatomy

```text
Session (shopper UUID)
└── Trace (one AI request)
    ├── LLM spans (dotted names: concierge.search_catalog_and_price, …)
    ├── Tool spans (search_catalog, check_inventory, …)
    └── Retriever spans (store_policies, catalog) nested under RAG features
```

Policies live in `backend/data/policies/`; indexed to pgvector on boot.

## Code map (verify when teaching)

| Topic | File |
|---|---|
| SDK + session | `backend/app/galileo_obs.py` |
| Callback injection | `backend/app/runnable_config.py` |
| Per-endpoint scope | `backend/app/agents.py` (`ai_request_scope`) |
| Span naming | `backend/app/galileo_span.py` |
| Problem toggles | `backend/app/problems.py` |
| UC UI + Simulate | `frontend/lib/galileo-workshop.ts`, `workshop-simulate.ts` |

Full spec: [`docs/reference/galileo-readiness.md`](../../../../../docs/reference/galileo-readiness.md).

{{< checkpoint title="Knowledge Check" >}}
Where is `GalileoAsyncCallback` attached to LangGraph runs?

{{< details summary="Click here to see the answer" >}}
In `backend/app/runnable_config.py` via `build_runnable_config()` — every AI endpoint that uses `ai_request_scope()` in `agents.py` inherits the callback without per-route boilerplate.
{{< /details >}}
