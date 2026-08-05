+++
title     = "Explore a trace"
linkTitle = "1. Explore a trace"
weight    = 1
time      = "8 minutes"
+++

Run **UC-1 Simulate** (or ask product Q&A on NS-001) before opening Console so you have fresh traces.

{{< exercise title="Investigate agent behavior" >}}

{{< step title="Open your project and log stream" >}}
1. Go to Splunk Agent Observability Console at the URL from `GET /api/galileo/config`.
2. Open project **`vega-concierge`** (or your `GALILEO_PROJECT` value).
3. Select log stream **`default`**.

![Project and log stream selection](../images/galileo-project.png?width=750px)
{{< /step >}}

{{< step title="Scan the trace list" >}}
Filter by **session ID** copied from the Use cases banner. Review recent traces — input/output tokens and span count at a glance.

![Trace list](../images/galileo-traces.png?width=750px)
{{< /step >}}

{{< step title="Open a trace and read the span tree" >}}
Open the UC-1 `product_qa` trace. Expand nested **LLM** and **retriever** spans — follow the agent path end to end.

![Trace detail with nested spans](../images/galileo-trace-view.png?width=750px)
{{< /step >}}

{{< step title="Inspect a span" >}}
Select an LLM span and confirm it captured **system/user messages**, **tools**, **output**, **token counts**, and **latency**.
{{< /step >}}

{{< step title="View the Trace Graph" >}}
Click **Trace graph** for a visual step-by-step view of the interaction.

![Trace Graph](../images/galileo-trace-graph.png?width=750px)
{{< /step >}}

{{< /exercise >}}

{{< checkpoint title="Knowledge Check" >}}
UC-1 returned HTTP 200 with an invented price. What in the trace explains the failure?

{{< details summary="Click here to see the answer" >}}
The **LLM output** diverges from **retriever chunks** and catalog facts — evaluators like Context Adherence score this even when the HTTP layer succeeded. The span tree shows whether retrieval ran and whether the model used it.
{{< /details >}}
