"""User accounts + DEMO auth + tiers (F-008, ADR-011).

Decisions (demo, no production security — see DT-010):
- **Password:** PBKDF2-HMAC-SHA256 (stdlib `hashlib`, no new dependency). Not
  bcrypt/argon2, but avoids plaintext. Stored as `pbkdf2_sha256$iter$salt$hash`.
- **Session:** opaque token (`secrets.token_urlsafe`) sent in `Authorization: Bearer`.
  token→user_id map lives IN MEMORY (resets on restart, like stock — DT-007/DT-010).
  Chosen over httpOnly cookie because CORS is `allow_origins=["*"]`, incompatible
  with credential cookies; token in header is simpler and standalone-first (ADR-011).
- **Tier:** computed from user's CUMULATIVE SPEND (PAID/SHIPPED/DELIVERED orders) and
  lazily materialized in the `tier` column (mirrors lifecycle pattern — ADR-008).
  Simple thresholds, configurable by env.

Users persist in SQLite (same file as orders — ADR-006).
"""
import hashlib
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from .db import connect
from .orders import create_order as _create_order  # test user seed (F-010)
from ..settings import settings

# Tier thresholds by cumulative spend (BRL). Configurable by env; simple values for demo.
GOLD_THRESHOLD = settings.tier_gold_usd
PLATINUM_THRESHOLD = settings.tier_platinum_usd

PBKDF2_ITERATIONS = settings.auth_pbkdf2_iterations

# token → user_id (demo sessions, in memory; reset on restart — DT-010).
_SESSIONS: dict[str, str] = {}


def init_db() -> None:
    """create_all at boot: users table if it doesn't exist."""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                email       TEXT NOT NULL UNIQUE,
                password    TEXT NOT NULL,   -- pbkdf2_sha256$iter$salt_hex$hash_hex
                tier        TEXT NOT NULL DEFAULT 'STANDARD',  -- STANDARD | GOLD | PLATINUM
                role        TEXT NOT NULL DEFAULT 'STANDARD',  -- STANDARD | OWNER (config gate — F-020)
                address     TEXT NOT NULL DEFAULT '',  -- saved address (F-011); pre-fills checkout
                created_at  TEXT NOT NULL    -- ISO-8601 UTC
            )"""
        )
        # Additive migrations: `address` (F-011), `role` (F-020) in pre-existing tables.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
        if "address" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN address TEXT NOT NULL DEFAULT ''")
        if "role" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'STANDARD'")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return "USR-" + uuid.uuid4().hex[:8].upper()


# --- password (PBKDF2, stdlib) -----------------------------------------------

def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iters))
        return secrets.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# --- tier -------------------------------------------------------------------

def tier_for_spend(spend: float) -> str:
    """Tier from cumulative spend. Thresholds configurable (env)."""
    if spend >= PLATINUM_THRESHOLD:
        return "PLATINUM"
    if spend >= GOLD_THRESHOLD:
        return "GOLD"
    return "STANDARD"


def _row_to_user(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "tier": row["tier"],
        "role": row["role"] if "role" in keys else "STANDARD",
        "address": row["address"] if "address" in keys else "",
        "created_at": row["created_at"],
    }


def public_user(user: dict, spend: float) -> dict:
    """API payload: recomputes tier from spend (does not expose password).
    `address` (F-011) is the saved address that pre-fills checkout.
    `role` (F-020) is STANDARD|OWNER — front hides config screen for non-owner."""
    return {
        "id": user["id"],
        "name": user["name"],
        "email": user["email"],
        "tier": tier_for_spend(spend),
        "spend": round(spend, 2),
        "address": user.get("address", ""),
        "role": user.get("role", "STANDARD"),
    }


# --- CRUD + auth ------------------------------------------------------------

def register(name: str, email: str, password: str) -> dict:
    """Creates a user. Raises ValueError if email already exists."""
    email = email.strip().lower()
    user = {
        "id": _new_id(),
        "name": name.strip(),
        "email": email,
        "password": _hash_password(password),
        "tier": "STANDARD",
        "created_at": _now_iso(),
    }
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO users (id, name, email, password, tier, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], user["name"], user["email"], user["password"], user["tier"], user["created_at"]),
            )
    except sqlite3.IntegrityError:
        raise ValueError("email already registered")
    return _row_to_user_dict(user)


def _row_to_user_dict(user: dict) -> dict:
    """Version without password (for internal use after creation)."""
    return {k: user[k] for k in ("id", "name", "email", "tier", "created_at")}


def get_user(user_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    return _row_to_user(row) if row else None


def authenticate(email: str, password: str) -> dict | None:
    """Checks email+password; returns user (without password) or None."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if row is None or not _verify_password(password, row["password"]):
        return None
    return _row_to_user(row)


