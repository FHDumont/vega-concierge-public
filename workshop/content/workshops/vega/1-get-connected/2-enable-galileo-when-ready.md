+++
title     = "Enable Splunk Agent Observability (when ready)"
linkTitle = "2. Enable Splunk Agent Observability"
weight    = 2
time      = "5 minutes"
+++

You do **not** add the Splunk Agent Observability SDK in this workshop — Vega ships **pre-instrumented**. When your instructor says to turn tracing on, set environment variables and restart the stack.

{{< exercise title="Enable tracing with one key" >}}

{{< step title="Add credentials to `.env`" >}}
```ini
GALILEO_API_KEY=<your-key>
GALILEO_CONSOLE_URL=https://console.multitenant.galileocloud.io
GALILEO_PROJECT=vega-concierge
GALILEO_LOG_STREAM=default
VEGA_SESSION_IDLE_MINUTES=5   # 0 = disable auto-rotation
```

Restart the stack after changing env. No rebuild.
{{< /step >}}

{{< step title="Verify in the API" >}}
```bash
curl -s http://localhost:8000/api/galileo/config | jq
# enabled: true, console_url, agent_control_url, session_idle_minutes
```
{{< /step >}}

{{< step title="Verify in the UI" >}}
Open **Admin → Workshop → Use cases**. The banner should show **Splunk Agent Observability connected** with links to Log stream and Agent Control. Copy **session ID** from the banner when filtering traces later.
{{< /step >}}

{{< /exercise >}}

| Mode | Condition | Behavior |
|---|---|---|
| **Base** | No `GALILEO_API_KEY` | Zero Splunk Agent Observability network; store works normally |
| **Instrumented** | Key + project + log stream set | Sessions, traces, LLM/tool/retriever spans; Protect if rulesets on |

{{< notice note "Real LLM for evaluator scores" >}}
Stub offline shows trace **shape**. Evaluators using LLM-as-judge need a real provider (Ollama on AMI, or Admin → LLM).
{{< /notice >}}

**Code path (read-only):** `galileo_obs.init` → `GalileoAsyncCallback` in `build_runnable_config()` → `ai_request_scope()` wraps `galileo_context` + `start_session(external_id=session_uuid)`.

{{< checkpoint title="Knowledge Check" >}}
Why doesn't Vega require you to edit Python to start tracing?

{{< details summary="Click here to see the answer" >}}
The codebase always includes the Splunk Agent Observability callback; without `GALILEO_API_KEY` it simply does not export. Setting the key activates the same code path production would use — minimal ops change, no redeploy of agent logic.
{{< /details >}}
