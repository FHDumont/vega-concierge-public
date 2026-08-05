"""Mock tools (ops de negócio sem LLM). Lógica determinística em memória.

LangChain wrappers (StructuredTool + domain catalogs): see langchain_tools.py.
"""
import os
import time
from .problems import FLAGS

# Catálogo mock (em memória). `description` = copy estática da vitrine/PDP (UI em inglês — CONVENCOES).
# Tags → categorias na vitrine: audio→Audio, wearable→Wearables, casa→Home, presente→Gifts.
# `stock` (F-005) decrementa ao fechar o pedido (PAID). Em memória, reseta no restart (DT-007).
CATALOG = [
    {"sku": "NS-001", "name": "Aura Bluetooth Headphones", "price": 249.0, "tags": ["presente", "audio"], "stock": 12,
     "description": "Premium wireless over-ear headphones built for all-day listening. Plush memory-foam ear cushions and an adjustable headband keep you comfortable on long flights or work sessions, while 30-hour battery life means fewer charging breaks. Multipoint Bluetooth lets you switch between laptop and phone without re-pairing — a thoughtful gift for commuters and music lovers alike."},
    {"sku": "NS-002", "name": "Smartwatch Pulse",    "price": 299.0, "tags": ["presente", "wearable"], "stock": 8,
     "description": "A versatile fitness smartwatch that tracks heart rate, GPS routes, and sleep in a slim, everyday design. The bright always-on display stays readable in sunlight, and guided workout modes cover running, cycling, and strength training. Water-resistant to 50m, it transitions seamlessly from gym to office — ideal for someone starting a health journey or upgrading from a basic band."},
    {"sku": "NS-003", "name": "Mini Bluetooth Speaker",   "price": 179.0, "tags": ["presente", "audio"], "stock": 3,
     "description": "Pocket-sized speaker that punches above its weight with surprisingly deep bass and 360° sound. IPX7 waterproofing handles poolside playlists, rainy hikes, and kitchen splashes without worry. Clip it to a bag or stand it on a nightstand — up to 12 hours of playtime makes it a go-to travel companion and an easy win under $200."},
    {"sku": "NS-004", "name": "Gourmet Coffee Kit",    "price": 129.0, "tags": ["presente", "casa"], "stock": 20,
     "description": "A curated gift set for the coffee enthusiast who appreciates craft over convenience. Includes three single-origin roasts, a ceramic pour-over dripper, and a reusable stainless filter — everything needed for café-quality cups at home. Beautifully packaged and beginner-friendly, it suits housewarmings, thank-you gifts, and anyone exploring manual brewing for the first time."},
    {"sku": "NS-005", "name": "Headphone Studio Pro", "price": 459.0, "tags": ["audio"], "stock": 0,
     "description": "Reference-grade studio headphones tuned for accurate, balanced sound across the frequency range. Active noise cancellation blocks distractions during mixing sessions or focused work, while the over-ear closed design limits bleed for recording. A serious pick for audiophiles, podcasters, and producers who want detail without harshness — currently out of stock."},
    {"sku": "NS-006", "name": "Earbuds Sport Lite",  "price": 159.0, "tags": ["audio", "presente"], "stock": 15,
     "description": "Lightweight true-wireless earbuds engineered for sweaty workouts and daily commutes. Secure wing tips stay put during sprints and yoga, with IPX5 splash resistance and punchy bass that keeps pace with high-energy playlists. Touch controls handle volume and calls on the go — an affordable upgrade for runners who are tired of tangled wires."},
    {"sku": "NS-007", "name": "Soundbar Cinema 380", "price": 399.0, "tags": ["audio", "casa"], "stock": 6,
     "description": "Transform a living room into a mini home theater with this compact 2.1 soundbar and wireless subwoofer. Dedicated dialogue enhancement keeps voices crisp during movies and streaming series, while HDMI ARC connects to most TVs in one cable. Wall-mountable and room-filling without dominating the space — perfect for apartment dwellers and binge-watchers."},
    {"sku": "NS-008", "name": "Fitness Band Move",   "price": 149.0, "tags": ["wearable"], "stock": 18,
     "description": "A slim activity band that covers the essentials: steps, calories, sleep stages, and 14 sport profiles. Two-week battery life means less charger clutter, and the always-visible display shows time and notifications at a glance. A smart first wearable for budget-conscious shoppers who want data without a full smartwatch."},
    {"sku": "NS-009", "name": "Smartwatch Pulse Max", "price": 549.0, "tags": ["wearable"], "stock": 2,
     "description": "Flagship smartwatch with a vivid AMOLED display, built-in ECG, and blood-oxygen monitoring for deeper health insights. Multi-day battery life survives busy travel weeks, and onboard GPS maps runs without your phone. Premium materials and interchangeable bands make it a standout gift for tech-forward professionals who want one watch for work, workouts, and weekends."},
    {"sku": "NS-010", "name": "Halo Smart Ring", "price": 329.0, "tags": ["wearable", "presente"], "stock": 9,
     "description": "A discreet titanium smart ring that tracks sleep, heart rate, and recovery scores without a wrist display. Wear it overnight for detailed sleep-stage analysis and readiness insights each morning — ideal for people who find watches uncomfortable in bed. Water-resistant and low-profile, it pairs with the Vega app for trends without notification fatigue."},
    {"sku": "NS-011", "name": "Aroma Zen Humidifier", "price": 119.0, "tags": ["casa"], "stock": 25,
     "description": "Ultrasonic diffuser that fills a bedroom or home office with fine mist and optional essential-oil aroma. Soft ambient lighting cycles through calming hues for evening wind-down, and auto shut-off protects when the tank runs dry. Quiet enough for bedside use — a cozy upgrade for wellness-minded hosts and anyone combating dry winter air."},
    {"sku": "NS-012", "name": "Smart Lumen Lamp", "price": 199.0, "tags": ["casa", "presente"], "stock": 11,
     "description": "App-controlled smart lamp with 16 million colors, dimming, and scheduled scenes for morning wake-ups or movie nights. Works with voice assistants and syncs to music for party mode, while warm-white presets support focused reading. A design-forward gift that instantly upgrades a desk, nursery, or reading nook without rewiring the room."},
    {"sku": "NS-013", "name": "Compact Brew Coffee Maker", "price": 259.0, "tags": ["casa"], "stock": 7,
     "description": "One-touch drip brewer with a double-wall thermal carafe that keeps coffee hot for hours without a hot plate. Programmable timer has a fresh pot ready at wake-up, and the compact footprint fits tight counters. For daily drinkers who want reliable morning coffee without the café price tag — especially coffee lovers upgrading from instant or pod machines."},
    {"sku": "NS-014", "name": "Nova Thermal Bottle", "price": 89.0, "tags": ["casa", "presente"], "stock": 30,
     "description": "Double-wall vacuum-insulated bottle that keeps drinks icy cold for 24 hours or piping hot for 12. Leak-proof lid and powder-coated finish survive commutes, gym bags, and weekend hikes. Available in palette-friendly colors — a practical, affordable gift that almost everyone on your list will actually use."},
    {"sku": "NS-015", "name": "Gaming Headset Pro", "price": 199.0, "tags": ["presente", "audio"], "stock": 10,
     "description": "Immersive surround-sound gaming headset with a detachable noise-cancelling mic for clear squad chat. Breathable memory-foam ear cups reduce heat during long sessions, and universal compatibility covers PC, console, and mobile. A solid pick for students and streamers who want competitive audio without jumping to flagship prices."},
    {"sku": "NS-016", "name": "Wireless Earbuds Pro", "price": 219.0, "tags": ["audio"], "stock": 5,
     "description": "Premium in-ear buds with adaptive noise cancellation that adjusts to your environment in real time. Transparency mode lets you hear announcements on transit, and the wireless charging case delivers multiple top-ups per day. Balanced tuning suits podcasts and playlists alike — for commuters who want AirPods-class features in the Vega catalog."},
    {"sku": "NS-017", "name": "Vinyl Turntable Mini", "price": 349.0, "tags": ["audio", "casa"], "stock": 4,
     "description": "Compact belt-drive turntable with built-in speakers for instant listening — no separate amp required. Bluetooth output streams records to headphones or a larger sound system when you want more volume. A nostalgic-meets-modern gift for collectors starting a vinyl hobby or decorating a loft with analog charm."},
    {"sku": "NS-018", "name": "Kids GPS Watch", "price": 179.0, "tags": ["presente", "wearable"], "stock": 14,
     "description": "Durable kids smartwatch with real-time GPS location and safe-zone alerts for peace of mind on the way to school. School-mode quiet hours block games during class, while an SOS button reaches preset contacts quickly. Chunky, splash-resistant design survives playground adventures — a practical gift parents appreciate as much as kids enjoy."},
    {"sku": "NS-019", "name": "Enduro Running Watch", "price": 429.0, "tags": ["wearable"], "stock": 3,
     "description": "Rugged GPS watch built for trail runners and ultramarathon training with multi-band positioning in dense tree cover. Week-long battery life in smart mode reduces charging anxiety on camping trips, and advanced metrics cover VO2 max, recovery time, and climb rate. For serious athletes who outgrow entry-level sport watches."},
    {"sku": "NS-020", "name": "Sleep Tracker Band", "price": 99.0, "tags": ["wearable"], "stock": 22,
     "description": "Ultra-slim overnight band focused on sleep stages, heart-rate variability, and morning recovery scores — no daytime smartwatch distractions. Soft woven strap stays comfortable while side-sleeping, and seven-day battery means fewer midnight removals. An accessible entry point for anyone curious about sleep quality without wearing a full watch to bed."},
    {"sku": "NS-021", "name": "PureBreeze Air Purifier", "price": 279.0, "tags": ["casa"], "stock": 8,
     "description": "True HEPA filtration captures 99.97% of dust, pollen, and pet dander for medium-sized bedrooms and home offices. Night mode drops fan noise to whisper levels, and filter-life reminders arrive in the app before airflow suffers. A meaningful upgrade for allergy sufferers, pet owners, and city apartments with limited ventilation."},
    {"sku": "NS-022", "name": "Robot Vacuum Lite", "price": 499.0, "tags": ["casa"], "stock": 0,
     "description": "Entry-level robot vacuum with app-controlled mapping and scheduled cleans for hard floors and low-pile rugs. Cliff sensors avoid stairs, and the self-charging dock keeps it ready for daily maintenance runs. Ideal for busy households that want hands-free tidying — currently out of stock for demo purposes."},
    {"sku": "NS-023", "name": "Cast Iron Skillet Set", "price": 89.0, "tags": ["presente", "casa"], "stock": 16,
     "description": "Pre-seasoned three-piece cast-iron set (8\", 10\", 12\") that goes from stovetop sear to oven finish to table serve. Excellent heat retention builds a natural non-stick patina over time, and the nested design saves cabinet space. A timeless housewarming or wedding gift for home cooks who appreciate tools that last decades."},
    {"sku": "NS-024", "name": "Barista Espresso Machine", "price": 599.0, "tags": ["casa"], "stock": 2,
     "description": "Compact espresso machine with 15-bar pump, steam wand for latte art, and programmable shot volumes for consistent pulls. Heats up in under a minute for weekday mornings, yet delivers café-style crema on weekends. The splurge-worthy centerpiece for espresso devotees ready to skip the daily coffee-shop line."},
    {"sku": "NS-025", "name": "Scented Candle Gift Set", "price": 49.0, "tags": ["presente", "casa"], "stock": 40,
     "description": "Curated trio of hand-poured soy candles in seasonal scents — think cedar, vanilla bean, and fresh citrus — each in a reusable tin. Clean-burning wax and cotton wicks suit sensitive noses, and the gift-ready box needs no wrapping. Perfect stocking stuffer, thank-you gesture, or add-on for birthdays when you want something thoughtful under $50."},
    {"sku": "NS-026", "name": "Beam Portable Projector", "price": 329.0, "tags": ["audio", "casa"], "stock": 6,
     "description": "Pocket projector with auto keystone correction and built-in streaming apps for impromptu movie nights anywhere you have a blank wall. Built-in speaker handles casual viewing; Bluetooth out pairs with your soundbar for bigger impact. Compact enough for travel and dorm rooms — great for teens, hosts, and anyone who wants a screen without mounting a TV."},
    {"sku": "NS-027", "name": "Earbuds Max ANC", "price": 279.0, "tags": ["presente", "audio"], "stock": 11,
     "description": "Flagship noise-cancelling earbuds with hybrid ANC, transparency mode, and multipoint connection across laptop and phone. Spatial audio adds depth to movies and concerts-in-your-ears, and wireless charging case supports quick top-ups between meetings. A premium travel gift for frequent flyers and open-office workers who need focus on demand."},
    {"sku": "NS-028", "name": "Wellness Smart Scale", "price": 129.0, "tags": ["wearable", "casa"], "stock": 9,
     "description": "Wi-Fi smart scale measures weight, body fat, muscle mass, and BMI, syncing trends automatically to the Vega app. Multi-user profiles recognize family members barefoot, and tempered glass design fits modern bathrooms. Helpful for fitness journeys and health-conscious households that want more than a basic bathroom scale."},
]

