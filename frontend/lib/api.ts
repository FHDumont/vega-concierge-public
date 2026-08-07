// API base — resolvido por AMBIENTE (F-041, ADR-024 › separado):
// • NAVEGADOR: base PÚBLICO da API (origem própria, subdomínio `api.vega.<dom>`) lido de
//   `window.__API_BASE`, INJETADO em runtime pelo server-render (ver app/layout.tsx). NÃO usamos
//   `NEXT_PUBLIC_*` nem rewrite porque AMBOS são "baked" no `next build` (inlined / routes-manifest)
//   → quebrariam o multi-host. A injeção em runtime mantém 1 imagem servindo qualquer HOMELAB_DOMAIN.
// • SERVIDOR (SSR): não há `window` → fala com o backend interno direto (API_INTERNAL_URL, runtime;
//   default dev = localhost:8000; no compose = http://backend:8000).
declare global {
  interface Window {
    // Base público da API injetado em runtime pelo server-render (app/layout.tsx).
    __API_BASE?: string;
  }
}

const BASE =
  typeof window === "undefined"
    ? process.env.API_INTERNAL_URL || "http://localhost:8000"
    : window.__API_BASE || "";

export type Product = { sku: string; name: string; price: number; tags: string[]; description?: string; stock?: number };

export type StorePolicy = { slug: string; title: string; markdown: string };

export type RunResult = {
  messages: string[];
  quality: { grounded: boolean; accuracy: number } | null;
  recommended: Product | null;
  answer: string | null; // recomendação composta pelo LLM, fundamentada no produto real (F-025)
  language: string | null; // idioma detectado/usado na resposta (F-025)
  order: { order_id: string; status: string } | null;
  error: string | null;
};
export type Problems = {
  active_scenario?: string;
} & Record<string, boolean | string | undefined>;

// Pedido persistido (F-003). Espelha o domínio Order do backend (SQLite).
export type OrderItem = { sku: string; name: string; qty: number; price: number };
export type Customer = { name: string; email: string; address: string };
export type OrderStatus = "PENDING" | "PAID" | "SHIPPED" | "DELIVERED" | "FAILED" | "REFUNDED";
export type OrderTransition = { status: OrderStatus; at: string };
export type Order = {
  id: string;
  items: OrderItem[];
  customer: Customer;
  total: number;
  status: OrderStatus;
  created_at: string;
  history?: OrderTransition[]; // transições do ciclo de vida (F-005)
  failure_reason?: string; // checkout FAILED — inventory, fraud, payment (workshop UX)
};

// Conta de usuário (F-008). `tier` é computado pelo gasto acumulado no backend;
// `spend` é o total acumulado (BRL) usado para exibir o progresso de tier.
export type Tier = "STANDARD" | "GOLD" | "PLATINUM";
// `role` (F-020): STANDARD | OWNER. OWNER gateia a config de LLM (tela escondida p/ os demais).
export type Role = "STANDARD" | "OWNER";
// `address` (F-011): endereço salvo no perfil; pré-preenche o checkout (vazio = sem endereço).
export type User = { id: string; name: string; email: string; tier: Tier; role: Role; spend: number; address: string };
export type AuthResult = { token: string; user: User };

// Token da sessão (bearer) mantido em memória; o AuthProvider o sincroniza com o
// localStorage e o injeta aqui (ADR-011 — sem cookie por causa do CORS "*").
let authToken: string | null = null;
export function setAuthToken(token: string | null) {
  authToken = token;
}
function authHeaders(): Record<string, string> {
  return authToken ? { authorization: `Bearer ${authToken}` } : {};
}

// --- Sessão de comprador (F-GALILEO-1, expiry F-GALILEO-8) ------------------
// UUID por navegador, persistido no localStorage e enviado em `X-Vega-Session` nas chamadas de
// IA. Costura os vários requests de uma visita numa sessão só no Splunk Agent Observability — é o que habilita as
// métricas de nó de sessão do Console. Expira após inatividade (default 5 min, configurável via
// VEGA_SESSION_IDLE_MINUTES) ou manualmente (botão "New session" no BTS). Não é autenticação.
const SESSION_KEY = "vega_shopper_session";
const SESSION_AT_KEY = "vega_shopper_session_at";
let shopperSessionId: string | null = null;
let sessionIdleMinutes = 5;
let sessionConfigLoaded = false;
let sessionConfigPromise: Promise<void> | null = null;

