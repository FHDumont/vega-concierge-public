+++
title     = "Enable Splunk Agent Observability"
linkTitle = "4. Enable Splunk Agent Observability"
weight    = 40
time      = "10 minutes"
layout    = "chapter"
aliases   = ["/workshops/vega/14-galileo/"]
+++

You can't observe what you don't capture. Vega ships with **Splunk Agent Observability tracing already wired** — your job is to enable credentials and confirm the callback is live.

{{% notice title="Persona" style="orange" icon="user" %}}
As Vega Retail Co.'s **AI engineer**, you want end-to-end visibility into agent decisions with minimal code change. Rather than hand-instrument every step, the app attaches a single LangChain callback at the graph level and Splunk Agent Observability captures the whole tree automatically.
{{% /notice %}}

> [!splunk] **Instrumentation is lightweight:** a Splunk Agent Observability callback is a standard LangChain callback handler. Attach it to a LangGraph run and it captures prompts, responses, model names, token usage, timing, and span nesting for you.

{{% notice title="Where to work" style="info" %}}
`.env` on the VM or local repo; verify in **Admin → Use cases** banner and `GET /api/galileo/config`.
{{% /notice %}}

Continue to verify configuration and understand the callback path (read-only).
