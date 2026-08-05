+++
title     = "Simulator & Inspector"
linkTitle = "Simulator"
weight    = 3
hidden    = true
aliases   = ["/workshops/vega/11-simulator/"]
+++

{{< lead >}}
One request teaches; **traffic** makes dashboards and Signals meaningful. The simulator runs real journeys; the Inspector captures LLM prompts locally for debugging.
{{< /lead >}}

**Admin → Workshop → Simulator** (`/admin/simulator`, flag `simulator`): concurrent journeys — login, browse, optional chat, checkout. **API** or **Browser** (Playwright) mode. **Problem injection %** briefly flips global toggles.

**Admin → Inspector** (`/admin/llm-activity`, flag `inspector`): ring buffer of prompts/responses — orthogonal to Splunk Agent Observability; never exports prompt text to spans.

Use simulator before the **Signals** chapter to populate trace volume.

**Code:** `backend/app/simulator.py`, `backend/app/llm_activity.py`.

{{< checkpoint "You started simulator traffic and read a call in Inspector" >}}
