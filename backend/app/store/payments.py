"""Simulated payment gateway — fake EXTERNAL dependency (F-016).

Replaces the `time.sleep(0.4)` that stood in for "payment" at checkout. Has
CONFIGURABLE latency and failure rate (env) and responds to ProblemPanel toggles
(`payment_outage` forces failure; `payment_latency` injects high latency) — same
pattern as other problems (app breaks visibly). Logic: PENDING→PAID/FAILED."""
import random
import time

from ..problems import FLAGS
from ..settings import settings

# Configurable by env (flag): base latency and gateway failure rate.
# Default: ~400ms (mirrors old sleep) and 0% failure → reliable checkout in demo;
# failures come from payment_outage toggle (didactic). PAYMENT_LATENCY_SPIKE_MS is the
# increment when payment_latency is ON.
BASE_LATENCY_MS = settings.payment_latency_ms
FAIL_RATE = settings.payment_fail_rate
LATENCY_SPIKE_MS = settings.payment_latency_spike_ms


def charge(order: dict) -> dict:
    """Charges the order on the simulated external gateway. Returns `{paid, latency_ms, reason}`.
    `paid=False` → checkout marks order FAILED. Respects problem toggles."""
    latency_ms = BASE_LATENCY_MS + random.uniform(0, 80)
    if FLAGS.payment_latency:
        latency_ms += LATENCY_SPIKE_MS

    time.sleep(latency_ms / 1000.0)

    # payment_outage forces failure; otherwise, random failure rate (default 0).
    if FLAGS.payment_outage:
        paid, reason = False, "payment gateway unavailable"
    elif random.random() < FAIL_RATE:
        paid, reason = False, "payment declined"
    else:
        paid, reason = True, "approved"

    return {"paid": paid, "latency_ms": round(latency_ms, 1), "reason": reason}
