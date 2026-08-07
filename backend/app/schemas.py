"""Modelos de REQUEST da API (Pydantic) — extraídos de `api.py` na F-BACKEND-1.

Só forma de entrada: zero lógica, zero import de domínio. Os modelos ficam agrupados pelo
domínio do endpoint que os consome, na mesma ordem em que aparecem nos routers.

O formato de RESPOSTA não mora aqui — os endpoints devolvem os dicts que os módulos de domínio
já montam, e esse contrato é congelado (`CONVENCOES.md` §NÃO mude).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RunRequest(BaseModel):
    request: str = "a birthday gift under $300"

class ChatMessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class ChatContextIn(BaseModel):
    sku: str | None = None
    order_id: str | None = None

class ChatRequest(BaseModel):
    messages: list[ChatMessageIn]
    context: ChatContextIn | None = None

class SecurityActionRequest(BaseModel):
    action: Literal["delete_product", "export_recent_customers"]
    sku: str | None = None

class ProductQARequest(BaseModel):
    sku: str
    question: str = ""
class CompareRequest(BaseModel):
    # Compare 2 produtos (F-029): coordinator → comparator + tools (dados reais).
    sku_a: str
    sku_b: str
class CartCrossSellRequest(BaseModel):
    # IA-Carrinho (F-023): cross-sell a partir dos SKUs no carrinho atual.
    skus: list[str] = []
class OrderItemIn(BaseModel):
    sku: str
    name: str
    qty: int
    price: float
class CustomerIn(BaseModel):
    name: str
    email: str
    address: str
class CreateOrderRequest(BaseModel):
    items: list[OrderItemIn]
    customer: CustomerIn
class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str
class LoginRequest(BaseModel):
    email: str
    password: str
class UpdateMeRequest(BaseModel):
    address: str
class ProblemUpdate(BaseModel):
    price_hallucination: bool | None = None
    fraud_false_positive: bool | None = None
    inventory_outage: bool | None = None
    latency_spike: bool | None = None
    cost_spike: bool | None = None
    payment_outage: bool | None = None
    payment_latency: bool | None = None
    refund_false_denial: bool | None = None  # F-029: nega um reembolso elegível (erro do agente)
    prompt_injection: bool | None = None  # UC-4: agente aceita override de preço/política do comprador
    active_scenario: str | None = None  # preset UC ativo (uc-1..uc-5); "" limpa
class InspectorToggle(BaseModel):
    # Liga/desliga o LLM Inspector (F-023; owner-only).
    enabled: bool
class RumIn(BaseModel):
    # Edição parcial da config do Splunk RUM (F-040-RUM; owner-only). None = não mexe.
    enabled: bool | None = None
    snippet: str | None = None
class FlagsIn(BaseModel):
    # Edição parcial das feature flags de menu (F-033; owner-only). None = não mexe.
    behind_the_scenes: bool | None = None
    admin: bool | None = None
    simulator: bool | None = None
    inspector: bool | None = None
class SimStartRequest(BaseModel):
    # Config do simulador avançado (F-018). Tudo opcional → defaults/clamps em SimConfig.from_dict.
    mode: str | None = None                  # api | browser (F-039): API in-process vs navegador real
    concurrency: int | None = None          # N: tamanho do pool E nº de jornadas concorrentes
    wait_min_s: float | None = None         # espera entre jornadas (slot ocioso)
    wait_max_s: float | None = None
    think_min_s: float | None = None        # think-time entre ações
    think_max_s: float | None = None
    actions_min: int | None = None          # nº de ações de navegação por jornada
    actions_max: int | None = None
    concierge_pct: int | None = None        # % de jornadas que usam o Concierge
    problem_pct: int | None = None          # % de jornadas que injetam um problema
    problems: list[str] | None = None       # quais problemas elegíveis p/ injeção
    category_mix: dict[str, int] | None = None  # peso por categoria no carrinho
    tier_mix: dict[str, int] | None = None      # distribuição de tier dos usuários criados
    speed: float | None = None              # multiplicador dos sleeps (<1 = demo rápido)
    target_kind: str | None = None          # none | orders | duration
    target_value: int | None = None         # nº de pedidos OU segundos
    reset: bool | None = None               # limpar pedidos antes de iniciar
    max_lines: int | None = None
    max_qty: int | None = None
class SimPauseRequest(BaseModel):
    paused: bool = True
class ProviderIn(BaseModel):
    # Cria um provider da cascata de LLM (config owner-only — F-020). `api_key` é segredo
    # (write-only; nunca volta ao front). `kind`: openai | anthropic | bedrock.
    name: str
    kind: str = "openai"
    base_url: str = ""
    model: str
    api_key: str = ""
    enabled: bool = True
class ProviderUpdate(BaseModel):
    # Atualização parcial. `api_key` vazio/omitido MANTÉM a chave atual (write-only).
    name: str | None = None
    kind: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
    enabled: bool | None = None
    order: int | None = None
class ReorderIn(BaseModel):
    ids: list[str]
class TestProviderIn(BaseModel):
    # Test "ao vivo" de um provider ainda não salvo (UI). Se vier vazio, testa o salvo por id.
    name: str | None = None
    kind: str | None = None
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None
class AgentUpdate(BaseModel):
    # Config por agente (F-021). Campos parciais; None mantém. Sem segredo (vai cru ao front).
    connection: str | None = None   # provider id (LP-xxxx) ou '' = cascata completa
    model: str | None = None        # override opcional do modelo
    role: str | None = None
    system_prompt: str | None = None
class AgentTestIn(BaseModel):
    # Test "ao vivo" de um agente: edições opcionais sobre o salvo → 1 chamada real ao LLM resolvido.
    connection: str | None = None
    model: str | None = None
    role: str | None = None
    system_prompt: str | None = None
class HubSourceIn(BaseModel):
    # Fonte de config local|remote (hub/peer — F-026). Tokens são write-only (segredo;
    # nunca voltam ao front). `serve_token` aceita '' explícito (owner desliga o servir).
    source: str | None = None             # local | remote
    hub_url: str | None = None            # URL do hub (lado cliente)
    enrollment_token: str | None = None   # token p/ puxar do hub (write-only)
    pull_interval_s: int | None = None
    serve_token: str | None = None        # token exigido p/ servir como hub
class EnrollIn(BaseModel):
    # Enroll RECEBIDO (lado cliente — F-027). Máquina-a-máquina: o hub manda a própria URL +
    # o token p/ esta loja puxar a config. Gateado por ENROLL_TOKEN (segredo do lab), não owner.
    hub_url: str
    enrollment_token: str = ""
    pull_interval_s: int | None = None
class EnrollPushIn(BaseModel):
    # Enroll PUSH (lado hub — F-027, owner-only): força N lojas (por IP) a virar clientes deste hub.
    ips: list[str]
    hub_url: str                          # URL deste hub (como os alvos o alcançam)
    enroll_token: str                     # segredo compartilhado p/ autenticar nos alvos (ENROLL_TOKEN deles)
    enrollment_token: str                 # token que os alvos usarão p/ puxar (= serve_token deste hub)
    pull_interval_s: int | None = None
