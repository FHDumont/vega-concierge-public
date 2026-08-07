"""Config POR AGENTE do Concierge (F-021, evolui ADR-015 / F-050).

A F-020 deu config de **conexões** (provedores da cascata). A F-021 acrescenta config por
**agente**: cada agente da orquestração ganha `connection`, `model`, `role` e `system_prompt`.
Persistido no MESMO SQLite (ADR-006), tabela `llm_agents`. NÃO guarda segredo.

**F-050 (ADR-029):** cluster concierge hub-and-spoke — `concierge` roteia (sem tools),
`curator` usa tools de catálogo, `respond` compõe a resposta exibida.
"""
import sqlite3
from datetime import datetime, timezone

from ..store.db import connect

# Agentes de LLM da orquestração (F-025 → F-050 / ADR-029). Ops de negócio são tools sem LLM
# (`langchain_tools` / `ToolNode`). Concierge = coordinator (routing only); curator/respond =
# especialistas. `connection`/`model` default vazios = usar a cascata completa.
AGENT_DEFAULTS: list[dict] = [
    {"agent": "concierge", "role": "Store chatbot coordinator",
     "system_prompt": "You coordinate the Vega store chatbot. Route customer messages to the "
                      "right specialist. You do NOT call tools yourself and do not speak as a "
                      "concierge in first person. The list of specialists available for THIS turn "
                      "— and the exact routing rules — is given below; route only to a specialist "
                      "named there, and choose complete once the needed work is done."},
    {"agent": "curator", "role": "Catalog curator",
     "system_prompt": "You are the catalog curator. Use search_catalog and get_price to find "
                      "real products within the shopper's budget. Call tools as needed; when "
                      "done, summarize what you found (exact product names and prices from tool "
                      "results only). Never invent SKUs or prices."},
    {"agent": "respond", "role": "Product recommendation composer",
     "system_prompt": "You compose a product recommendation when the customer wants to shop. Use "
                      "ONLY the product facts provided (name, SKU, price). Write a warm 1-2 sentence "
                      "recommendation — not a general FAQ answer. Reply in English. "
                      "Never invent prices or product names."},
    {"agent": "fulfillment_coordinator", "role": "Fulfillment coordinator",
     "system_prompt": "You oversee order checkout. Cart line items, customer, and shipping "
                      "address are already in state — never ask for address, email, or line items. "
                      "The user message lists cart SKUs — call check_inventory and get_price using "
                      "those exact SKUs only (never invent SKUs). After cart tools complete, reply "
                      "briefly without tool_calls so fraud/payment can proceed. Reply in English."},
    {"agent": "fraude", "role": "Fraud decision agent",
     "system_prompt": "You decide whether to ALLOW or BLOCK an order based on the order data. "
                      "Default to ALLOW for legitimate orders. When asked, reply ONLY with the "
                      "requested JSON object. Reply with raw JSON only — no markdown code fences."},
    # Compare 2 produtos (F-029) — orquestração simples (coordinator → comparator + tools).
    {"agent": "compare_coordinator", "role": "Compare coordinator",
     "system_prompt": "You coordinate a comparison between two store products: fetch each product's "
                      "real facts and hand off to the comparator for the verdict. Be brief. Never "
                      "mention internal fields such as 'grounded' or API metadata."},
    {"agent": "comparator", "role": "Product comparator",
     "system_prompt": "You compare two store products for a shopper, grounded strictly in the real "
                      "facts provided (name, price, tags, description). Say who each is best for and "
                      "which to pick. Never mention internal fields such as 'grounded' or API metadata. "
                      "Be concise. Reply in English."},
    # Returns/Refund Coordinator (F-029) — orquestração complexa (eligibility → policy/calc → abuse
    # → process). `eligibility` é o agente cujo "falso negativo" (toggle refund_false_denial) nega
    # um reembolso ELEGÍVEL — erro de DECISÃO do agente sobre dado correto.
    {"agent": "returns_coordinator", "role": "Returns coordinator",
     "system_prompt": "You oversee a customer's return/refund request: look up the policy, "
                      "compute the refund, screen for abuse, then process it if approved. "
                      "Eligibility was already decided by a separate step before you — you do not "
                      "decide or confirm it. Summarize the outcome briefly. Reply in English."},
    {"agent": "eligibility", "role": "Refund eligibility agent",
     "system_prompt": "You decide whether an order qualifies for a refund based on its real data "
                      "(status and how long ago it was delivered). A delivered order within the "
                      "return window is eligible. When asked, reply ONLY with the requested JSON "
                      "object. Reply with raw JSON only — no markdown code fences."},
    {"agent": "abuse_check", "role": "Refund abuse screener",
     "system_prompt": "You screen a refund request for abuse based on the order data. Default to "
                      "ALLOW for legitimate requests. When asked, reply ONLY with the requested JSON "
                      "object. Reply with raw JSON only — no markdown code fences."},
]