function newUuid(): string {
  // `crypto.randomUUID` só existe em secure context, e o workshop roda em http://<IP-da-VM>
  // (ADR-025) — daí o fallback.
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    return (ch === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function persistShopperSession(id: string, atMs: number = Date.now()): void {
  shopperSessionId = id;
  try {
    window.localStorage.setItem(SESSION_KEY, id);
    window.localStorage.setItem(SESSION_AT_KEY, String(atMs));
  } catch {
    /* localStorage bloqueado (navegação privada) */
  }
}

/** Override do timeout de inatividade (0 = desliga expiry). Chamado após getGalileoConfig. */
export function configureShopperSession(idleMinutes: number): void {
  sessionIdleMinutes = idleMinutes >= 0 ? idleMinutes : 0;
  sessionConfigLoaded = true;
}

/** Gera UUID novo e reinicia a jornada Splunk Agent Observability (botão BTS ou expiry por inatividade). */
export function resetShopperSession(): string {
  const id = newUuid();
  persistShopperSession(id);
  return id;
}

function ensureSessionConfig(): Promise<void> {
  if (sessionConfigLoaded) return Promise.resolve();
  if (sessionConfigPromise) return sessionConfigPromise;
  sessionConfigPromise = getGalileoConfig()
    .then((g) => configureShopperSession(g.session_idle_minutes))
    .catch(() => {
      /* mantém default 5 min */
    });
  return sessionConfigPromise;
}

function resolveShopperSession(touchActivity: boolean): string {
  if (typeof window === "undefined") return newUuid();
  void ensureSessionConfig();

  const now = Date.now();
  let id: string | null = null;
  let atMs = 0;

  try {
    id = window.localStorage.getItem(SESSION_KEY);
    const rawAt = window.localStorage.getItem(SESSION_AT_KEY);
    atMs = rawAt ? Number(rawAt) : 0;
  } catch {
    /* localStorage bloqueado */
  }

  if (shopperSessionId && !id) id = shopperSessionId;

  const idleMs = sessionIdleMinutes > 0 ? sessionIdleMinutes * 60_000 : 0;
  const expired = idleMs > 0 && id !== null && atMs > 0 && now - atMs > idleMs;

  if (!id || expired) {
    return resetShopperSession();
  }

  if (touchActivity) {
    persistShopperSession(id, now);
  } else {
    shopperSessionId = id;
  }
  return id;
}

/** Prefetch da config de idle (Providers no boot) — evita race na 1ª request de IA. */
export function initShopperSessionConfig(): void {
  void ensureSessionConfig();
}

function sessionHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {}; // SSR não tem jornada de comprador
  return { "x-vega-session": resolveShopperSession(true) };
}

export async function register(name: string, email: string, password: string): Promise<AuthResult> {
  const r = await fetch(`${BASE}/api/auth/register`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, email, password }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `register failed: ${r.status}`);
  return r.json();
}
export async function login(email: string, password: string): Promise<AuthResult> {
  const r = await fetch(`${BASE}/api/auth/login`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `login failed: ${r.status}`);
  return r.json();
}
export async function logout(): Promise<void> {
  await fetch(`${BASE}/api/auth/logout`, { method: "POST", headers: authHeaders() });
}
// Retorna o usuário da sessão atual, ou null se o token está ausente/expirado.
export async function getMe(): Promise<User | null> {
  const r = await fetch(`${BASE}/api/auth/me`, { headers: authHeaders() });
  if (r.status === 401) return null;
  if (!r.ok) throw new Error(`me failed: ${r.status}`);
  return (await r.json()).user;
}
// Salva/edita o endereço do perfil (F-011). Exige sessão; retorna o usuário atualizado.
export async function updateAddress(address: string): Promise<User> {
  const r = await fetch(`${BASE}/api/auth/me`, {
    method: "PUT", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ address }),
  });
  if (!r.ok) throw new Error(`update failed: ${r.status}`);
  return (await r.json()).user;
}

export async function runConcierge(request: string): Promise<RunResult> {
  const r = await fetch(`${BASE}/api/run`, {
    method: "POST", headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({ request }),
  });
  return r.json();
}

