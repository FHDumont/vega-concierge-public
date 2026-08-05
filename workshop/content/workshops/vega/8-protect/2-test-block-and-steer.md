+++
title     = "Test Block and Steer"
linkTitle = "2. Test Block & Steer"
weight    = 2
time      = "7 minutes"
+++

{{< exercise title="Prove Protect works" >}}

{{< step title="UC-3 — Block wrongful denial" >}}
Load UC-3 → **Simulate** refund. With Block on `returns.finalize`, shopper sees safe outcome; trace shows control firing.

![Blocked refund trace](../images/galileo-control-trace.png?width=750px)
{{< /step >}}

{{< step title="UC-4 — Block destructive delete" >}}
Load UC-4 → **Simulate**. With Block on `delete_product`, catalog stays intact; chat may show safe refusal.

![Blocked chat response](../images/galileo-control-blocked-chat.png?width=750px)
{{< /step >}}

{{< step title="UC-5 — Steer PII away" >}}
Load UC-5 → **Simulate** notification copy. Steer redacts PII in output; trace shows steer attempts.

![Steered response](../images/galileo-steered-response-chat.png?width=750px)
{{< /step >}}

{{< /exercise >}}

{{< checkpoint "You configured at least one Block or Steer ruleset, re-ran the matching UC, and confirmed control fired in the trace" >}}
