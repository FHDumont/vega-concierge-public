+++
title     = "Verify configuration"
linkTitle = "1. Verify configuration"
weight    = 1
time      = "5 minutes"
+++

{{< exercise title="Confirm Splunk Agent Observability is active" >}}

{{< step title="Check environment variables" >}}
```ini
GALILEO_API_KEY=<secret>
GALILEO_CONSOLE_URL=https://console.multitenant.galileocloud.io
GALILEO_PROJECT=vega-concierge
GALILEO_LOG_STREAM=default
VEGA_SESSION_IDLE_MINUTES=5
```
{{< /step >}}

{{< step title="Verify API" >}}
```bash
curl -s http://localhost:8000/api/galileo/config | jq
```
Expect `enabled: true`, `console_url`, `agent_control_url`, `session_idle_minutes`.
{{< /step >}}

{{< step title="Verify UI banner" >}}
**Admin → Use cases** → **Splunk Agent Observability connected**. Links open Log stream and Agent Control. **Copy session ID** before running Simulate.
{{< /step >}}

{{< /exercise >}}

{{< checkpoint title="Knowledge Check" >}}
What happens if you remove `GALILEO_PROJECT` and `GALILEO_LOG_STREAM` from `.env`?

{{< details summary="Click here to see the answer" >}}
The SDK falls back to a project and log stream both named `default`. Traces still export, but you may not see them where you expect — always set explicit project/stream names for workshop clarity.
{{< /details >}}