// --- Chat aberto (F-050-CHAT) ------------------------------------------------
export type ChatMessage = { role: "user" | "assistant"; content: string };
export type ChatContext = { sku?: string; order_id?: string };
export type ChatResult = {
  reply: string;
  intent: string;
  artifacts: Record<string, unknown>;
  language: string | null;
  llm_unavailable?: boolean;
  error: string | null;
};
export async function sendChatMessage(
  messages: ChatMessage[],
  context?: ChatContext,
): Promise<ChatResult> {
  const r = await fetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders(), ...sessionHeaders() },
    body: JSON.stringify({ messages, context: context || undefined }),
  });
  return r.json();
}

export async function getCatalog(): Promise<Product[]> {
  return (await fetch(`${BASE}/api/catalog`)).json();
}

export async function getPolicies(): Promise<StorePolicy[]> {
  const data = await (await fetch(`${BASE}/api/policies`)).json();
  return data.policies ?? [];
}

// --- IA-Produto (F-022) -----------------------------------------------------
// Q&A fundamentado nos dados do produto na PDP. Honra os toggles de problema.
export async function askProduct(sku: string, question: string): Promise<{ answer: string; grounded: boolean }> {
  const r = await fetch(`${BASE}/api/product/qa`, {
    method: "POST", headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({ sku, question }),
  });
  if (!r.ok) throw new Error(`qa failed: ${r.status}`);
  return r.json();
}

// --- Compare 2 produtos (F-029) ---------------------------------------------
// Orquestração simples: Compare Coordinator (agente) busca os 2 produtos via tool real e delega
// ao Comparator (agente) o veredito exibido. Só o conteúdo. Honra os toggles + cache (par igual
// 2× = cache hit).
export type CompareLayout = {
  lead?: string;
  sections?: { title: string; body: string }[];
  facts?: { label: string; value: string }[];
  bullets?: string[];
};

export type CompareResult = {
  product_a: Product;
  product_b: Product;
  verdict: string;
  layout?: CompareLayout | null;
};
export async function compareProducts(skuA: string, skuB: string): Promise<CompareResult> {
  const r = await fetch(`${BASE}/api/compare`, {
    method: "POST", headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({ sku_a: skuA, sku_b: skuB }),
  });
  if (!r.ok) throw new Error(`compare failed: ${r.status}`);
  return r.json();
}

// --- IA-Carrinho (F-023) ----------------------------------------------------
// Cross-sell/bundle ("complete your purchase") a partir dos SKUs no carrinho.
// Honra os toggles; fallback gracioso offline.
export type CartCrossSell = { products: Product[]; blurb: string };
export async function cartCrossSell(skus: string[]): Promise<CartCrossSell> {
  const r = await fetch(`${BASE}/api/cart/crosssell`, {
    method: "POST", headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({ skus }),
  });
  if (!r.ok) throw new Error(`crosssell failed: ${r.status}`);
  return r.json();
}

