+++
title     = "Verify evaluators on the log stream"
linkTitle = "1. Verify evaluators"
weight    = 1
time      = "5 minutes"
+++

Evaluators are configured on the **log stream**, so every new trace is scored automatically.

Your app already did the heavy lifting: when the backend created your log stream on first boot, it enabled the workshop's core evaluators via the Galileo SDK. In this exercise you **verify** that configuration (and see where you'd manage it by hand).

{{< exercise title="Verify the out-of-the-box evaluators" >}}

{{< step title="Open log stream settings" >}}
In Splunk Agent Observability Console, open project **`vega-concierge`** and select your log stream. Click **Configure Evaluators**.

![Configure evaluators](../images/sao-enable-evaluators.png?width=750px)
{{< /step >}}

{{< step title="Verify the core evaluators" >}}
These should already be enabled (the backend enables them when it creates the stream):

* **Context Adherence (SLM)** — grounded in retrieved content? (UC-1)
* **Agent Efficiency** — redundant steps / token waste? (UC-2 preset)
* **Correctness** — answer factually right? (UC-3)
* **Correctness AWS Bedrock** — the same judgment from a Bedrock-hosted model (custom scorer)
* **Instruction Adherence** — policy followed? (UC-3)
* **Prompt Injection (SLM)** — override accepted? (UC-4)
* **Input PII (SLM)** / **Output PII (SLM)** — sensitive data in or out? (UC-5)

![Enable evaluators](../images/sao-enable-two-evaluators.png?width=750px)

Missing one? Enable it here, then Save and **Apply**. Optionally compute on **Last 1 day** for traces you already captured.

![Compute metrics](../images/sao-compute-metrics.png?width=350px)
{{< /step >}}

{{< /exercise >}}

{{< checkpoint title="Knowledge Check" >}}
Why enable evaluators on the **log stream** rather than scoring traces one by one?

{{< details summary="Click here to see the answer" >}}
Log-stream evaluators apply **automatically to every new trace**, giving continuous scaled evaluation instead of manual spot-checks.
{{< /details >}}
