+++
title     = "Explore Signals"
linkTitle = "1. Explore Signals"
weight    = 1
time      = "10 minutes"
+++

{{% notice style="warning" title="Prerequisite" %}}
Signals need **enough traces** in the log stream. Run all five UC **Simulate** rounds (or ask the instructor to run simulator traffic) before generating Signals.
{{% /notice %}}

{{% notice style="warning" title="Cost note" %}}
Generating Signals runs analysis over your trace history and may incur LLM usage. In a large room, the instructor may demo this section once while participants observe.
{{% /notice %}}

{{< exercise title="Review Signals" >}}

{{< step title="Generate Signals" >}}
In Splunk Agent Observability Console, open project **`vega-concierge`** / log stream **`default`**, then click **Signals** → **Generate**.

![Generate Signals](../images/sao-generate-signals.png?width=250px)

Analysis may take a few moments over the traces from your UC runs.
{{< /step >}}

{{< step title="Review the overview" >}}
Inspect detected patterns — titles vary by traffic. Look for themes tied to Vega UCs: ungrounded Q&A (UC-1), tool failures (UC-2), policy violations (UC-3/4), or output safety (UC-5).

![Signals overview](../images/sao-signals-overview.png?width=750px)
{{< /step >}}

{{< step title="Open a signal for context" >}}
Select a signal. Read *what* the pattern is, *why* it happens, and the recommended remediation.

![Signal detail](../images/sao-signal-detail.png?width=450px)
{{< /step >}}

{{< step title="Jump to underlying traces" >}}
From the signal, pivot to contributing traces — **View affected spans** — to go from pattern to exact requests.

![Signal to traces](../images/sao-signal-traces.png?width=750px)
{{< /step >}}

{{< /exercise >}}

{{% notice title="Why this matters" style="info" %}}
Without Signals, finding recurring UC-style failures means manually combing traces. Signals connect **evaluator scores** (what you measured) to **emerging clusters** (what you didn't).
{{% /notice %}}

{{< checkpoint title="Knowledge Check" >}}
How do Signals differ from evaluators on a single trace?

{{< details summary="Click here to see the answer" >}}
Evaluators score **individual traces** against known quality dimensions. Signals **cluster recurring patterns** across many traces — including failure modes you didn't predefine — and suggest remediation.
{{< /details >}}