export async function createOrder(items: OrderItem[], customer: Customer): Promise<Order> {
  const r = await fetch(`${BASE}/api/orders`, {
    method: "POST", headers: { "content-type": "application/json", ...authHeaders(), ...sessionHeaders() },
    body: JSON.stringify({ items, customer }),
  });
  if (!r.ok) throw new Error(`order failed: ${r.status}`);
  return r.json();
}
// Histórico de compras do usuário logado (F-008). Exige sessão (401 sem token).
export async function getOrders(): Promise<Order[]> {
  const r = await fetch(`${BASE}/api/orders`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`orders failed: ${r.status}`);
  return r.json();
}
// Detalhe de uma ordem. Envia o token da sessão (F-019): com sessão, o backend só
// devolve a PRÓPRIA ordem (404 p/ ordem alheia). Sem token segue público (Admin).
export async function getOrder(id: string): Promise<Order> {
  const r = await fetch(`${BASE}/api/orders/${id}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`order not found: ${r.status}`);
  return r.json();
}
// --- IA-Notificação (F-031) -------------------------------------------------
// Copy gerada de e-mail p/ o evento atual do pedido (confirmação/enviado), reaproveitando a
// notificação simulada (F-005). Só o conteúdo. Backend resolve a ordem (grounding real) + honra
// os toggles; offline → fallback gracioso. Envia o bearer (autorização do getOrder, F-019).
export type NotificationCopy = {
  subject: string;
  body: string;
  channel: string;
  event: "confirmation" | "shipped";
  grounded: boolean;
};
export async function orderNotification(id: string): Promise<NotificationCopy> {
  const r = await fetch(`${BASE}/api/orders/${id}/notification`, {
    method: "POST", headers: { ...authHeaders(), ...sessionHeaders() },
  });
  if (!r.ok) throw new Error(`notification failed: ${r.status}`);
  return r.json();
}

// --- IA-Conta (F-031) -------------------------------------------------------
// Insights do histórico + benefícios do tier + sugestão de recompra a partir dos dados reais
// do usuário logado. Só o conteúdo. Backend resolve user/pedidos (grounding real) + honra os
// toggles; offline → fallback gracioso. Exige sessão.
export type AccountInsights = {
  summary: string;
  tier_benefits: string;
  repurchase: string;
  grounded: boolean;
};
export async function accountInsights(): Promise<AccountInsights> {
  const r = await fetch(`${BASE}/api/account/insights`, {
    headers: { ...authHeaders(), ...sessionHeaders() },
  });
  if (!r.ok) throw new Error(`account insights failed: ${r.status}`);
  return r.json();
}

// --- Returns/Refund Coordinator (F-029) -------------------------------------
// Orquestração COMPLEXA: a partir de um pedido DELIVERED, o Returns Coordinator roda
// eligibility→policy→calc→abuse→process e marca o pedido REFUNDED quando aprovado. Só o conteúdo
// (passos + veredito). Envia o bearer (autorização do getOrder, F-019).
export type RefundStep = { label: string; ok: boolean; detail: string };
export type RefundResult = {
  eligible: boolean; approved: boolean; refunded: boolean; refund_amount: number;
  status: OrderStatus; reason: string; steps: RefundStep[]; order: Order;
};
export async function requestRefund(id: string): Promise<RefundResult> {
  const r = await fetch(`${BASE}/api/orders/${id}/refund`, {
    method: "POST", headers: { ...authHeaders(), ...sessionHeaders() },
  });
  if (!r.ok) throw new Error(`refund failed: ${r.status}`);
  return r.json();
}

// --- IA-Checkout (F-024) ----------------------------------------------------
// Explicação amigável de bloqueio de fraude quando o pedido é barrado.
// a UI só mostra a explicação amigável quando true. Envia o bearer (autorização do getOrder).
export async function fraudExplain(id: string): Promise<{ explanation: string; fraud: boolean }> {
  const r = await fetch(`${BASE}/api/orders/${id}/fraud-explain`, {
    method: "POST", headers: { ...authHeaders(), ...sessionHeaders() },
  });
  if (!r.ok) throw new Error(`fraud explain failed: ${r.status}`);
  return r.json();
}

// --- Admin (camada de NEGÓCIO — dono; F-014) --------------------------------
// Endpoints aditivos de agregação/admin; veem TODOS os pedidos (diferente de getOrders,
// escopado pela sessão). Sem auth (consistente com os controles de workshop).
export type SalesSummary = {
  orders: number;
  paid_orders: number;
  revenue: number; // = net_revenue (mantido p/ compat)
  net_revenue: number;
  gross_revenue: number;
  refunded_amount: number;
  returned_orders: number;
  avg_ticket: number;
  by_status: Record<OrderStatus, number>;
};
export type AdminProduct = { sku: string; name: string; price: number; stock: number; tags: string[]; deleted?: boolean };

export async function getAdminSummary(): Promise<SalesSummary> {
  const r = await fetch(`${BASE}/api/admin/summary`);
  if (!r.ok) throw new Error(`summary failed: ${r.status}`);
  return r.json();
}

// IA-Admin (F-024): insights de vendas + anomalias + reposição a partir de dados agregados.
// Resumo/anomalias frasados pelo LLM (grounded nos números); restock determinístico. Honra os
// toggles; offline → texto determinístico. Custo controlado (cache/max_tokens no backend).
export type AdminInsights = {
  period_days: number;
  metrics: { orders: number; paid: number; failed: number; revenue: number; avg_ticket: number };
  summary: string;
  anomalies: string[];
  restock: { sku: string; name: string; stock: number }[];
};
export async function getAdminInsights(): Promise<AdminInsights> {
  const r = await fetch(`${BASE}/api/admin/insights`, { headers: sessionHeaders() });
  if (!r.ok) throw new Error(`insights failed: ${r.status}`);
  return r.json();
}
export async function getAdminOrders(): Promise<Order[]> {
  const r = await fetch(`${BASE}/api/admin/orders`);
  if (!r.ok) throw new Error(`admin orders failed: ${r.status}`);
  return r.json();
}
export async function getAdminProducts(): Promise<AdminProduct[]> {
  const r = await fetch(`${BASE}/api/admin/products`);
  if (!r.ok) throw new Error(`admin products failed: ${r.status}`);
  return r.json();
}
export async function seedAdminOrders(): Promise<number> {
  const r = await fetch(`${BASE}/api/admin/seed`, { method: "POST" });
  if (!r.ok) throw new Error(`seed failed: ${r.status}`);
  return (await r.json()).seeded;
}
// Clear Sales (F-027): apaga todos os pedidos E repõe o estoque aos níveis iniciais.
export async function clearAdminOrders(): Promise<{ cleared: number; stock_restored: number; catalog_restored?: number }> {
  const r = await fetch(`${BASE}/api/admin/orders`, { method: "DELETE" });
  if (!r.ok) throw new Error(`clear failed: ${r.status}`);
  return r.json();
}

// --- Simulador avançado (F-018, ADR-014) ------------------------------------
// Engine de sessões concorrentes: pool de N usuários + N jornadas que navegam e
// sempre compram (loop espera+sorteio). A tela própria (/admin/simulator) faz poll
// de simStatus. Config completa no start; controles stop/pause.
export type SimSession = {
  slot: number; user: string | null; tier: string | null;
  action: string; journeys: number; last: string | null;
};
export type SimConfig = {
  mode: "api" | "browser";   // F-039: API in-process vs navegador real (Playwright) p/ RUM
  concurrency: number;
  wait_min_s: number; wait_max_s: number;
  think_min_s: number; think_max_s: number;
  actions_min: number; actions_max: number;
  concierge_pct: number; problem_pct: number;
  problems: string[];
  category_mix: Record<string, number>;
  tier_mix: Record<string, number>;
  speed: number;
  target_kind: "none" | "orders" | "duration";
  target_value: number;
  reset: boolean;
  max_lines: number; max_qty: number;
};
export type SimStatus = {
  status: "stopped" | "running" | "paused";
  config: SimConfig;
  pool_size: number;
  uptime_s: number;
  completed: number;
  paid: number;
  injected: number;
  orders_per_min: number;
  errors: number;
  by_status: Record<string, number>;
  target: { kind: string; value: number };
  sessions: SimSession[];
};

export async function simStart(cfg: Partial<SimConfig>): Promise<SimStatus> {
  const r = await fetch(`${BASE}/api/simulator/start`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(cfg),
  });
  if (!r.ok) throw new Error(`sim start failed: ${r.status}`);
  return r.json();
}
export async function simStop(): Promise<SimStatus> {
  const r = await fetch(`${BASE}/api/simulator/stop`, { method: "POST" });
  if (!r.ok) throw new Error(`sim stop failed: ${r.status}`);
  return r.json();
}
export async function simPause(paused: boolean): Promise<SimStatus> {
  const r = await fetch(`${BASE}/api/simulator/pause`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ paused }),
  });
  if (!r.ok) throw new Error(`sim pause failed: ${r.status}`);
  return r.json();
}
export async function simStatus(): Promise<SimStatus> {
  const r = await fetch(`${BASE}/api/simulator/status`);
  if (!r.ok) throw new Error(`sim status failed: ${r.status}`);
  return r.json();
}

// --- Config de LLM (OWNER-only — F-020, ADR-015) ----------------------------
// Provedores da cascata gerenciados pelo dono. A API NUNCA devolve `api_key` (versão
// mascarada: has_key + key_hint). Todas as chamadas vão com o bearer do owner (authHeaders).
export type ProviderKind = "openai" | "anthropic" | "bedrock";
export type LLMProvider = {
  id: string; name: string; kind: ProviderKind; base_url: string; model: string;
  enabled: boolean; order: number; has_key: boolean; key_hint: string | null;
};
// Entrada de criação/edição. `api_key` é write-only: vazio MANTÉM a chave salva.
export type ProviderInput = {
  name: string; kind: ProviderKind; base_url: string; model: string; api_key?: string; enabled?: boolean;
};
export type ProviderTest = {
  ok: boolean; latency_ms?: number; model?: string; provider?: string;
  input_tokens?: number; output_tokens?: number; error?: string;
};

const CONFIG_BASE = `${BASE}/api/admin/config/providers`;

async function configJson<T>(r: Response, what: string): Promise<T> {
  if (r.status === 401) throw new Error("not authenticated");
  if (r.status === 403) throw new Error("owner only");
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${what} failed: ${r.status}`);
  return r.json();
}

