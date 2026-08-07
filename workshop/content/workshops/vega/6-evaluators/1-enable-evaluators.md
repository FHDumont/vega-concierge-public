+++
title     = "Enable evaluators on the log stream"
linkTitle = "1. Enable evaluators"
weight    = 1
time      = "5 minutes"
+++

Evaluators are configured on the **log stream**, so every new trace is scored automatically.

{{< exercise title="Enable out-of-the-box evaluators" >}}

{{< step title="Open log stream settings" >}}
In Splunk Agent Observability Console, open project **`vega-concierge`** and select log stream **`default`**. Click **Configure Evaluators**.

![Configure evaluators](../images/sao-enable-evaluators.png?width=750px)
{{< /step >}}

{{< step title="Enable core evaluators" >}}
Enable at minimum for the workshop:

* **Context Adherence** — grounded in retrieved content? (UC-1)
* **Agent Efficiency** — redundant steps / token waste? (UC-2 preset)
* **Tool Errors** — failing tool calls? (Advanced inventory outage on checkout)
* **Instruction Adherence** — policy followed? (UC-3)
* **Prompt Injection** — override accepted? (UC-4)
* **PII** — sensitive data in output? (UC-5)

![Enable evaluators](../images/sao-enable-two-evaluators.png?width=750px)

Save and **Apply**. Optionally compute on **Last 1 day** for traces you already captured.

![Compute metrics](../images/sao-compute-metrics.png?width=350px)
{{< /step >}}

{{< /exercise >}}

{{< checkpoint title="Knowledge Check" >}}
Why enable evaluators on the **log stream** rather than scoring traces one by one?

{{< details summary="Click here to see the answer" >}}
Log-stream evaluators apply **automatically to every new trace**, giving continuous scaled evaluation instead of manual spot-checks.
{{< /details >}}
