+++
title     = "Hub & feature flags"
linkTitle = "Hub & flags"
weight    = 4
hidden    = true
aliases   = ["/workshops/vega/12-hub-flags/"]
+++

{{< lead >}}
In a full-room workshop configure once on the **hub** and every participant VM pulls cascade + flags. Hide **Use cases** until you're ready to reveal Splunk Agent Observability.
{{< /lead >}}

**Admin → Connection / Hub** — **local** vs **remote** config source. Hub serves resolved cascade including keys to clients (never to browsers).

**Feature flags:** `behind_the_scenes` (Use cases), `admin`, `simulator`, `inspector`. Owner always bypasses.

**Code:** `backend/app/hub.py`, `backend/app/feature_flags.py`.

{{< checkpoint "You understand hub client vs server and used flags to shape participant nav" >}}