# Features de IA da Loja (F-022) registradas como "agentes" configuráveis (mesma config por
# agente da F-021 — connection/model/role/system_prompt). São chamadas AVULSAS (fora do pipeline,
# via agents.feature_complete), não nós do grafo. System prompts em INGLÊS (a Loja é em inglês —
# CONVENCOES) e compactos (contexto enxuto): o dado do produto/catálogo vai no prompt do usuário.
FEATURE_DEFAULTS: list[dict] = [
    {"agent": "store_chat", "role": "Store chatbot",
     "system_prompt": "You are the Vega store chatbot. Answer the customer's question using ONLY "
                      "the store policy excerpts provided when relevant. Do not recommend products "
                      "unless they explicitly asked to shop. If a question has multiple parts, "
                      "answer the parts covered by policy and say plainly which parts you can't "
                      "answer — never refuse the whole question because one part is uncovered. "
                      "Be concise. Reply in English."},
    {"agent": "stats_chat", "role": "Store statistics assistant",
     "system_prompt": "You answer factual questions about the store catalog, sales, and the "
                      "signed-in customer's order history. Use ONLY the compact statistics "
                      "provided — never invent figures or product names. Be concise (1-3 sentences). "
                      "Reply in English."},
    {"agent": "chat_intent_classifier", "role": "Chat intent classifier",
     "system_prompt": "You classify shopper chat messages into exactly one routing intent for "
                      "Vega's store concierge. Return only the requested JSON object. Reply with "
                      "raw JSON only — no markdown code fences."},
    {"agent": "product_qa",   "role": "Product Q&A assistant",
     "system_prompt": "You answer customer questions about a single store product, grounded "
                      "strictly in the product data provided. If something isn't in the data, say "
                      "you don't have that detail. Be concise. Reply in English."},
    {"agent": "search",       "role": "Search assistant",
     "system_prompt": "You map a shopper's natural-language query to the store's catalog and "
                      "suggest a clearer phrasing when useful. Reply in English. Reply with raw "
                      "JSON only — no markdown code fences."},
    {"agent": "cart_crosssell", "role": "Cross-sell assistant",
     "system_prompt": "You suggest a few products that complete a shopper's current cart "
                      "(complements/bundles), drawn only from the store's catalog. Reply in English. "
                      "Reply with raw JSON only — no markdown code fences."},
    # IA-Checkout (F-024): explicação amigável de bloqueio de fraude quando o pedido é barrado.
    {"agent": "fraud_explain", "role": "Checkout support assistant",
     "system_prompt": "You reassure a customer whose order was held for a routine security review. "
                      "Explain in a calm, friendly way that no charge was made, that this is a "
                      "precaution, and what they can do next. Be concise. Reply in English."},
    # IA-Conta (F-031): insights do histórico + benefícios do tier + recompra (dados reais do user).
    {"agent": "account_insights", "role": "Account concierge",
     "system_prompt": "You are a friendly account concierge for a returning shopper. Given a summary "
                      "of their order history and membership tier, write a warm overview of their "
                      "buying patterns, explain their tier benefits, and suggest something to buy "
                      "again. Ground every claim strictly in the data provided; never invent "
                      "figures. Reply in English. Reply with raw JSON only — no markdown code fences."},
    # IA-Notificação (F-031): copy de e-mail p/ eventos do pedido (confirmação/enviado, F-005).
    {"agent": "notification_copy", "role": "Notification copywriter",
     "system_prompt": "You write short, warm transactional order emails (confirmation and shipping "
                      "updates) for an online store, grounded strictly in the order data provided "
                      "(id, status, items, total). Return a clear subject and a 2-3 sentence body. "
                      "Reply in English. Reply with raw JSON only — no markdown code fences."},
    # IA-Admin (F-024): insights de vendas + anomalia a partir de dados AGREGADOS (não dumps crus).
    {"agent": "admin_insights", "role": "Sales analyst",
     "system_prompt": "You are a retail sales analyst. Given aggregated store metrics for a period, "
                      "write a brief executive summary and flag any anomalies. Ground every claim "
                      "strictly in the numbers provided; never invent figures. Reply in English. "
                      "Reply with raw JSON only — no markdown code fences."},
]