export async function getProviders(): Promise<LLMProvider[]> {
  return configJson(await fetch(CONFIG_BASE, { headers: authHeaders() }), "providers");
}
export async function createProvider(input: ProviderInput): Promise<LLMProvider> {
  return configJson(await fetch(CONFIG_BASE, {
    method: "POST", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(input),
  }), "create provider");
}
export async function updateProvider(id: string, patch: Partial<ProviderInput> & { order?: number }): Promise<LLMProvider> {
  return configJson(await fetch(`${CONFIG_BASE}/${id}`, {
    method: "PUT", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(patch),
  }), "update provider");
}
export async function deleteProvider(id: string): Promise<void> {
  await configJson(await fetch(`${CONFIG_BASE}/${id}`, { method: "DELETE", headers: authHeaders() }), "delete provider");
}
export async function reorderProviders(ids: string[]): Promise<LLMProvider[]> {
  return configJson(await fetch(`${CONFIG_BASE}/reorder`, {
    method: "POST", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ ids }),
  }), "reorder providers");
}
// Test "ao vivo": edições opcionais (ex.: chave recém-digitada) sobre o provider salvo.
export async function testProvider(id: string, edits: Partial<ProviderInput> = {}): Promise<ProviderTest> {
  return configJson(await fetch(`${CONFIG_BASE}/${id}/test`, {
    method: "POST", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(edits),
  }), "test provider");
}