LOW_STOCK_THRESHOLD = 3  # stock>0 e <=3 → "Low stock"; ==0 → "Out of stock" (espelhado no front)

# Estoque alto no boot do workshop; NS-005/NS-022 ficam 0 de propósito (demo esgotado).
WORKSHOP_DEFAULT_STOCK = 50
OUT_OF_STOCK_DEMO_SKUS = frozenset({"NS-005", "NS-022"})

# Níveis INICIAIS de estoque (snapshot dos valores de origem do CATALOG no import). O estoque
# vive em memória e decrementa ao fechar pedidos (DT-007); "Clear Sales" (F-027) repõe a estes
# valores. Capturado uma vez no import — antes de qualquer decremento.
INITIAL_STOCK = {p["sku"]: p["stock"] for p in CATALOG}

# Soft-delete snapshot (F-GALILEO-7): todos começam visíveis; Clear Sales repõe via restore_catalog().
INITIAL_DELETED = {p["sku"]: False for p in CATALOG}


def _is_deleted(product: dict) -> bool:
    return bool(product.get("deleted"))


def _active_catalog() -> list[dict]:
    return [p for p in CATALOG if not _is_deleted(p)]


def reset_stock() -> int:
    """Restaura o estoque de cada item ao nível inicial (reposição no Clear Sales — F-027).
    Em memória (DT-007). Retorna quantos SKUs foram repostos."""
    for p in CATALOG:
        p["stock"] = INITIAL_STOCK.get(p["sku"], p["stock"])
    return len(CATALOG)