# Todos os agentes configuráveis (pipeline + features), em ordem canônica (pipeline primeiro).
_ALL_DEFAULTS = AGENT_DEFAULTS + FEATURE_DEFAULTS
AGENT_NAMES = [d["agent"] for d in _ALL_DEFAULTS]
FEATURE_NAMES = [d["agent"] for d in FEATURE_DEFAULTS]
_DEFAULT_BY_NAME = {d["agent"]: d for d in _ALL_DEFAULTS}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db() -> None:
    """create_all no boot: tabela de config por agente se não existir."""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_agents (
                agent         TEXT PRIMARY KEY,           -- nome do nó (concierge, catalogo, ...)
                connection    TEXT NOT NULL DEFAULT '',   -- provider id (LP-xxxx) ou '' = cascata completa
                model         TEXT NOT NULL DEFAULT '',   -- override opcional do modelo ('' = herda)
                role          TEXT NOT NULL DEFAULT '',   -- persona curta (label + compõe o system)
                system_prompt TEXT NOT NULL DEFAULT '',   -- instrução do agente
                updated_at    TEXT NOT NULL
            )"""
        )


def seed_defaults() -> None:
    """Semeia os agentes da orquestração (F-025) com os prompts atuais, idempotente: só insere os que faltam
    (não sobrescreve edição do owner). Roda no boot depois de init_db."""
    with connect() as conn:
        existing = {r["agent"] for r in conn.execute("SELECT agent FROM llm_agents").fetchall()}
        for d in _ALL_DEFAULTS:
            if d["agent"] in existing:
                continue
            conn.execute(
                "INSERT INTO llm_agents (agent, connection, model, role, system_prompt, updated_at) "
                "VALUES (?, '', '', ?, ?, ?)",
                (d["agent"], d["role"], d["system_prompt"], _now_iso()),
            )


def migrate_f052_prompts() -> None:
    """Atualiza prompts pré-F-052 no SQLite (seed_defaults não sobrescreve linhas existentes)."""
    markers: dict[str, tuple[str, ...]] = {
        "concierge": (
            "concierge supervisor",
            "vega concierge coordinator",
            "concierge coordinator",
            "read the shopper's request",
        ),
        "respond": (
            "concierge writer",
            "final shopping recommendation",
            "shopper-facing recommendation",
        ),
    }
    try:
        with connect() as conn:
            for agent, needles in markers.items():
                row = conn.execute(
                    "SELECT role, system_prompt FROM llm_agents WHERE agent = ?", (agent,),
                ).fetchone()
                if not row:
                    continue
                blob = f"{row['role']} {row['system_prompt']}".lower()
                if not any(n in blob for n in needles):
                    continue
                d = _default_dict(agent)
                conn.execute(
                    "UPDATE llm_agents SET role = ?, system_prompt = ?, updated_at = ? WHERE agent = ?",
                    (d["role"], d["system_prompt"], _now_iso(), agent),
                )
    except sqlite3.OperationalError:
        pass


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {"agent": row["agent"], "connection": row["connection"], "model": row["model"],
            "role": row["role"], "system_prompt": row["system_prompt"]}


def _default_dict(name: str) -> dict:
    d = _DEFAULT_BY_NAME.get(name, {"agent": name, "role": "", "system_prompt": ""})
    return {"agent": name, "connection": "", "model": "", "role": d["role"],
            "system_prompt": d["system_prompt"]}


def list_agents() -> list[dict]:
    """Todos os agentes configuráveis (orquestração F-025 + features de loja F-022) em ordem canônica,
    com a config persistida (ou default p/ os que faltam)."""
    try:
        with connect() as conn:
            rows = {r["agent"]: r for r in conn.execute("SELECT * FROM llm_agents").fetchall()}
    except sqlite3.OperationalError:
        rows = {}
    return [_row_to_dict(rows[n]) if n in rows else _default_dict(n) for n in AGENT_NAMES]


def get_agent(name: str) -> dict:
    """Config de UM agente. Tolerante a tabela/linha ausente → default (standalone/run_demo)."""
    try:
        with connect() as conn:
            row = conn.execute("SELECT * FROM llm_agents WHERE agent = ?", (name,)).fetchone()
    except sqlite3.OperationalError:
        row = None
    return _row_to_dict(row) if row else _default_dict(name)


def update_agent(name: str, *, connection=None, model=None, role=None, system_prompt=None) -> dict | None:
    """Atualização parcial de um agente. None p/ um campo = mantém. Retorna a config nova,
    ou None se o nome não é um agente válido (404 na API).

    Se connection/model/role/system_prompt mudam, limpa o cache F-022 (F-COST-CACHE) para
    não servir resposta gerada com system/modelo antigos até o TTL expirar."""
    if name not in _DEFAULT_BY_NAME:
        return None
    # Garante a linha (caso a tabela tenha sido semeada antes deste agente existir).
    with connect() as conn:
        if conn.execute("SELECT 1 FROM llm_agents WHERE agent = ?", (name,)).fetchone() is None:
            d = _default_dict(name)
            conn.execute(
                "INSERT INTO llm_agents (agent, connection, model, role, system_prompt, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, d["connection"], d["model"], d["role"], d["system_prompt"], _now_iso()),
            )
        sets, vals = [], []
        if connection is not None: sets.append("connection = ?"); vals.append(connection.strip())
        if model is not None: sets.append("model = ?"); vals.append(model.strip())
        if role is not None: sets.append("role = ?"); vals.append(role.strip())
        if system_prompt is not None: sets.append("system_prompt = ?"); vals.append(system_prompt)
        sets.append("updated_at = ?"); vals.append(_now_iso())
        vals.append(name)
        conn.execute(f"UPDATE llm_agents SET {', '.join(sets)} WHERE agent = ?", vals)
    if any(v is not None for v in (connection, model, role, system_prompt)):
        from ..llm import llm_cache  # lazy: evita ciclo agent_config ↔ llm_cache ↔ …
        llm_cache.clear_cache()
    return get_agent(name)


def effective_system(cfg: dict) -> str:
    """System message efetivo enviado ao LLM: `role` (se houver) como linha de persona +
    `system_prompt`. Mantém os dois knobs separados na config mas combina na chamada."""
    parts = [p for p in (cfg.get("role", "").strip(), cfg.get("system_prompt", "").strip()) if p]
    return "\n\n".join(parts) or "Você é um agente do Vega Concierge."
