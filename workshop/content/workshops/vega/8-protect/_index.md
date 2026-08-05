+++
title     = "Apply Protect at runtime"
linkTitle = "8. Protect"
weight    = 80
time      = "15 minutes"
layout    = "chapter"
+++

Tracing and evaluators show problems after the fact. **Agent Control (Protect)** can **block** or **steer** unsafe behavior **before** it reaches shoppers — while the storefront may still return HTTP 200 with a safe message.

{{% notice title="Persona" style="orange" icon="user" %}}
As Vega Retail Co.'s **AI engineer**, you enforce policy at runtime: deny wrongful refunds, stop destructive tool calls, redact PII in notification copy.
{{% /notice %}}

{{% notice title="Where to work" style="info" %}}
Splunk Agent Observability Console → **Agent Control** (URL from `GET /api/galileo/config`). Same `GALILEO_API_KEY`. Steps are already registered in code — you configure rulesets in Console.
{{% /notice %}}

Continue to configure rulesets and test Block/Steer with UC-3, UC-4, and UC-5.