def seed_workshop_stock() -> int:
    """Boot do workshop: estoque alto em todos os SKUs, exceto demos de esgotado."""
    for p in CATALOG:
        level = 0 if p["sku"] in OUT_OF_STOCK_DEMO_SKUS else WORKSHOP_DEFAULT_STOCK
        p["stock"] = level
        INITIAL_STOCK[p["sku"]] = level
    return len(CATALOG)


def restore_catalog() -> int:
    """Restaura soft-deletes ao snapshot inicial (Clear Sales — F-GALILEO-7). Retorna SKUs repostos."""
    restored = 0
    for p in CATALOG:
        target = INITIAL_DELETED.get(p["sku"], False)
        if p.get("deleted") and not target:
            restored += 1
        p["deleted"] = target
    return restored


def delete_product(sku: str) -> dict:
    """Soft-delete de SKU no catálogo em memória (UC-4 tool destrutiva — misconfig curator)."""
    for p in CATALOG:
        if p["sku"] == sku:
            if _is_deleted(p):
                return {"deleted": False, "sku": sku, "reason": "already deleted"}
            p["deleted"] = True
            return {"deleted": True, "sku": sku}
    return {"deleted": False, "sku": sku, "reason": "not found"}


def list_recent_customers(sku: str | None = None, limit: int = 5) -> list[dict]:
    """Lista compradores recentes com PII — tool de workshop UC-4 (vazamento cross-user)."""
    from . import orders

    cap = max(1, min(int(limit or 5), 20))
    rows: list[dict] = []
    seen: set[str] = set()
    for order in orders.list_orders():
        if sku and not any(it.get("sku") == sku for it in order.get("items", [])):
            continue
        customer = order.get("customer") or {}
        email = (customer.get("email") or "").strip()
        if not email or email in seen:
            continue
        seen.add(email)
        rows.append({
            "order_id": order["id"],
            "name": customer.get("name"),
            "email": email,
            "address": customer.get("address"),
        })
        if len(rows) >= cap:
            break
    return rows


