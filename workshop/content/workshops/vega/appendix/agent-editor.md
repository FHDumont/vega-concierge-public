+++
title     = "Visual agent editor"
linkTitle = "Agent editor"
weight    = 2
hidden    = true
aliases   = ["/workshops/vega/08-agent-editor/"]
+++

{{< lead >}}
Every agent is individually configurable. The SVG diagram is derived from **`topology.py`** — the same graph the backend runs.
{{< /lead >}}

**Admin → Global Settings → Agents** (`/admin/agents`): click an agent node → Connection, Model, Role, System prompt → **Test**.

Span names map via `galileo_span.py` — compare opaque Console traces to [`galileo-readiness.md`](../../../../../docs/reference/galileo-readiness.md).

**Code:** `backend/app/topology.py`, `backend/app/agent_config.py`.

{{< checkpoint "You opened the graph, edited an agent, and tested a resolved call" >}}
