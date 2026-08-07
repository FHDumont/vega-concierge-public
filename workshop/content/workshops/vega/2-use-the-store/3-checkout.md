+++
title     = "Cart & checkout"
linkTitle = "3. Cart & checkout"
weight    = 3
time      = "8 minutes"
aliases   = ["/workshops/vega/04-checkout/"]
+++

{{< lead >}}
Buying something exercises the deepest agent flow: inventory check, **fraud decision**, simulated payment gateway, stock decrement, and notification — orchestrated as **`fulfillment.workflow`** and fully visible in Splunk Agent Observability when the key is set.
{{< /lead >}}

{{< exercise title="Place a paid order" >}}

{{< step title="Add items and open checkout" >}}
Add products to cart. **Checkout** (`/checkout`) — Details → Payment (`4242 4242 4242 4242` · `12/29` · `123`) → Confirmation.
{{< /step >}}

{{< step title="Optional gift message" >}}
In Details, expand **gift message** — pick a preset or type a brief; AI drafts copy.
{{< /step >}}

{{< step title="Pay" >}}
Behind **Pay**, the order goes `PENDING → PAID` or `FAILED`:

- ReAct coordinator → `check_inventory` / `get_price` tools
- **Fraud** LLM + tool `decide_fraud_allow_or_block`
- Post-ReAct: stock confirm → charge → persist → notification

On success: confirmation page with AI **status summary**.
{{< /step >}}

{{< /exercise >}}

Order lifecycle (computed from elapsed time since PAID — no background job):

```text
PAID  ──30s──▶  SHIPPED  ──90s──▶  DELIVERED
```

{{< notice note "Workshop surfaces" >}}
- **UC-2 (preset):** `cost_spike` → demo gift question via chat / PDP / Simulate — trace `gift_recommend.workflow`, Agent Efficiency.
- **Advanced:** `inventory_outage` → checkout FAILED, `check_inventory` tool error (Tool Errors — separate from preset UC-2).
- **Advanced:** `fraud_false_positive`, `payment_outage`, `payment_latency` — each card has **Simulate**.
{{< /notice >}}

{{< checkpoint "You placed an order (PAID) and saw confirmation with status summary" >}}
