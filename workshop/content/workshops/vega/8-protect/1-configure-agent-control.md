+++
title     = "Configure Agent Control"
linkTitle = "1. Configure rulesets"
weight    = 1
time      = "8 minutes"
+++

Vega registers controllable steps in `backend/app/obs/galileo_control.py`. Enable rulesets in Console for these step names:

| Step name | Stage | Ruleset | Demo UC |
|---|---|---|---|
| `returns.finalize` | post | Instruction Adherence / eligible-denied | UC-3 |
| `product_qa` | pre | Prompt Injection | UC-4 discount |
| `search` | pre | Prompt Injection | UC-4 on search |
| `delete_product` | pre | Prompt Injection / destructive mutation | UC-4 delete |
| `notification_copy` | post steer | PII redact | UC-5 |
| `gift_message` | post steer | PII | UC-5 gift |

Optional env: `AGENT_CONTROL_URL`, `AGENT_CONTROL_API_KEY_HEADER` (default `Galileo-API-Key`).

The prompt injection control (`vega-prompt-injection-<your log stream>`) **already exists and is attached to your log stream, turned off** — the app creates it on boot. Your job in the first step is to flip the toggle on.

{{< exercise title="Enable Protect in Console" >}}

{{< step title="Open Agent Control" >}}
From Use cases banner or `galileo/config`, open Agent Control for your project/log stream.
{{< /step >}}

{{< step title="Turn on the prompt injection control (UC-4)" >}}
Find `vega-prompt-injection-<your log stream>` in the list and flip its toggle **on**. It is a pre-stage **Block** that denies whenever the **Prompt Injection (SLM)** metric scores ≥ 0.7 on the step input. It carries no step name, so it covers every `tool` and `llm` step — `product_qa`, `search`, `delete_product` and `list_recent_customers` alike.
{{< /step >}}

{{< step title="Create the remaining Block ruleset" >}}
For UC-3: post-stage **Block** on `returns.finalize` when instruction adherence fails.

![Agent Control rules](../images/galileo-log-stream-controls.png?width=750px)
{{< /step >}}

{{< step title="Create Steer rulesets" >}}
For UC-5: post-stage **Steer** on `notification_copy` and `gift_message` to redact PII (retries then deterministic fallback in code).
{{< /step >}}

{{< /exercise >}}

{{< checkpoint title="Knowledge Check" >}}
Why does Block on UC-3 still return HTTP 200 to the shopper?

{{< details summary="Click here to see the answer" >}}
The API returns a **safe, user-facing denial message** while Agent Control records the violation in the trace. Classic uptime metrics stay green; governance is visible in Splunk Agent Observability, not as a 500 error.
{{< /details >}}