// --- Type presets de conexão (F-021) ----------------------------------------
// Catálogo p/ a UI de conexão: escolher um Type prefilla kind+base_url e sugere modelos
// econômicos (dropdown editável). Defaults convenientes/editáveis — não autoritativos.
export type LLMTypePreset = {
  type: string; label: string; kind: ProviderKind; base_url: string; models: string[];
};
export async function getLLMTypes(): Promise<LLMTypePreset[]> {
  return configJson(await fetch(`${BASE}/api/admin/config/llm-types`, { headers: authHeaders() }), "llm types");
}

// --- Config por agente (F-021) ----------------------------------------------
// Cada um dos 6 agentes do Concierge: `connection` (provider id ou '' = cascata completa),
// `model` (override opcional), `role` e `system_prompt`. Sem segredo (vai cru ao front).
export type AgentConfig = {
  agent: string; connection: string; model: string; role: string; system_prompt: string;
};
export type AgentInput = Partial<Pick<AgentConfig, "connection" | "model" | "role" | "system_prompt">>;
export type AgentTest = ProviderTest; // mesmo shape (ok/latency/model/provider/tokens/error)

const AGENTS_BASE = `${BASE}/api/admin/config/agents`;
export async function getAgents(): Promise<AgentConfig[]> {
  return configJson(await fetch(AGENTS_BASE, { headers: authHeaders() }), "agents");
}
export async function updateAgent(name: string, patch: AgentInput): Promise<AgentConfig> {
  return configJson(await fetch(`${AGENTS_BASE}/${name}`, {
    method: "PUT", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(patch),
  }), "update agent");
}
// Test "ao vivo" de um agente: edições opcionais (connection/model/role/system_prompt) sobre
// o salvo → 1 chamada real ao LLM resolvido p/ aquele agente.
export async function testAgent(name: string, edits: AgentInput = {}): Promise<AgentTest> {
  return configJson(await fetch(`${AGENTS_BASE}/${name}/test`, {
    method: "POST", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(edits),
  }), "test agent");
}

