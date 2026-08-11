"""OWNER-only config — LLM cascade, agents, local/hub source, flags, RUM, and Inspector."""
from fastapi import APIRouter, Header, HTTPException
from ..hub import agent_config, feature_flags, hub, hub_settings, rum, topology
from ..llm import llm, llm_providers, llm_activity, llm_config
from ..schemas import AgentTestIn, AgentUpdate, FlagsIn, HubSourceIn, InspectorToggle, ProviderIn, ProviderUpdate, ReorderIn, RumIn, TestProviderIn
from ._common import _require_owner

# No `prefix`: each route carries the full path, just like it was in `api.py`.
router = APIRouter()


# --- LLM Config (OWNER-only — F-020, ADR-015) ----------------------------
# Manages cascade providers (order/enable/model/key). Different from rest of
# Admin (no auth, workshop controls), these endpoints are GATED to OWNER: the config
# guards SECRETS (keys). The API only returns the MASKED version (no `api_key`).

@router.get("/api/admin/config/llm-types")
def config_llm_types(authorization: str | None = Header(default=None)):
    # Catalog of Type presets (base_url + suggested economical models) for the connection UI
    # (F-021). No secrets — gated to OWNER only for config namespace consistency.
    _require_owner(authorization)
    return llm_providers.list_type_presets()


