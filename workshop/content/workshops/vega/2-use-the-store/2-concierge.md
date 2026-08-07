+++
title     = "The AI Concierge"
linkTitle = "2. The AI Concierge"
weight    = 2
time      = "8 minutes"
aliases   = ["/workshops/vega/03-concierge/"]
+++

{{< lead >}}
The concierge is the star of the store. Tell it what you want in plain English and it recommends a real product — with a real reason. Underneath, a **LangGraph chat router** delegates to specialists (search, product Q&A, gifts, returns, stats).
{{< /lead >}}

{{< exercise title="Chat with the concierge" >}}

{{< step title="Ask for a recommendation" >}}
Reach the concierge from the **home band** or the **floating launcher (✦)** bottom-right on every store screen.

```text
I need a gift for someone who loves cooking, budget around $200
```

The reply highlights a product card plus grounded copy (real name and **USD** price). Follow up in the same thread. All traffic goes to **`POST /api/chat`**.
{{< /step >}}

{{< step title="Try order context" >}}
From a **Delivered** order in Account, open chat — context chips can attach `order_id` for returns questions. The graph routes to the **returns** spoke when appropriate.
{{< /step >}}

{{< /exercise >}}

![Concierge chat panel with product recommendation](../images/vega-store-concierge.png?width=750px)

{{< mermaid >}}
flowchart TD
    U([Your message]) --> R[chat router]
    R -->|gift / browse| SC[store_chat specialist]
    R -->|policy / SKU| PQ[product_qa]
    SC --> T[tools: search_catalog, get_price]
    SC --> A([Reply + product card])
{{< /mermaid >}}

Session ID for Splunk Agent Observability: UUID in `localStorage` → header `X-Vega-Session` → `start_session(external_id=…)`.

UC-2 **Simulate** sends the demo gift prompt through chat with preset **`cost_spike`** on — trace **`gift_recommend.workflow`** with redundant steps (same as PDP and floating chat). Advanced **Inventory outage** on checkout is a separate demo (Tool Errors).

{{< checkpoint "The concierge recommended a real product with a reason, in a multi-turn chat thread" >}}
