+++
title     = "Five workshop use cases"
linkTitle = "2. Five use cases"
weight    = 2
time      = "10 minutes"
aliases   = ["/workshops/vega/09-behind-the-scenes/"]
+++

{{< lead >}}
The workshop panel is **Admin → Use cases**: load toggles, fire real requests with **Simulate**, copy the **session ID**, open **Splunk Agent Observability Console**.
{{< /lead >}}

## Before you start

1. Set `GALILEO_API_KEY` (+ project/log stream) and restart — see [Enable Splunk Agent Observability](/workshops/vega/1-get-connected/2-enable-galileo-when-ready/).
2. Enable evaluators in Console → Log stream (detailed in [Evaluators](/workshops/vega/6-evaluators/)).
3. Use cases works for participants when flag `behind_the_scenes` is on; owner always sees Advanced.

## The five use cases (canonical table)

| UC | Toggle(s) | Simulate (1 action) | Evaluator (enable in Console) |
|---|---|---|---|
| **UC-1** | `price_hallucination` | Product Q&A NS-001 — *"how much does it cost?"* | Context Adherence |
| **UC-2** | `inventory_outage` | Checkout demo | Tool Errors |
| **UC-3** | `refund_false_denial` | Refund on demo DELIVERED order | Correctness |
| **UC-4** | `prompt_injection` | Concierge delete NS-001 | Prompt Injection, Context Adherence |
| **UC-5** | `price_hallucination` | Notification copy for demo order | PII |

Each card shows **Scenario ON/OFF** chips, **Load scenario**, **Simulate**, and expandable steps.

{{< exercise title="Run the drill" >}}

{{< step title="UC-1 — invented price" >}}
**Simulate** → read snippet (invented price). Console → session → `product_qa` trace → Context Adherence drops (no retriever on hallucination path).
{{< /step >}}

{{< step title="UC-2 — inventory failure" >}}
**Simulate** → one checkout trace: `check_inventory` in error, order FAILED.
{{< /step >}}

{{< step title="UC-3 or UC-4" >}}
UC-3: denied refund on eligible order — eligibility span cites wrong window. UC-4: Simulate deletes NS-001 from the catalog (real soft-delete); try discount Q&A or cross-user export via Run for Prompt Injection.
{{< /step >}}

{{< step title="UC-5 — PII in email" >}}
**Simulate** notification copy — body should echo demo SSN, credit card, CVV, email, and address (not just first name).
{{< /step >}}

{{< /exercise >}}

{{< notice tip "Reset between rounds" >}}
**Clear all** on the preset bar — toggles stack if you forget. **New session** before each UC keeps Console readable.
{{< /notice >}}

Full narrative: [`docs/reference/galileo-readiness.md`](../../../../../docs/reference/galileo-readiness.md).

{{< checkpoint "You simulated two UCs, read inline results, and opened matching traces in Console (or described what to look for)" >}}