@router.get("/api/admin/config/providers")
def config_list(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return llm_config.list_providers()


@router.post("/api/admin/config/providers")
def config_create(p: ProviderIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return llm_config.create_provider(p.name, p.kind, p.base_url, p.model, p.api_key, p.enabled)


@router.put("/api/admin/config/providers/{provider_id}")
def config_update(provider_id: str, p: ProviderUpdate, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    updated = llm_config.update_provider(provider_id, **p.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return updated


@router.delete("/api/admin/config/providers/{provider_id}")
def config_delete(provider_id: str, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    if not llm_config.delete_provider(provider_id):
        raise HTTPException(status_code=404, detail="provider not found")
    return {"deleted": provider_id}


@router.post("/api/admin/config/providers/reorder")
def config_reorder(body: ReorderIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return llm_config.reorder(body.ids)


@router.post("/api/admin/config/providers/{provider_id}/test")
def config_test(provider_id: str, body: TestProviderIn, authorization: str | None = Header(default=None)):
    # Makes ONE test call and returns ok/error/latency (without leaking the key). Uses the saved provider
    # (with the key stored) merged with fields edited in the UI; if the UI sends a new
    # key it uses it, otherwise keeps the saved one — so the owner tests edits before saving.
    _require_owner(authorization)
    stored = llm_config.get_provider_with_key(provider_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="provider not found")
    edits = body.model_dump(exclude_none=True)
    cfg = {**stored, **{k: v for k, v in edits.items() if k != "api_key" or v}}
    return llm.test_provider(cfg)


# --- Agent config (OWNER-only — F-021; + store AI features F-022) --------
# The 6 Concierge agents + the store AI features (product_qa/search/cart_crosssell),
# each with connection/model/role/system_prompt. No secrets (goes raw to front), but gated
# to OWNER for config namespace consistency.

@router.get("/api/admin/agents/topology")
def config_agents_topology(authorization: str | None = Header(default=None)):
    # Visual editor (F-027): orchestration topology (clusters + standalone) derived from
    # the real graph (agents.py, ADR-018). Owner-only — clicking an agent opens/edits config (F-021).
    _require_owner(authorization)
    return topology.build()


@router.get("/api/admin/config/agents")
def config_agents(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return agent_config.list_agents()


@router.put("/api/admin/config/agents/{name}")
def config_agent_update(name: str, p: AgentUpdate, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    updated = agent_config.update_agent(name, **p.model_dump(exclude_none=True))
    if updated is None:
        raise HTTPException(status_code=404, detail="agent not found")
    return updated


@router.post("/api/admin/config/agents/{name}/test")
def config_agent_test(name: str, p: AgentTestIn, authorization: str | None = Header(default=None)):
    # Resolves agent LLM (saved + UI edits) and makes ONE real call with the effective
    # system (role + system_prompt). Shows real provider/model/tokens (stub if it fails).
    _require_owner(authorization)
    if name not in agent_config.AGENT_NAMES:
        raise HTTPException(status_code=404, detail="agent not found")
    cfg = {**agent_config.get_agent(name), **p.model_dump(exclude_none=True)}
    return llm.test_agent(cfg.get("connection", ""), cfg.get("model", ""),
                          agent_config.effective_system(cfg))


# --- Config source: local | remote (hub/peer — F-026, ADR-019) ------------
# Owner chooses whether the store is independent (local) or a hub client (remote, pulls
# config from another store). Owner-only (stores enrollment tokens — secrets). The API returns
# status WITHOUT secrets (tokens become has_* flags). Changing source applies live.

@router.get("/api/admin/config/source")
def config_source_get(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return hub.settings_public()


@router.put("/api/admin/config/source")
def config_source_set(body: HubSourceIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    hub_settings.update_settings(**body.model_dump(exclude_none=True))
    hub.apply_source()  # reinstala a ConfigSource ativa conforme a nova escolha (a quente)
    return hub.settings_public()


@router.post("/api/admin/config/source/sync")
def config_source_sync(authorization: str | None = Header(default=None)):
    # "Sync now" button: forces a hub pull (only in remote mode).
    _require_owner(authorization)
    return hub.sync_now()


# --- Menu/surface feature flags (F-033) ------------------------------
# Owner toggles on/off menu areas (what PARTICIPANTS see). Served by the same config
# source (local/hub): in `remote` hub flags apply (propagates to 150 VMs). Reading
# EFFECTIVE flags is PUBLIC (front decides what to show/block); editing is OWNER-only.

@router.get("/api/flags")
def flags_effective():
    # Effective flags (public enough for front to decide menu/routes). No secrets.
    return feature_flags.effective_flags()


@router.get("/api/admin/flags")
def flags_admin(authorization: str | None = Header(default=None)):
    # Owner toggle screen: LOCAL flags (editable) + EFFECTIVE ones + source, to make
    # clear when the hub is overriding local (remote mode).
    _require_owner(authorization)
    s = hub_settings.get_settings()
    return {"local": feature_flags.get_local_flags(),
            "effective": feature_flags.effective_flags(),
            "source": s["source"]}


@router.put("/api/admin/flags")
def flags_set(body: FlagsIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    feature_flags.update_flags(**body.model_dump(exclude_none=True))
    s = hub_settings.get_settings()
    return {"local": feature_flags.get_local_flags(),
            "effective": feature_flags.effective_flags(),
            "source": s["source"]}


# --- Splunk RUM (Browser Agent) — snippet configurable by owner (F-040-RUM) -
# Owner pastes raw RUM snippet + toggles on; front injects in <head> (server-render)
# for all browser sessions. Reading PUBLIC (RUM token is client-side by nature, goes
# to every visitor's HTML); EDITING owner-only (raw snippet = arbitrary JS on clients — DT).

@router.get("/api/rum")
def rum_public():
    # What front injects (server-render in layout): only brings snippet when enabled. No gate.
    return rum.public_config()


@router.get("/api/admin/rum")
def rum_admin(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return rum.get_config()


@router.put("/api/admin/rum")
def rum_set(body: RumIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return rum.update_config(**body.model_dump(exclude_none=True))


# --- LLM Inspector (OWNER-only, toggleable — F-023, ADR-017) ----------------
# LOCAL capture of LLM activity (system/user prompt + response + model/provider/tokens/
# cache/latency) in a ring buffer in memory — prompt content stays local. Owner-only
# (guards prompt content); toggleable (in-memory flag, default ON; becomes real feature flag in
# F-025). Hidden from participants.

@router.get("/api/admin/llm-activity")
def llm_activity_list(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return llm_activity.snapshot()  # {enabled, max, entries[]}


@router.put("/api/admin/llm-activity/enabled")
def llm_activity_set_enabled(u: InspectorToggle, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return {"enabled": llm_activity.set_enabled(u.enabled)}


@router.delete("/api/admin/llm-activity")
def llm_activity_clear(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    llm_activity.clear()
    return {"cleared": True}
