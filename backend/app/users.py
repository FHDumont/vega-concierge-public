"""Contas de usuário + auth de DEMO + tiers (F-008, ADR-011).

Decisões (demo, sem segurança de produção — ver DT-010):
- **Senha:** PBKDF2-HMAC-SHA256 (stdlib `hashlib`, sem nova dependência). Não é
  bcrypt/argon2, mas evita texto-plano. Guardada como `pbkdf2_sha256$iter$salt$hash`.
- **Sessão:** token opaco (`secrets.token_urlsafe`) enviado em `Authorization: Bearer`.
  Mapa token→user_id vive EM MEMÓRIA (reseta no restart, como o estoque — DT-007/DT-010).
  Escolhido em vez de cookie httpOnly porque o CORS é `allow_origins=["*"]`, incompatível
  com cookies de credencial; token em header é mais simples e standalone-first (ADR-011).
- **Tier:** computado pelo GASTO ACUMULADO do usuário (pedidos PAID/SHIPPED/DELIVERED) e
  materializado de forma lazy na coluna `tier` (espelha o padrão do ciclo de vida — ADR-008).
  Thresholds simples e configuráveis por env.

Usuários persistem em SQLite (mesmo arquivo dos pedidos — ADR-006).
"""
import hashlib
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

from .orders import DB_PATH  # mesmo arquivo SQLite dos pedidos (ADR-006)
from .orders import create_order as _create_order  # seed do usuário de teste (F-010)

# Thresholds de tier por gasto acumulado (BRL). Configuráveis por env; valores simples p/ demo.
GOLD_THRESHOLD = float(os.getenv("TIER_GOLD_USD", "1000"))
PLATINUM_THRESHOLD = float(os.getenv("TIER_PLATINUM_USD", "5000"))

PBKDF2_ITERATIONS = int(os.getenv("AUTH_PBKDF2_ITERATIONS", "120000"))

# token → user_id (sessões de demo, em memória; resetam no restart — DT-010).
_SESSIONS: dict[str, str] = {}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """create_all no boot: tabela de usuários se não existir."""
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                email       TEXT NOT NULL UNIQUE,
                password    TEXT NOT NULL,   -- pbkdf2_sha256$iter$salt_hex$hash_hex
                tier        TEXT NOT NULL DEFAULT 'STANDARD',  -- STANDARD | GOLD | PLATINUM
                role        TEXT NOT NULL DEFAULT 'STANDARD',  -- STANDARD | OWNER (gate da config — F-020)
                address     TEXT NOT NULL DEFAULT '',  -- endereço salvo (F-011); pré-preenche o checkout
                created_at  TEXT NOT NULL    -- ISO-8601 UTC
            )"""
        )
        # Migrações aditivas: `address` (F-011), `role` (F-020) em tabelas pré-existentes.
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
        if "address" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN address TEXT NOT NULL DEFAULT ''")
        if "role" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'STANDARD'")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return "USR-" + uuid.uuid4().hex[:8].upper()


# --- senha (PBKDF2, stdlib) -------------------------------------------------

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
    """Tier a partir do gasto acumulado. Thresholds configuráveis (env)."""
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
    """Payload de API: recomputa o tier pelo gasto (não expõe a senha).
    `address` (F-011) é o endereço salvo que pré-preenche o checkout.
    `role` (F-020) é STANDARD|OWNER — o front esconde a tela de config p/ não-owner."""
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
    """Cria um usuário. Levanta ValueError se o e-mail já existe."""
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
        with _connect() as conn:
            conn.execute(
                "INSERT INTO users (id, name, email, password, tier, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], user["name"], user["email"], user["password"], user["tier"], user["created_at"]),
            )
    except sqlite3.IntegrityError:
        raise ValueError("email already registered")
    return _row_to_user_dict(user)


def _row_to_user_dict(user: dict) -> dict:
    """Versão sem a senha (para uso interno após criar)."""
    return {k: user[k] for k in ("id", "name", "email", "tier", "created_at")}


def get_user(user_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    return _row_to_user(row) if row else None


def authenticate(email: str, password: str) -> dict | None:
    """Verifica e-mail+senha; retorna o usuário (sem senha) ou None."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if row is None or not _verify_password(password, row["password"]):
        return None
    return _row_to_user(row)


def update_tier(user_id: str, tier: str) -> None:
    """Materializa o tier computado na coluna (lazy, no /me)."""
    with _connect() as conn:
        conn.execute("UPDATE users SET tier = ? WHERE id = ?", (tier, user_id))


