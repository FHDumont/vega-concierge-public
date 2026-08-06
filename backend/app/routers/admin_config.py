"""Config OWNER-only — cascata de LLM, agentes, fonte local/hub, flags, RUM e Inspector."""
from fastapi import APIRouter, Header, HTTPException
from .. import agent_config
from .. import feature_flags
from .. import hub
from .. import hub_settings
from .. import llm
from .. import llm_providers
from .. import llm_activity
from .. import llm_config
from .. import rum
from .. import topology
from ..schemas import AgentTestIn, AgentUpdate, FlagsIn, HubSourceIn, InspectorToggle, ProviderIn, ProviderUpdate, ReorderIn, RumIn, TestProviderIn
from ._common import _require_owner

# Sem `prefix`: cada rota carrega o path completo, igualzinho ao que estava em `api.py`.
router = APIRouter()


# --- Config de LLM (OWNER-only — F-020, ADR-015) ----------------------------
# Gerencia os provedores da cascata (ordem/enable/modelo/chave). Diferente do resto do
# Admin (sem auth, controles de workshop), estes endpoints são GATED a OWNER: a config
# guarda SEGREDOS (chaves). A API só devolve a versão MASCARADA (sem `api_key`).

@router.get("/api/admin/config/llm-types")
def config_llm_types(authorization: str | None = Header(default=None)):
    # Catálogo de Type presets (base_url + modelos econômicos sugeridos) p/ a UI de conexão
    # (F-021). Não guarda segredo — gated a OWNER só p/ consistência do namespace de config.
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
    # Faz UMA chamada de teste e devolve ok/erro/latência (sem vazar a chave). Usa o provider
    # salvo (com a chave guardada) mesclado com os campos editados na UI; se a UI mandar uma
    # nova chave usa-a, senão mantém a salva — assim o owner testa edições antes de salvar.
    _require_owner(authorization)
    stored = llm_config.get_provider_with_key(provider_id)
    if stored is None:
        raise HTTPException(status_code=404, detail="provider not found")
    edits = body.model_dump(exclude_none=True)
    cfg = {**stored, **{k: v for k, v in edits.items() if k != "api_key" or v}}
    return llm.test_provider(cfg)


# --- Config por agente (OWNER-only — F-021; + features de loja F-022) --------
# Os 6 agentes do Concierge + as features de IA da Loja (product_qa/product_desc/search),
# cada um com connection/model/role/system_prompt. Sem segredo (vai cru ao front), mas gated
# a OWNER p/ consistência do namespace de config.

@router.get("/api/admin/agents/topology")
def config_agents_topology(authorization: str | None = Header(default=None)):
    # Editor visual (F-027): topologia da orquestração (clusters + standalone) derivada do
    # grafo real (agents.py, ADR-018). Owner-only — clicar num agente abre/edita a config (F-021).
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
    # Resolve o LLM do agente (saved + edições da UI) e faz UMA chamada real com o system
    # efetivo (role + system_prompt). Mostra provider/modelo/tokens reais (stub se cair).
    _require_owner(authorization)
    if name not in agent_config.AGENT_NAMES:
        raise HTTPException(status_code=404, detail="agent not found")
    cfg = {**agent_config.get_agent(name), **p.model_dump(exclude_none=True)}
    return llm.test_agent(cfg.get("connection", ""), cfg.get("model", ""),
                          agent_config.effective_system(cfg))


# --- Fonte de config: local | remote (hub/peer — F-026, ADR-019) ------------
# O owner escolhe se a loja é independente (local) ou cliente de um hub (remote, puxa a
# config de outra loja). Owner-only (guarda tokens de enrollment — segredos). A API devolve
# o status SEM segredos (tokens viram flags has_*). Mudar a fonte aplica a quente.

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
    # Botão "sync agora": força um pull do hub (só em modo remote).
    _require_owner(authorization)
    return hub.sync_now()


# --- Feature flags de menu/superfícies (F-033) ------------------------------
# O owner liga/desliga áreas do menu (o que os PARTICIPANTES veem). Servidas pela mesma fonte
# de config (local/hub): em `remote` valem as flags do hub (propaga p/ as 150 VMs). A leitura
# das EFETIVAS é PÚBLICA (o front decide o que mostrar/bloquear); a edição é OWNER-only.

@router.get("/api/flags")
def flags_effective():
    # Flags efetivas (públicas o suficiente p/ o front decidir menu/rotas). Sem segredo.
    return feature_flags.effective_flags()


@router.get("/api/admin/flags")
def flags_admin(authorization: str | None = Header(default=None)):
    # Tela de toggles do owner: as flags LOCAIS (editáveis) + as EFETIVAS + a fonte, p/ deixar
    # claro quando o hub está sobrepondo o local (modo remote).
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


# --- Splunk RUM (Browser Agent) — snippet configurável pelo owner (F-040-RUM) -
# O owner cola o snippet bruto do RUM + liga o toggle; o front injeta no <head> (server-render)
# p/ todas as sessões de navegador. Leitura PÚBLICA (o token RUM é client-side por natureza, vai
# ao HTML de todo visitante); EDIÇÃO owner-only (snippet bruto = JS arbitrário nos clientes — DT).

@router.get("/api/rum")
def rum_public():
    # O que o front injeta (server-render no layout): só traz o snippet quando enabled. Sem gate.
    return rum.public_config()


@router.get("/api/admin/rum")
def rum_admin(authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return rum.get_config()


@router.put("/api/admin/rum")
def rum_set(body: RumIn, authorization: str | None = Header(default=None)):
    _require_owner(authorization)
    return rum.update_config(**body.model_dump(exclude_none=True))


# --- LLM Inspector (OWNER-only, desligável — F-023, ADR-017) ----------------
# Captura LOCAL de atividade de LLM (system/user prompt + resposta + modelo/provider/tokens/
# cache/latência) num ring buffer em memória — o conteúdo de prompt fica local. Owner-only
# (guarda conteúdo de prompt); desligável (flag em memória, default ON; vira feature flag de
# verdade na F-025). Some p/ participantes.

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