def update_tier(user_id: str, tier: str) -> None:
    """Materializes computed tier in column (lazy, on /me)."""
    with connect() as conn:
        conn.execute("UPDATE users SET tier = ? WHERE id = ?", (tier, user_id))


def update_address(user_id: str, address: str) -> None:
    """Saves/edits user address in profile (F-011)."""
    with connect() as conn:
        conn.execute("UPDATE users SET address = ? WHERE id = ?", (address.strip(), user_id))


# --- OWNER role (LLM config gate — F-020) ----------------------------

def update_role(user_id: str, role: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def is_owner(user_id: str) -> bool:
    """True if user has OWNER role (access to LLM config screen/endpoints)."""
    user = get_user(user_id)
    return bool(user and user.get("role") == "OWNER")


# --- sessions (in memory) ---------------------------------------------------

def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = user_id
    return token


def session_user_id(token: str) -> str | None:
    return _SESSIONS.get(token)


def drop_session(token: str) -> None:
    _SESSIONS.pop(token, None)


# --- demo test user (DEMO seed — F-010) --------------------------------
# Standard account to ease workshop validation. FIXED and
# known credentials → UNSAFE by design (demo layer only; see DT-010). Example orders
# sum ~$1,275 → GOLD tier (≥$1,000) with non-empty history.
DEMO_EMAIL = "demo@vega.test"
DEMO_PASSWORD = "demo1234"
DEMO_NAME = "Demo User"
_DEMO_ADDRESS = "221B Demo Street, Test City"
# Fictional payment data (workshop UC-5 — DT-010; never use in production).
_DEMO_PAYMENT = {
    "ssn": "123-45-6789",
    "card_number": "4242 4242 4242 4242",
    "card_exp": "08/28",
    "card_cvv": "123",
}


def _demo_customer() -> dict:
    return {
        "name": DEMO_NAME,
        "email": DEMO_EMAIL,
        "address": _DEMO_ADDRESS,
        **_DEMO_PAYMENT,
    }

# (days ago, items). Dated in the past → lifecycle (ADR-008) materializes
# SHIPPED/DELIVERED on first read, giving realistic history. Items are snapshots
# (mirror catalog SKUs; orders store the item, don't reference live catalog).
_DEMO_ORDERS = [
    (40, [{"sku": "NS-002", "name": "Smartwatch Pulse", "qty": 1, "price": 299.0},
          {"sku": "NS-001", "name": "Aura Bluetooth Headphones", "qty": 1, "price": 249.0}]),
    (18, [{"sku": "NS-007", "name": "Soundbar Cinema 380", "qty": 1, "price": 399.0}]),
    (5,  [{"sku": "NS-004", "name": "Gourmet Coffee Kit", "qty": 1, "price": 129.0},
          {"sku": "NS-012", "name": "Smart Lumen Lamp", "qty": 1, "price": 199.0}]),
]


def seed_demo_user() -> None:
    """Idempotent: creates DEMO test user + example orders (GOLD tier),
    if not already present. Runs at boot (api.py). Does nothing if email exists —
    safe to restart. DO NOT use in production (public credentials — DT-010)."""
    existing = get_user_by_email(DEMO_EMAIL)
    if existing:
        update_address(existing["id"], _DEMO_ADDRESS)
        return
    user = register(DEMO_NAME, DEMO_EMAIL, DEMO_PASSWORD)
    update_address(user["id"], _DEMO_ADDRESS)  # saved address in profile (F-011)
    customer = _demo_customer()
    for days_ago, items in _DEMO_ORDERS:
        total = sum(i["qty"] * i["price"] for i in items)
        created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        _create_order(items, customer, total, status="PAID", user_id=user["id"], created_at=created)


# --- OWNER user (owner; LLM config gate — F-020) ---------------------
# Only OWNER role in app: accesses LLM config screen/endpoints. Password via env
# OWNER_PASSWORD (DEMO default — change on any deploy; see DT-012). Idempotent:
# ensures account AND role at boot (if account exists, just promotes to OWNER).
OWNER_EMAIL = settings.owner_email
OWNER_PASSWORD = settings.owner_password  # DEMO default — DT-012
OWNER_NAME = settings.owner_name


def seed_owner_user() -> None:
    """Ensures OWNER user at boot. Creates if not present (password = OWNER_PASSWORD) and
    always ensures `role=OWNER`. Does not overwrite password of existing account."""
    existing = get_user_by_email(OWNER_EMAIL)
    if existing is None:
        user = register(OWNER_NAME, OWNER_EMAIL, OWNER_PASSWORD)
        update_role(user["id"], "OWNER")
        update_address(user["id"], _DEMO_ADDRESS)
    else:
        if existing.get("role") != "OWNER":
            update_role(existing["id"], "OWNER")
        update_address(existing["id"], _DEMO_ADDRESS)