// --- Topologia de agentes (editor visual — F-027) ---------------------------
// Derivada do grafo real (ADR-029): clusters ORQUESTRADOS (concierge hub-and-spoke; fulfillment /
// compare / returns ReAct) + agentes STANDALONE (features F-022). Owner-only.
export type TopologyNode = {
  id: string; kind: "workflow" | "agent" | "tool" | "dep"; agent: string | null; role: string; label: string;
};
export type TopologyEdge = { from: string; to: string };
export type TopologyCluster = {
  id: string; label: string; kind: "orchestrated"; root: string;
  nodes: TopologyNode[]; edges: TopologyEdge[];
};
export type AgentTopology = { clusters: TopologyCluster[]; standalone: TopologyNode[] };
export async function getAgentTopology(): Promise<AgentTopology> {
  return configJson(await fetch(`${BASE}/api/admin/agents/topology`, { headers: authHeaders() }), "agent topology");
}

// --- Fonte de config: local | remote (hub/peer — F-026) ---------------------
// O owner escolhe se a loja é independente (local) ou cliente de um hub (remote: puxa a
// config de outra loja). Tokens nunca voltam ao front (flags has_*). Status de conexão
// resume modo/alvo/saúde/last-sync + clientes (no hub).
export type HubSource = {
  source: "local" | "remote";
  hub_url: string;
  pull_interval_s: number;
  has_enrollment_token: boolean;
  has_serve_token: boolean;
};
export type HubSourceInput = Partial<{
  source: "local" | "remote"; hub_url: string; enrollment_token: string;
  pull_interval_s: number; serve_token: string;
}>;
export type HubClient = {
  env: string; last_pull: string; first_seen?: string; ip?: string; agent?: string; pulls: number;
};
export type HubRemoteStatus = {
  hub_url: string; interval_s: number; has_cache: boolean; cached_providers: number;
  last_ok: boolean; last_error: string | null; last_sync: string | null; hub_env: string | null;
  insecure: boolean;  // HTTP não-local: as chaves trafegam em claro (DT-013) → avisar o owner
};
export type HubStatus = {
  env: string;
  mode: "standalone" | "client" | "hub" | "hub-idle";
  source: "local" | "remote";
  hub_url: string;
  has_enrollment_token: boolean;
  pull_interval_s: number;
  serving: boolean;
  remote: HubRemoteStatus | null;
  clients: HubClient[];
  local_providers: number;
};

const SOURCE_BASE = `${BASE}/api/admin/config/source`;
export async function getHubSource(): Promise<HubSource> {
  return configJson(await fetch(SOURCE_BASE, { headers: authHeaders() }), "config source");
}
export async function setHubSource(patch: HubSourceInput): Promise<HubSource> {
  return configJson(await fetch(SOURCE_BASE, {
    method: "PUT", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(patch),
  }), "set config source");
}
export async function syncHubNow(): Promise<HubRemoteStatus & { synced: boolean; reason?: string }> {
  return configJson(await fetch(`${SOURCE_BASE}/sync`, { method: "POST", headers: authHeaders() }), "sync hub");
}
export async function getHubStatus(): Promise<HubStatus> {
  return configJson(await fetch(`${BASE}/api/admin/hub/status`, { headers: authHeaders() }), "hub status");
}

// --- Enrollment push por IP (F-027) -----------------------------------------
// O hub empurra `source=remote` p/ N lojas (por IP/host), chamando o endpoint de enroll de cada
// uma (token-gated por um segredo compartilhado do lab). Resultado POR IP (ok/falha/timeout).
export type EnrollPushInput = {
  ips: string[]; hub_url: string; enroll_token: string; enrollment_token: string; pull_interval_s?: number;
};
export type EnrollPushResult = {
  total: number; ok: number; failed: number;
  results: { ip: string; ok: boolean; status?: number; env?: string; mode?: string; error?: string }[];
};
export async function enrollPush(input: EnrollPushInput): Promise<EnrollPushResult> {
  return configJson(await fetch(`${BASE}/api/admin/hub/enroll-push`, {
    method: "POST", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(input),
  }), "enroll push");
}

// --- LLM Inspector (OWNER-only, desligável — F-023, ADR-017) ----------------
// Captura LOCAL de atividade de LLM (conteúdo completo + metadados). Owner-only (guarda
// prompts); reusa o tratamento 401/403 do namespace de config.
export type LLMActivityEntry = {
  id: number; ts: string; feature: string; model: string; provider: string; family: string;
  input_tokens: number; output_tokens: number; prompt_cache_tokens?: number;
  cache: string | null; latency_ms: number;
  fallback: boolean; system: string; prompt: string; response: string;
};
export type LLMActivity = { enabled: boolean; max: number; entries: LLMActivityEntry[] };

