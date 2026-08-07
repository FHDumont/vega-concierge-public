"""Notificação/email simulada — chamada a um serviço EXTERNO (fake).

Tem latência e uma pequena taxa de falha. NÃO bloqueia o pedido: a falha é
registrada/engolida, o pedido segue válido."""
import random
import time

FAIL_RATE = 0.15  # pequena taxa de falha do "provedor de email"


def send_order_notification(order: dict) -> dict:
    """Simula um POST a um provedor de email externo (confirmação do pedido).
    Retorna `{sent, latency_ms}`; nunca levanta — a notificação não pode quebrar o pedido."""
    latency = random.uniform(0.05, 0.25)
    time.sleep(latency)
    sent = random.random() > FAIL_RATE
    return {"sent": sent, "latency_ms": round(latency * 1000, 1)}
