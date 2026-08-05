+++
title     = "Browse & search"
linkTitle = "1. Browse & search"
weight    = 1
time      = "8 minutes"
aliases   = ["/workshops/vega/02-browse-search/"]
+++

{{< lead >}}
Vega is a real store: **28 products** across categories (Kitchen, Tech, Audio, Wearables, Home, Gifts), product detail pages, cart, and checkout — all in **American English** and **USD**. Semantic search uses a real retriever (pgvector by default) so Splunk Agent Observability can score chunk relevance.
{{< /lead >}}

{{< exercise title="Find products three ways" >}}

{{< step title="Browse by category" >}}
Click a category pill (e.g. **Kitchen**, **Tech**). The grid filters instantly; breadcrumb **← Home › Kitchen** and **All products** bring you back.

Each card shows **USD** price and stock — **Low stock** or **Out of stock** (add-to-cart disabled). Stock is real: it drops when orders are paid. **NS-022** is seeded out-of-stock for demos.
{{< /step >}}

{{< step title="Open a product" >}}
Click any card → `/product/<sku>` (e.g. **NS-001**). You get description from the catalog, rating, **Add to cart**, and AI helpers below.
{{< /step >}}

{{< step title="Keyword search" >}}
Type a product name or tag (e.g. `espresso`). The grid filters by name and tag — exact match.
{{< /step >}}

{{< step title="Semantic search" >}}
Search by *meaning*:

```text
something to make espresso at home
```

The AI maps your phrase to catalog SKUs and shows a short **interpretation** plus a *did you mean…* hint.

`POST /api/search/semantic` runs retriever → LLM → SKU list. In Splunk Agent Observability, look for a `search` trace with nested **retriever** span.
{{< /step >}}

{{< /exercise >}}

## AI on the product page (on demand)

Nothing auto-generates on mount — you trigger each touchpoint:

- **Ask about this product** — grounded Q&A from product data + store policies via RAG. UC-1 uses SKU **NS-001**.
- **Compare** — pick a second SKU; coordinator fetches prices and writes a verdict.

![Product page with semantic search and Q&A](../images/vega-store-browse.png?width=750px)

{{< notice tip "Watch for 'AI is working'" >}}
The sparkle indicator shows while an agent runs — the same calls you'll see as traces later.
{{< /notice >}}

UC-1 **Simulate** fires product Q&A on NS-001 with `price_hallucination` on. UC-4 fires injection prompts on the same page.

{{< checkpoint "You found a product three ways — category, keyword, and semantic search — and tried product-page Q&A" >}}
