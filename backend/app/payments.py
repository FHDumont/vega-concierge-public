"""Gateway de pagamento simulado — dependência EXTERNA fake (F-016).

Substitui o `time.sleep(0.4)` que fazia as vezes de "pagamento" no checkout. Tem
latência e taxa de falha CONFIGURÁVEIS (env) e responde aos toggles do ProblemPanel
(`payment_outage` força falha; `payment_latency` injeta latência alta) — no mesmo
padrão dos demais problemas (a app quebra de forma visível). Lógica: PENDING→PAID/FAILED."""
import os
import random
import time

from .problems import FLAGS

# Configuráveis por env (flag): latência base e taxa de falha do "gateway".
# Default: ~400ms (espelha o antigo sleep) e 0% de falha → checkout confiável na demo;
# as falhas vêm do toggle payment_outage (didático). PAYMENT_LATENCY_SPIKE_MS é o
# acréscimo quando payment_latency está ON.
BASE_LATENCY_MS = float(os.getenv("PAYMENT_LATENCY_MS", "400"))
FAIL_RATE = float(os.getenv("PAYMENT_FAIL_RATE", "0.0"))
LATENCY_SPIKE_MS = float(os.getenv("PAYMENT_LATENCY_SPIKE_MS", "1500"))


def charge(order: dict) -> dict:
    """Cobra o pedido no gateway externo simulado. Retorna `{paid, latency_ms, reason}`.
    `paid=False` → o checkout marca o pedido FAILED. Respeita os toggles de problema."""
    latency_ms = BASE_LATENCY_MS + random.uniform(0, 80)
    if FLAGS.payment_latency:
        latency_ms += LATENCY_SPIKE_MS

    time.sleep(latency_ms / 1000.0)

    # payment_outage força falha; senão, taxa de falha aleatória (default 0).
    if FLAGS.payment_outage:
        paid, reason = False, "payment gateway unavailable"
    elif random.random() < FAIL_RATE:
        paid, reason = False, "payment declined"
    else:
        paid, reason = True, "approved"

    return {"paid": paid, "latency_ms": round(latency_ms, 1), "reason": reason}
