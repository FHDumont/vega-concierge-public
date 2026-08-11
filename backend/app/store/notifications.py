"""Simulated notification/email — call to an EXTERNAL (fake) service.

Has latency and a small failure rate. Does NOT block order: failure is
logged/swallowed, order stays valid."""
import random
import time

FAIL_RATE = 0.15  # small failure rate of "email provider"


def send_order_notification(order: dict) -> dict:
    """Simulates POST to external email provider (order confirmation).
    Returns `{sent, latency_ms}`; never raises — notification cannot break order."""
    latency = random.uniform(0.05, 0.25)
    time.sleep(latency)
    sent = random.random() > FAIL_RATE
    return {"sent": sent, "latency_ms": round(latency * 1000, 1)}
