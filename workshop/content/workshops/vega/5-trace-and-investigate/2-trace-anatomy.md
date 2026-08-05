+++
title     = "The five failures in traces"
linkTitle = "2. Five failures"
weight    = 2
time      = "7 minutes"
+++

Reference when reading traces from **Simulate** or manual prompts:

| # | Failure | Trigger | Splunk Agent Observability feature | Protect |
|---|---|---|---|---|
| 1 | Invented policy/price | `price_hallucination` | `product_qa` | observe |
| 2 | Wrong tool + token waste | `inventory_outage` + `cost_spike` | `chat`, `fulfillment.workflow` | observe |
| 3 | Refund wrongly denied | `refund_false_denial` | `returns.workflow` | **Block** `returns.finalize` |
| 4 | Injection (+ delete) | `prompt_injection` | `product_qa`, chat | **Block** `delete_product` |
| 5 | PII in email | `price_hallucination` | `notification_copy` | **Steer** |

### UC-4 teaching note

Do **not** demo *print your system prompt* — the model refuses anyway. Use **discount override** or **delete NS-001**. Simulate runs Q&A and destructive concierge paths.

### Why infra dashboards stay green

System metrics answer **is it running?** Traces and evaluators answer **is the answer correct?**

{{< checkpoint "You opened a UC trace, named at least two span types (LLM, tool, retriever), and explained one failure without citing HTTP status" >}}
