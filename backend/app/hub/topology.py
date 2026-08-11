"""Agent orchestration topology (F-027) — derived from real graph (agents.py, ADR-029).

Exposes NODES (agents/tools/deps) and EDGES (who calls whom) for owner's **visual editor**.
Diagram flattens finalize and uses hub-and-spoke layout in concierge cluster (return edges curator/respond → coordinator).

- **Concierge cluster** (recommendation): `concierge.workflow → agent.concierge (coordinator)` →
  `{agent.curator → tool.search_catalog/tool.get_price, agent.respond}`.
- **Fulfillment cluster** (checkout): `agent.fulfillment_coordinator →
  {tool.check_inventory, tool.get_price, agent.fraude, payment.charge, notify.send_email}`.
- **Standalone** (direct call, no orchestration): Store AI features (F-022/F-024) —
  product_qa, search, cart_crosssell,
  fraud_explain, admin_insights.

No second source of truth: structure is fixed (mirrors code) and roles (`role`) and
configurable agents list come from `agent_config` (same per-agent config as F-021), for
editor to open/edit config on agent click. Tools/deps are leaves (not configurable).
"""
from . import agent_config

# Configurable agent nodes in orchestrated clusters → clickable (open F-021 config).
# Tools (LLM-less) and external deps are informative leaves (not clickable for config).
_TOOL_LABELS = {
    "tool.search_catalog": "search_catalog",
    "tool.get_price": "get_price",
    "tool.check_inventory": "check_inventory",
    # Returns/Refund (F-029) — LLM-less tools in Returns Coordinator chain.
    "tool.policy_lookup": "policy_lookup",
    "tool.refund_calc": "refund_calc",
    "tool.process_refund": "process_refund",
}
_DEP_LABELS = {
    "payment.charge": "payment gateway",
    "notify.send_email": "email provider",
}

# Orchestrated clusters: (id, label, root, edges parent→child). Agents reference
# real names from `agent_config`; tools/deps use identifiers from store/tools.py and
# store/payments.py.
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
            ("concierge", "product_qa"),
            ("concierge", "returns"),
            ("curator", "concierge"),
            ("respond", "concierge"),
            ("compare", "concierge"),
            ("search", "concierge"),
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
    """Node of configurable agent (clickable): fetches real `role` from per-agent config (F-021)."""
    cfg = agent_config.get_agent(name)
    return {"id": name, "kind": "agent", "agent": name, "role": cfg["role"], "label": name}


def _leaf_node(node_id: str) -> dict:
    """Non-configurable leaf node: tool (LLM-less) or external dep."""
    if node_id in _TOOL_LABELS:
        return {"id": node_id, "kind": "tool", "agent": None, "role": "", "label": _TOOL_LABELS[node_id]}
    return {"id": node_id, "kind": "dep", "agent": None, "role": "", "label": _DEP_LABELS.get(node_id, node_id)}


def _build_cluster(spec: dict) -> dict:
    """Builds nodes (deduplicated, in order of appearance) + edges of a cluster."""
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
    """Complete topology for editor: orchestrated clusters + standalone agents (features)."""
    clusters = [_build_cluster(c) for c in _CLUSTERS]
    standalone = [_agent_node(name) for name in agent_config.FEATURE_NAMES]
    return {"clusters": clusters, "standalone": standalone}
