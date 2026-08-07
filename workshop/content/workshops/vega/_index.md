+++
title       = "Vega Concierge"
linkTitle   = "Vega Concierge"
description = "Shop in a real storefront, break it on purpose, then watch every agent call land in Splunk Agent Observability — traces, evaluators, Signals, Protect, and (opt-in) Splunk Observability Cloud."
time        = "90 min"
authors     = ["Fernando Dumont"]
difficulty  = "beginner"
product     = "Splunk Agent Observability + Splunk Observability Cloud"
weight      = 1
layout      = "chapter"
subtitle    = "Workshop · Hands-on · 90 minutes"
aliases     = ["/workshops/vega/00-introduction/"]
+++

Agentic AI in production looks healthy right up until a shopper gets a **wrong price**, a **denied refund**, or **PII in an email** — while every HTTP response stays **200 OK**. Infrastructure metrics stay green; the failure lives in **reasoning and output quality**. That is the agentic trust gap.

**Splunk Agent Observability** closes it for **Vega Retail Co.**: a complete e-commerce store with an AI shopping concierge (FastAPI + LangGraph). You shop like any customer, then operate the **workshop panel** where five failures are injected on purpose — and watch each one appear in **Splunk Agent Observability Console** as a trace with evaluators, even though the storefront never threw an error.

> [!splunk] **The scenario.**
> **Vega Retail Co.** runs a polished online store with semantic search, a multi-turn chat concierge, checkout, and refunds. Observability for servers and APIs is solid, but agentic systems add unpredictable reasoning, hallucinations, tool errors, cost spikes, and sensitive-data exposure. One bad answer — inventing a return policy or leaking an email into notification copy — is enough to erode trust. In this workshop, *you* help Vega get ahead of that risk.

In this hands-on workshop you'll use a **pre-deployed, pre-instrumented** Vega instance: enable Splunk Agent Observability with one key, run five workshop use cases with **Simulate**, trace and score them in Console, explore **Signals**, and optionally apply **Protect** at runtime.

{{< objectives title="What you will learn" >}}
- **Use the store** — browse 28 SKUs, chat concierge, checkout, refunds (the "before" experience)
- **Operate the workshop panel** — load five UC presets, fire **Simulate**, copy session IDs
- **Enable Splunk Agent Observability** — one env key; verify traces without rewriting agent code
- **Trace and investigate** — span trees, trace graph, root cause across multi-step workflows
- **Enable evaluators** — out-of-the-box metrics that flag hallucinations, tool errors, PII
- **Surface issues with Signals** — recurring failure patterns you didn't think to measure
- **Apply Protect** — Block and Steer guardrails for UC-3, UC-4, and UC-5
{{< /objectives >}}

{{< prerequisites >}}
- A **modern web browser** (Chrome, Firefox, Safari, Edge)
- The **URL of your workshop instance** (instructor provides `http://<host>:3000`) — or run locally ([Get connected](/workshops/vega/1-get-connected/))
- For scored evaluators: a **real LLM** (Ollama is pre-seeded on the workshop AMI; cloud keys go in Admin → LLM Providers)
- About **90 minutes**. No Splunk, AI, or coding background needed.
{{< /prerequisites >}}

## The teaching arc

1. **Without `GALILEO_API_KEY`** — the store behaves normally; zero Splunk Agent Observability network calls. This is the "before".
2. **Set the key and restart** — every AI touchpoint logs sessions/traces. Enable evaluators on the Log stream.
3. **Load a UC and click Simulate** — real API requests fire with problem toggles on; read the inline result, then find the trace by session ID.
4. **Optional: Agent Control** — Block/Steer rulesets in Console stop UC-3/4/5 while the storefront still returns 200.

## What you'll do

| Part | Chapters | Outcome |
|---|---|---|
| **Get connected & shop** | 1–2 | Open the store, browse, buy, refunds |
| **Workshop panel** | 3 | Owner login, five UCs, Advanced toggles |
| **Splunk Agent Observability arc** | 4–8 | Enable → trace → evaluators → Signals → Protect |
| **Wrap-up** | 9 | Recap and next steps |
| **Appendix (optional)** | Instructor | LLM cascade, agent editor, simulator, hub |

{{< notice tip "Workshop VM layout" >}}
On the EC2 lab: store **:3000**, API **:8000**, Ops Console **:9000**, this guide **:1313**. See [Access the store](/workshops/vega/1-get-connected/1-access-the-store/).
{{< /notice >}}

{{% notice title="Primary references" style="info" %}}
* [Splunk Agent Observability documentation](https://docs.galileo.ai/)
* [Splunk Agent Observability Quickstart](https://docs.galileo.ai/getting-started/quickstart)
* [Splunk Agent Observability LangChain integration](https://docs.galileo.ai/sdk-api/third-party-integrations/langchain/langchain)
* Repo spec: [`docs/reference/galileo-readiness.md`](../../../../docs/reference/galileo-readiness.md)
{{% /notice %}}

{{< notice tip "Navigate with keyboard" >}}
Use **←** / **→** cursor keys or the pager buttons top-right to step through the workshop.
{{< /notice >}}
