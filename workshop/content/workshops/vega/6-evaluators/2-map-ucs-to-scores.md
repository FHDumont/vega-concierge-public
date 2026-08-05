+++
title     = "Map UCs to evaluator scores"
linkTitle = "2. Map UCs to scores"
weight    = 2
time      = "5 minutes"
+++

After enabling evaluators, re-run **Simulate** for each UC and read scores on the trace:

| UC | Enable these evaluators | What to look for |
|---|---|---|
| UC-1 | Context Adherence, Chunk Relevance, Chunk Attribution, Completeness, Correctness | Low adherence despite retriever spans |
| UC-2 | Tool Selection Quality, Tool Errors, Agent Efficiency, Agent Flow | `check_inventory` error; extra chat rounds |
| UC-3 | Instruction Adherence, Correctness, Agent Flow | Eligibility data correct; decision wrong |
| UC-4 | Prompt Injection, Toxicity, Context Adherence | Injection obeyed; destructive tool attempted |
| UC-5 | PII, Tone, Toxicity | Email copy contains PII |

![Evaluator scores on a flagged trace](../images/sao-evaluator-scores.png?width=750px)

{{< exercise title="Score UC-1" >}}
1. **New session** in Use cases banner.
2. **Load** UC-1 → **Simulate**.
3. Open trace → check **Context Adherence** and related RAG metrics.
{{< /exercise >}}

{{< checkpoint "You enabled evaluators, re-ran at least one UC, and named one metric that flagged the injected failure" >}}