export async function getLLMActivity(): Promise<LLMActivity> {
  return configJson(await fetch(`${BASE}/api/admin/llm-activity`, { headers: authHeaders() }), "llm activity");
}
export async function setLLMInspectorEnabled(enabled: boolean): Promise<boolean> {
  const r = await configJson<{ enabled: boolean }>(await fetch(`${BASE}/api/admin/llm-activity/enabled`, {
    method: "PUT", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ enabled }),
  }), "inspector toggle");
  return r.enabled;
}
export async function clearLLMActivity(): Promise<void> {
  await configJson(await fetch(`${BASE}/api/admin/llm-activity`, { method: "DELETE", headers: authHeaders() }), "clear activity");
}

// --- Feature flags de menu/superfícies (F-033) ------------------------------
// O owner liga/desliga áreas do menu (o que os PARTICIPANTES veem). Servidas pela mesma fonte
// de config (local/hub): em `remote` o hub vence. A leitura das EFETIVAS é PÚBLICA (o front
// decide menu/rotas); a edição é owner-only. `effective` ≠ `local` quando o hub sobrepõe.
export type FeatureFlags = {
  behind_the_scenes: boolean;
  admin: boolean;
  simulator: boolean;
  inspector: boolean;
};
export type AdminFlags = { local: FeatureFlags; effective: FeatureFlags; source: "local" | "remote" };

export async function getFlags(): Promise<FeatureFlags> {
  return (await fetch(`${BASE}/api/flags`)).json();
}
export async function getAdminFlags(): Promise<AdminFlags> {
  return configJson(await fetch(`${BASE}/api/admin/flags`, { headers: authHeaders() }), "flags");
}
export async function setFlags(patch: Partial<FeatureFlags>): Promise<AdminFlags> {
  return configJson(await fetch(`${BASE}/api/admin/flags`, {
    method: "PUT", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(patch),
  }), "set flags");
}

export async function getProblems(): Promise<Problems> {
  return (await fetch(`${BASE}/api/problems`)).json();
}
export async function setProblems(p: Problems): Promise<Problems> {
  return (await fetch(`${BASE}/api/problems`, {
    method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(p),
  })).json();
}

// --- Splunk Agent Observability workshop (F-GALILEO-3) -----------------------------------------
export type GalileoConfig = {
  enabled: boolean;
  console_url: string;
  project: string;
  log_stream: string;
  agent_control_url: string;
  session_idle_minutes: number;
};
export async function getGalileoConfig(): Promise<GalileoConfig> {
  const r = await fetch(`${BASE}/api/galileo/config`);
  if (!r.ok) throw new Error(`Splunk Agent Observability config failed: ${r.status}`);
  return r.json();
}
export async function applyProblemPreset(presetId: string): Promise<Problems> {
  const r = await fetch(`${BASE}/api/problems/preset/${encodeURIComponent(presetId)}`, { method: "POST" });
  if (!r.ok) throw new Error(`preset failed: ${r.status}`);
  return r.json();
}
/** UUID da jornada do comprador (`vega_shopper_session`) — filtra sessão no Splunk Agent Observability Console. */
export function getShopperSessionId(): string | null {
  if (typeof window === "undefined") return null;
  return resolveShopperSession(false);
}

// --- Splunk RUM (Browser Agent) — snippet configurável pelo owner (F-040-RUM) ---
// `getRum` é PÚBLICO (o layout server-render consome p/ injetar no <head>); a edição
// (`getRumAdmin`/`setRum`) é owner-only (bearer via authHeaders).
export type RumConfig = { enabled: boolean; snippet: string };
export async function getRum(): Promise<RumConfig> {
  const r = await fetch(`${BASE}/api/rum`);
  if (!r.ok) throw new Error(`rum fetch failed: ${r.status}`);
  return r.json();
}
export async function getRumAdmin(): Promise<RumConfig> {
  const r = await fetch(`${BASE}/api/admin/rum`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`rum admin fetch failed: ${r.status}`);
  return r.json();
}
export async function setRum(patch: Partial<RumConfig>): Promise<RumConfig> {
  const r = await fetch(`${BASE}/api/admin/rum`, {
    method: "PUT", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`rum save failed: ${r.status}`);
  return r.json();
}
