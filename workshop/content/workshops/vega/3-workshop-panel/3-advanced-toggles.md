+++
title     = "Advanced problem toggles"
linkTitle = "3. Advanced toggles"
weight    = 3
time      = "5 minutes"
aliases   = ["/workshops/vega/10-problem-panel/"]
+++

{{< lead >}}
When you need one failure without a UC preset, open **Admin → Workshop → Advanced** (owner). Same toggles as `backend/app/problems.py` — each card has **Simulate** and Splunk Agent Observability evaluator hints.
{{< /lead >}}

| Toggle | Severity | Workshop UC | App signal |
|---|---|---|---|
| `price_hallucination` | alert | UC-1, UC-5 | Invented price/policy; PII in email (UC-5) |
| `fraud_false_positive` | critical | — | Checkout FAILED, fraud BLOCK on valid card |
| `inventory_outage` | critical | Advanced | Checkout FAILED, stock OK in catalog (Tool Errors — not preset UC-2) |
| `latency_spike` | warning | — | Slow catalog/concierge step |
| `cost_spike` | notice | — | Extra chat rounds, more tokens |
| `payment_outage` | critical | — | Payment always declines |
| `payment_latency` | warning | — | Slow payment span |
| `refund_false_denial` | alert | UC-3 | Refund denied on eligible DELIVERED order |
| `prompt_injection` | alert | UC-4 | Agent obeys shopper override / delete prompt |

{{< details summary="API for scripts (optional)" >}}
```bash
curl -s http://localhost:8000/api/problems | jq
curl -s -X PUT http://localhost:8000/api/problems \
  -H 'content-type: application/json' \
  -d '{"price_hallucination": true}'
curl -s -X POST http://localhost:8000/api/problems/preset/uc-1
```
{{< /details >}}

{{< checkpoint "You flipped an Advanced toggle, Simulated it, and matched the app signal to the Console trace" >}}
