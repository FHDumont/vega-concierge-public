"""Topologia da orquestração de agentes (F-027) — derivada do grafo real (agents.py, ADR-029).

Expõe NÓS (agentes/tools/deps) e ARESTAS (quem chama quem) p/ o **editor visual** do owner.
O diagrama achata finalize e usa layout hub-and-spoke no cluster concierge (arestas de retorno curator/respond → coordinator).

- **Cluster concierge** (recomendação): `concierge.workflow → agent.concierge (coordinator)` →
  `{agent.curator → tool.search_catalog/tool.get_price, agent.respond}`.
- **Cluster fulfillment** (fechamento): `agent.fulfillment_coordinator →
  {tool.check_inventory, tool.get_price, agent.fraude, payment.charge, notify.send_email}`.
- **Standalone** (chamada direta, sem orquestração): as features de IA da Loja (F-022/F-024) —
  product_qa, product_desc, search, home_picks, cart_crosssell, order_status, gift_message,
  fraud_explain, admin_insights.

Não há um 2º modelo de verdade: a estrutura é fixa (espelha o código) e os papéis (`role`) e a
lista de agentes configuráveis vêm de `agent_config` (a mesma config por-agente da F-021), p/ o
editor abrir/editar a config ao clicar num agente. Tools/deps são folhas (não configuráveis).
"""
from . import agent_config

# Nós-agente configuráveis dos clusters orquestrados → clicáveis (abrem a config F-021).
# Tools (sem LLM) e deps externas são folhas informativas (não clicáveis p/ config).
_TOOL_LABELS = {
    "tool.search_catalog": "search_catalog",
    "tool.get_price": "get_price",
    "tool.check_inventory": "check_inventory",
    # Returns/Refund (F-029) — tools sem LLM da cadeia do Returns Coordinator.
    "tool.policy_lookup": "policy_lookup",
    "tool.refund_calc": "refund_calc",
    "tool.process_refund": "process_refund",
}
_DEP_LABELS = {
    "payment.charge": "payment gateway",
    "notify.send_email": "email provider",
}

# Clusters orquestrados: (id, label, root, arestas pai→filho). Os agentes referenciam nomes
# reais de `agent_config`; as tools/deps usam os identificadores de tools.py/payments.py.
_CLUSTERS = [
    {
        "id": "concierge",
        "label": "Concierge — hub-and-spoke (coordinator → curator / respond / chat spokes)",
        "root": {"id": "concierge.workflow", "kind": "workflow", "label": "concierge.workflow"},
        "edges": [
            ("concierge.workflow", "concierge"),
            ("concierge", "curator"),
            ("concierge", "respond"),
            ("concierge", "compare"),
            ("concierge", "search"),
            ("concierge", "gift"),
            ("concierge", "product_qa"),
            ("concierge", "returns"),
            ("curator", "concierge"),
            ("respond", "concierge"),
            ("compare", "concierge"),
            ("search", "concierge"),
            ("gift", "concierge"),
            ("product_qa", "concierge"),
            ("returns", "concierge"),
            ("curator", "tool.search_catalog"),
            ("curator", "tool.get_price"),
            ("compare", "compare_coordinator"),
        ],
    },
    {
        "id": "compare",
        "label": "Compare — two products",
        "root": {"id": "compare.workflow", "kind": "workflow", "label": "compare.workflow"},
        "edges": [
            ("compare.workflow", "compare_coordinator"),
            ("compare_coordinator", "tool.get_price"),
            ("compare_coordinator", "comparator"),
        ],
    },
    {
        "id": "fulfillment",
        "label": "Checkout — fulfillment",
        "root": {"id": "fulfillment.workflow", "kind": "workflow", "label": "fulfillment.workflow"},
        "edges": [
            ("fulfillment.workflow", "fulfillment_coordinator"),
            ("fulfillment_coordinator", "tool.check_inventory"),
            ("fulfillment_coordinator", "tool.get_price"),
            ("fulfillment_coordinator", "fraude"),
            ("fulfillment_coordinator", "payment.charge"),
            ("fulfillment_coordinator", "notify.send_email"),
        ],
    },
    {
        "id": "returns",
        "label": "Returns — refund coordinator",
        "root": {"id": "returns.workflow", "kind": "workflow", "label": "returns.workflow"},
        "edges": [
            ("returns.workflow", "returns_coordinator"),
            ("returns_coordinator", "eligibility"),
            ("returns_coordinator", "tool.policy_lookup"),
            ("returns_coordinator", "tool.refund_calc"),
            ("returns_coordinator", "abuse_check"),
            ("returns_coordinator", "tool.process_refund"),
        ],
    },
]


def _agent_node(name: str) -> dict:
    """Nó de um agente configurável (clicável): traz o `role` real da config por-agente (F-021)."""
    cfg = agent_config.get_agent(name)
    return {"id": name, "kind": "agent", "agent": name, "role": cfg["role"], "label": name}


def _leaf_node(node_id: str) -> dict:
    """Nó folha não-configurável: tool (sem LLM) ou dep externa."""
    if node_id in _TOOL_LABELS:
        return {"id": node_id, "kind": "tool", "agent": None, "role": "", "label": _TOOL_LABELS[node_id]}
    return {"id": node_id, "kind": "dep", "agent": None, "role": "", "label": _DEP_LABELS.get(node_id, node_id)}


def _build_cluster(spec: dict) -> dict:
    """Monta nós (deduplicados, na ordem em que aparecem) + arestas de um cluster."""
    edges = [{"from": a, "to": b} for a, b in spec["edges"]]
    order: list[str] = []
    for a, b in spec["edges"]:
        for nid in (a, b):
            if nid not in order:
                order.append(nid)
    root = spec["root"]
    nodes = []
    for nid in order:
        if nid == root["id"]:
            nodes.append({**root, "agent": None, "role": ""} if root["kind"] == "workflow"
                         else _agent_node(nid))
        elif nid.startswith("tool.") or nid in _DEP_LABELS:
            nodes.append(_leaf_node(nid))
        else:
            nodes.append(_agent_node(nid))
    return {"id": spec["id"], "label": spec["label"], "kind": "orchestrated",
            "root": root["id"], "nodes": nodes, "edges": edges}


def build() -> dict:
    """Topologia completa p/ o editor: clusters orquestrados + agentes standalone (features)."""
    clusters = [_build_cluster(c) for c in _CLUSTERS]
    standalone = [_agent_node(name) for name in agent_config.FEATURE_NAMES]
    return {"clusters": clusters, "standalone": standalone}
