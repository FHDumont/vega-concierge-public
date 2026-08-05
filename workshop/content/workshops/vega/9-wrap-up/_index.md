+++
title       = "Wrap-up"
linkTitle   = "9. Wrap-up"
weight      = 90
layout      = "chapter"
time        = "5 minutes"
description = "Congratulations — you completed the Vega Concierge Agent Observability workshop."
+++

Congratulations, you've completed the **Vega Concierge** workshop!

You took Vega Retail Co.'s agentic shopping assistant from a black box that could quietly invent prices or leak PII, and turned it into a system you can **see**, **measure**, and **govern**.

## What you accomplished

* **Shopped the store** — browse, concierge, checkout, refunds on a real FastAPI + LangGraph stack.
* **Operated the workshop panel** — five UC presets with **Simulate** and session-scoped traces.
* **Enabled Splunk Agent Observability** — pre-wired callback, one key, no agent rewrite.
* **Traced and investigated** agent behavior to find root cause across multi-step workflows.
* **Enabled evaluators** to catch hallucinations, tool errors, and injection automatically.
* **Used Signals** to surface recurring failure patterns beyond predefined metrics.
* **Applied Protect** to block dangerous actions and steer unsafe outputs at runtime.

## Why Splunk Agent Observability

Splunk Agent Observability closes the AI trust gap that traditional infrastructure and APM monitoring can't see:

* **Accurate, scalable evaluations** — purpose-built metrics on agent, RAG, and tool quality.
* **End-to-end visibility** — span trees and trace graphs for complex agent workflows.
* **Runtime guardrails** — Block and Steer before bad answers reach customers.

Optional **Splunk Observability Cloud** (`--o11y`) adds process-level APM alongside Splunk Agent Observability — see [Appendix: o11y](/workshops/vega/appendix/observability/).

## Where to go next

* Add **custom evaluators** tuned to Vega policies and catalog facts.
* Run **Experiments** in Splunk Agent Observability before changing prompts or graph topology.
* Expand Protect across more steps and environments.
* Route workshop vs. production traffic to separate log streams.

## References

* [Splunk Agent Observability documentation](https://docs.galileo.ai/)
* [Splunk Agent Observability Quickstart](https://docs.galileo.ai/getting-started/quickstart)
* [Splunk Agent Observability LangChain integration](https://docs.galileo.ai/sdk-api/third-party-integrations/langchain/langchain)
* [`docs/reference/galileo-readiness.md`](../../../../docs/reference/galileo-readiness.md)

{{< checkpoint title="Workshop complete — **nice work!**" >}}
