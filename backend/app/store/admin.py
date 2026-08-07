"""Admin: seed de pedidos de exemplo p/ demo (F-014).

Acionado SOB DEMANDA (botão no Admin ou endpoint), NUNCA no boot — popula a loja/
dashboard para a demonstração. Pedidos são de convidado (user_id=None): aparecem no
Admin (lista de todos os pedidos), não no histórico de nenhum usuário.

Datas: offsets em segundos a partir de agora. Datados no passado, o ciclo de vida
(ADR-008) materializa SHIPPED/DELIVERED na 1ª leitura — por isso a amostra cobre os
5 status (offsets escolhidos relativos aos defaults SHIP_AFTER_S=30 / DELIVER_AFTER_S=90):
DELIVERED (dias atrás), SHIPPED (~60s, entre os offsets), PAID (~5s, antes do 1º),
além de FAILED e PENDING criados direto (não avançam)."""
from datetime import datetime, timedelta, timezone

from .orders import create_order
from .tools import CATALOG

_DAY = 86400


def _item(sku: str, qty: int) -> dict:
    """Snapshot do item a partir do catálogo (o pedido guarda o item, não referencia
    o catálogo vivo — espelha o seed do usuário de DEMO)."""
    p = next(p for p in CATALOG if p["sku"] == sku)
    return {"sku": sku, "name": p["name"], "qty": qty, "price": p["price"]}


# (segundos atrás, status criado, nome do cliente, [(sku, qty), ...])
_SAMPLE: list[tuple[int, str, str, list[tuple[str, int]]]] = [
    (45 * _DAY, "PAID", "Marina Alves", [("NS-002", 1)]),                    # → DELIVERED
    (30 * _DAY, "PAID", "Bruno Costa", [("NS-007", 1), ("NS-014", 2)]),      # → DELIVERED
    (12 * _DAY, "PAID", "Carla Dias", [("NS-004", 1), ("NS-011", 1)]),       # → DELIVERED
    (3 * _DAY, "PAID", "Diego Reis", [("NS-006", 2)]),                       # → DELIVERED
    (60, "PAID", "Eduarda Lima", [("NS-001", 1)]),                          # → SHIPPED (~60s)
    (5, "PAID", "Felipe Nunes", [("NS-012", 1)]),                           # → PAID (~5s)
    (8 * _DAY, "FAILED", "Gabriela Sá", [("NS-009", 1)]),                    # FAILED
    (20, "PENDING", "Heitor Rocha", [("NS-013", 1)]),                       # PENDING
]


def seed_sample_orders() -> int:
    """Cria os pedidos de exemplo (convidado). Retorna quantos foram criados.
    Não é idempotente por design: cada acionamento adiciona uma leva (útil p/ gerar
    volume na demo); o Admin tem 'limpar' para zerar antes, se quiser."""
    now = datetime.now(timezone.utc)
    for secs, status, name, items_spec in _SAMPLE:
        items = [_item(sku, qty) for sku, qty in items_spec]
        total = sum(i["qty"] * i["price"] for i in items)
        customer = {
            "name": name,
            "email": name.lower().replace(" ", ".").replace("á", "a").replace("ã", "a")
            .replace("é", "e").replace("í", "i").replace("ó", "o") + "@example.com",
            "address": "Av. Exemplo, 100 — Demo City",
        }
        created = (now - timedelta(seconds=secs)).isoformat()
        create_order(items, customer, total, status=status, created_at=created)
    return len(_SAMPLE)