def update_address(user_id: str, address: str) -> None:
    """Salva/edita o endereço do usuário no perfil (F-011)."""
    with _connect() as conn:
        conn.execute("UPDATE users SET address = ? WHERE id = ?", (address.strip(), user_id))


# --- papel OWNER (gate da config de LLM — F-020) ----------------------------

def update_role(user_id: str, role: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def is_owner(user_id: str) -> bool:
    """True se o usuário tem papel OWNER (acesso à tela/endpoints de config de LLM)."""
    user = get_user(user_id)
    return bool(user and user.get("role") == "OWNER")


# --- sessões (em memória) ---------------------------------------------------

def create_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = user_id
    return token


def session_user_id(token: str) -> str | None:
    return _SESSIONS.get(token)


def drop_session(token: str) -> None:
    _SESSIONS.pop(token, None)


# --- usuário de teste (seed de DEMO — F-010) --------------------------------
# Conta padrão para facilitar a validação no workshop. Credenciais FIXAS e
# conhecidas → INSEGURO por design (só a camada de demo; ver DT-010). Os pedidos
# de exemplo somam ~$1,275 → tier GOLD (≥$1,000) com histórico não-vazio.
DEMO_EMAIL = "demo@vega.test"
DEMO_PASSWORD = "demo1234"
DEMO_NAME = "Demo User"
_DEMO_ADDRESS = "221B Demo Street, Test City"
# Dados de pagamento fictícios (workshop UC-5 — DT-010; nunca usar em produção).
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

# (dias atrás, itens). Datados no passado → o ciclo de vida (ADR-008) materializa
# SHIPPED/DELIVERED na 1ª leitura, dando um histórico realista. Itens são snapshots
# (espelham SKUs do catálogo; pedidos guardam o item, não referenciam o catálogo vivo).
_DEMO_ORDERS = [
    (40, [{"sku": "NS-002", "name": "Smartwatch Pulse", "qty": 1, "price": 299.0},
          {"sku": "NS-001", "name": "Aura Bluetooth Headphones", "qty": 1, "price": 249.0}]),
    (18, [{"sku": "NS-007", "name": "Soundbar Cinema 380", "qty": 1, "price": 399.0}]),
    (5,  [{"sku": "NS-004", "name": "Gourmet Coffee Kit", "qty": 1, "price": 129.0},
          {"sku": "NS-012", "name": "Smart Lumen Lamp", "qty": 1, "price": 199.0}]),
]


def seed_demo_user() -> None:
    """Idempotente: cria o usuário de teste de DEMO + pedidos de exemplo (tier GOLD),
    se ainda não existir. Roda no boot (api.py). Não faz nada se o e-mail já existe —
    seguro para reiniciar. NÃO usar em produção (credenciais públicas — DT-010)."""
    existing = get_user_by_email(DEMO_EMAIL)
    if existing:
        update_address(existing["id"], _DEMO_ADDRESS)
        return
    user = register(DEMO_NAME, DEMO_EMAIL, DEMO_PASSWORD)
    update_address(user["id"], _DEMO_ADDRESS)  # endereço salvo no perfil (F-011)
    customer = _demo_customer()
    for days_ago, items in _DEMO_ORDERS:
        total = sum(i["qty"] * i["price"] for i in items)
        created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        _create_order(items, customer, total, status="PAID", user_id=user["id"], created_at=created)


# --- usuário OWNER (dono; gate da config de LLM — F-020) ---------------------
# Único papel OWNER da app: acessa a tela/endpoints de config de LLM. Senha via env
# OWNER_PASSWORD (default de DEMO — trocar em qualquer deploy; ver DT-012). Idempotente:
# garante a conta E o papel no boot (se a conta já existir, só promove a OWNER).
OWNER_EMAIL = os.getenv("OWNER_EMAIL", "fernando@fernando.com.br")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "owner1234")  # default DEMO — DT-012
OWNER_NAME = os.getenv("OWNER_NAME", "Fernando (Owner)")


def seed_owner_user() -> None:
    """Garante o usuário OWNER no boot. Cria se não existir (senha = OWNER_PASSWORD) e
    sempre garante `role=OWNER`. Não reescreve a senha de uma conta já existente."""
    existing = get_user_by_email(OWNER_EMAIL)
    if existing is None:
        user = register(OWNER_NAME, OWNER_EMAIL, OWNER_PASSWORD)
        update_role(user["id"], "OWNER")
        update_address(user["id"], _DEMO_ADDRESS)
    else:
        if existing.get("role") != "OWNER":
            update_role(existing["id"], "OWNER")
        update_address(existing["id"], _DEMO_ADDRESS)
