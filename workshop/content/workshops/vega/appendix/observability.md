+++
title     = "Splunk Observability Cloud"
linkTitle = "Splunk o11y"
weight    = 5
hidden    = true
aliases   = ["/workshops/vega/13-observability/"]
+++

{{< lead >}}
Splunk Agent Observability is the **primary** workshop observability story (SDK callback on LangChain). Splunk Observability Cloud is a **second layer**: process-level auto-instrumentation + GenAI semantic conventions, enabled only with **`--o11y`**.
{{< /lead >}}

```bash
./scripts/dev.sh --o11y      # dev: collector profile + opentelemetry-instrument
./scripts/up.sh --o11y       # Docker prod path
```

| Signal | Source |
|---|---|
| HTTP / FastAPI spans | Auto-instrumentation |
| LangChain / LangGraph | Splunk GenAI instrumentation |
| LLM content events | `SPAN_AND_EVENT` mode |
| Payment / notification | Business CLIENT spans |

Splunk Agent Observability and Splunk can run **at the same time** — different pipelines.

Deep dive: [`docs/reference/galileo-with-otel-collector.md`](../../../../../docs/reference/galileo-with-otel-collector.md).

{{< checkpoint "You know how to enable --o11y and which workshop toggles produce interesting Splunk signals" >}}
