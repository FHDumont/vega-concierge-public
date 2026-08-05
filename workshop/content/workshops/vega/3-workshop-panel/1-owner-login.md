+++
title     = "Owner login & portal"
linkTitle = "1. Owner login"
weight    = 1
time      = "5 minutes"
aliases   = ["/workshops/vega/06-owner-login/"]
+++

{{< lead >}}
You've shopped as a customer. Now put on the operator hat: the same app exposes business dashboards, the **workshop use-case panel**, and owner-only configuration behind a real login.
{{< /lead >}}

{{< exercise title="Sign in and find the workshop" >}}

{{< step title="Sign in as owner" >}}
- **Owner:** `fernando@fernando.com.br` / `OWNER_PASSWORD` (from `.env` on the VM)

Backend enforces owner on sensitive APIs (`401`/`403`), not just UI hiding.
{{< /step >}}

{{< step title="Open Use cases" >}}
Store header → **Use Cases** (`/use-cases`):

- **Splunk Agent Observability banner** — connected/off, Console + Agent Control links, **Copy session ID**, **New session**
- **Five UC cards** — Load scenario, **Simulate**, expandable steps
{{< /step >}}

{{< /exercise >}}

![Workshop use cases panel with Splunk Agent Observability banner and UC cards](../images/vega-use-cases.png?width=750px)

Navigation groups: **Store / Account** (everyone) · **Business** (flag `admin`) · **Workshop** (Simulator; Advanced → owner) · **Global Settings** (owner). **Use cases** live in the store header when flag `behind_the_scenes` is on.

{{< notice note "Feature flags" >}}
Owner can hide **Use cases** from participants via **Feature Flags** (`behind_the_scenes`) — reveal mid-session from hub for all VMs. Owner always bypasses flags.
{{< /notice >}}

{{< checkpoint "You're signed in as owner and can open Admin → Use cases" >}}
