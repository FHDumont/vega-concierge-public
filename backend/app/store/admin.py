"""Admin: example order seed for demo (F-014).

Triggered ON DEMAND (button in Admin or endpoint), NEVER at boot — populates store/
dashboard for demonstration. Orders are guest (user_id=None): appear in
Admin (list of all orders), not in any user's history.

Dates: offsets in seconds from now. Dated in past, lifecycle
(ADR-008) materializes SHIPPED/DELIVERED on first read — so sample covers
5 statuses (offsets chosen relative to defaults SHIP_AFTER_S=30 / DELIVER_AFTER_S=90):
DELIVERED (days ago), SHIPPED (~60s, between offsets), PAID (~5s, before first),
plus FAILED and PENDING created directly (don't advance)."""
from datetime import datetime, timedelta, timezone

from .orders import create_order
from .tools import CATALOG

_DAY = 86400


def _item(sku: str, qty: int) -> dict:
    """Snapshot of item from catalog (order stores the item, doesn't reference
    live catalog — mirrors DEMO user seed)."""
    p = next(p for p in CATALOG if p["sku"] == sku)
    return {"sku": sku, "name": p["name"], "qty": qty, "price": p["price"]}


# (seconds ago, status created, customer name, [(sku, qty), ...])
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
    """Creates example orders (guest). Returns how many were created.
    Not idempotent by design: each trigger adds a batch (useful for generating
    volume in demo); Admin has 'clear' to zero before, if needed."""
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