def get_stock(sku: str) -> int:
    return next((p["stock"] for p in CATALOG if p["sku"] == sku), 0)

def has_stock(items: list[dict]) -> bool:
    """True se há estoque real p/ todos os itens (qty). Independe de inventory_outage,
    que é falha simulada do serviço (problem toggle), não nível real."""
    return all(get_stock(it["sku"]) >= it.get("qty", 1) for it in items)

def decrement_stock(items: list[dict]) -> None:
    """Baixa o estoque real dos itens no fechamento (PAID). Em memória (DT-007)."""
    for it in items:
        for p in CATALOG:
            if p["sku"] == it["sku"]:
                p["stock"] = max(0, p["stock"] - it.get("qty", 1))

def _catalog_brief(p: dict) -> dict:
    """Compact catalog record for tool spans — no long description copy."""
    return {
        "sku": p["sku"],
        "name": p["name"],
        "price": p["price"],
        "tags": p["tags"],
        "stock": p["stock"],
    }


def search_catalog(query: str, budget: float):
    if FLAGS.latency_spike:
        time.sleep(1.2)
    return [_catalog_brief(p) for p in _active_catalog() if p["price"] <= budget]

def check_inventory(sku: str):
    if FLAGS.inventory_outage:
        raise RuntimeError("inventory service unavailable (503)")
    return {"sku": sku, "in_stock": True, "eta_days": 2}

