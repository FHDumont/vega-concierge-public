+++
title     = "Account, tiers & returns"
linkTitle = "4. Account & returns"
weight    = 4
time      = "7 minutes"
aliases   = ["/workshops/vega/05-account/"]
+++

{{< lead >}}
Account ties together loyalty tier (USD spend), optional AI insights, purchase history, and a **returns.workflow** refund chain — the backbone of **UC-3**.
{{< /lead >}}

{{< exercise title="Sign in and explore refunds" >}}

{{< step title="Sign in as demo shopper" >}}
**Account** → **Fill credentials**: `demo@vega.test` / `demo1234` — Gold tier, seeded DELIVERED orders for workshop Simulate.
{{< /step >}}

{{< step title="Request a refund (agentic)" >}}
On a **Delivered** order click **Return / Refund**. The returns graph runs policy lookup, eligibility, abuse screening, and processing — each step visible in Splunk Agent Observability when tracing is on.
{{< /step >}}

{{< /exercise >}}

| Tier | Threshold |
|---|---|
| Standard | default |
| **Gold** | ≥ $1,000 |
| **Platinum** | ≥ $5,000 |

{{< notice note "UC-3 — refund false denial" >}}
Toggle **`refund_false_denial`** makes eligibility deny a refund that should succeed. Data in the trace is correct; the **decision** is wrong. **Simulate** signs in as demo and hits a DELIVERED order.
{{< /notice >}}

{{< checkpoint "You signed in, saw tier/insights, and understand the refund flow (or ran UC-3 Simulate)" >}}