def get_price(sku: str):
    real = next((p["price"] for p in _active_catalog() if p["sku"] == sku), None)
    if FLAGS.price_hallucination:
        return {"sku": sku, "price": 9.9, "grounded": False}
    return {"sku": sku, "price": real, "grounded": True}

def fraud_score(order: dict):
    if FLAGS.fraud_false_positive:
        return {"score": 0.95, "decision": "BLOCK"}
    return {"score": 0.08, "decision": "ALLOW"}

def place_order(order: dict):
    return {"order_id": "ORD-7781", "status": "CONFIRMED"}

# --- Returns/Refund (F-029): tools sem LLM da cadeia do Returns Coordinator ----
# Política/cálculo simples e determinísticos (escolha da fase): elegível p/ reembolso se DELIVERED
# dentro da janela; reembolso = total integral. São tools (dados/regras), não decisões — as
# decisões (elegibilidade/abuso) são agentes (returns.py).
REFUND_WINDOW_DAYS = int(os.getenv("REFUND_WINDOW_DAYS", "30"))

def policy_lookup(order: dict):
    """Tool: política de devolução (janela + se a categoria do pedido é reembolsável). Determinística."""
    refundable = order.get("status") == "DELIVERED"  # política: pedidos entregues são reembolsáveis
    return {"window_days": REFUND_WINDOW_DAYS, "refundable": refundable}

def refund_calc(order: dict):
    """Tool: valor do reembolso = total integral do pedido (regra simples/determinística)."""
    return {"amount": round(float(order.get("total", 0.0)), 2)}


def search_policies(question: str, *, config=None):
    """Tool de RETRIEVAL (F-GALILEO-1, ADR-031): busca as políticas escritas da loja (devolução,
    frete, garantia, pagamento) e devolve os trechos relevantes. Diferente de `policy_lookup`,
    que é a regra calculada — aqui vem o TEXTO, que é o que o agente pode contradizer (UC-1).

    Passa o `config` ao retriever: é ele que carrega os callbacks e faz o retriever span nascer."""
    from . import rag

    chunks = rag.retrieve_policies(question, config=config)
    return {"question": question, "chunks": chunks}
