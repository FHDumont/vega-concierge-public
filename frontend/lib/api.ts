// API base — resolved per ENVIRONMENT (F-041, ADR-024 › separated):
// • BROWSER: PUBLIC API base (own origin, `api.vega.<dom>` subdomain) read from
//   `window.__API_BASE`, INJECTED at runtime by the server-render (see app/layout.tsx). We do NOT use
//   `NEXT_PUBLIC_*` or rewrite because BOTH are "baked" at `next build` (inlined / routes-manifest)
//   → they'd break the multi-host setup. Runtime injection keeps 1 image serving any HOMELAB_DOMAIN.
// • SERVER (SSR): no `window` → talks to the internal backend directly (API_INTERNAL_URL, runtime;
//   dev default = localhost:8000; in compose = http://backend:8000).
declare global {
  interface Window {
    // Public API base injected at runtime by the server-render (app/layout.tsx).
    __API_BASE?: string;
  }
}

const BASE =
  typeof window === "undefined"
    ? process.env.API_INTERNAL_URL || "http://localhost:8000"
    : window.__API_BASE || "";

/** API HTTP 429 — friendly message + Retry-After (F-WORKSHOP-GUARD). */
export class ApiRateLimitError extends Error {
  readonly retryAfterSeconds: number;

  constructor(message: string, retryAfterSeconds: number) {
    super(message);
    this.name = "ApiRateLimitError";
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

async function throwIfRateLimited(r: Response): Promise<void> {
  if (r.status !== 429) return;
  const body = (await r.json().catch(() => ({}))) as {
    detail?: string;
    retry_after_seconds?: number;
  };
  const detail =
    (typeof body.detail === "string" && body.detail) ||
    "Too many requests. Please wait a moment and try again.";
  const headerRetry = r.headers.get("Retry-After");
  const parsedHeader = headerRetry ? parseInt(headerRetry, 10) : NaN;
  const retryAfterSeconds = Number.isFinite(parsedHeader)
    ? parsedHeader
    : typeof body.retry_after_seconds === "number"
      ? body.retry_after_seconds
      : 60;
  throw new ApiRateLimitError(detail, retryAfterSeconds);
}

/** Central fetch — propagates 429 without automatic retry. */
async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const r = await fetch(input, init);
  await throwIfRateLimited(r);
  return r;
}

export type Product = { sku: string; name: string; price: number; tags: string[]; description?: string; stock?: number };

export type StorePolicy = { slug: string; title: string; markdown: string };

export type RunResult = {
  messages: string[];
  quality: { grounded: boolean; accuracy: number } | null;
  recommended: Product | null;
  answer: string | null; // recommendation composed by the LLM, grounded in the real product (F-025)
  language: string | null; // language detected/used in the response (F-025)
  order: { order_id: string; status: string } | null;
  error: string | null;
};
export type Problems = {
  active_scenario?: string;
} & Record<string, boolean | string | undefined>;

// Persisted order (F-003). Mirrors the backend's Order domain (SQLite).
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
  history?: OrderTransition[]; // lifecycle transitions (F-005)
  failure_reason?: string; // checkout FAILED — inventory, fraud, payment (workshop UX)
};

// User account (F-008). `tier` is computed from accumulated spend on the backend;
// `spend` is the accumulated total (BRL) used to display tier progress.
export type Tier = "STANDARD" | "GOLD" | "PLATINUM";
// `role` (F-020): STANDARD | OWNER. OWNER gates the LLM config (screen hidden from everyone else).
export type Role = "STANDARD" | "OWNER";
// `address` (F-011): address saved on the profile; pre-fills checkout (empty = no address).
export type User = { id: string; name: string; email: string; tier: Tier; role: Role; spend: number; address: string };
export type AuthResult = { token: string; user: User };

// Session token (bearer) kept in memory; the AuthProvider syncs it with
// localStorage and injects it here (ADR-011 — no cookie because of CORS "*").
let authToken: string | null = null;
export function setAuthToken(token: string | null) {
  authToken = token;
}
function authHeaders(): Record<string, string> {
  return authToken ? { authorization: `Bearer ${authToken}` } : {};
}

// --- Shopper session (F-GALILEO-1, expiry F-GALILEO-8) ------------------
// Per-browser UUID, persisted in localStorage and sent in `X-Vega-Session` on AI
// calls. Stitches a visit's several requests into a single session in Splunk Agent Observability — this is what
// enables the Console's session-node metrics. Expires after inactivity (default 5 min, configurable via
// VEGA_SESSION_IDLE_MINUTES) or manually ("New session" button in BTS). Not authentication.
const SESSION_KEY = "vega_shopper_session";
const SESSION_AT_KEY = "vega_shopper_session_at";
let shopperSessionId: string | null = null;
let sessionIdleMinutes = 5;
let sessionConfigLoaded = false;
let sessionConfigPromise: Promise<void> | null = null;

function newUuid(): string {
  // `crypto.randomUUID` only exists in a secure context, and the workshop runs on http://<VM-IP>
  // (ADR-025) — hence the fallback.
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
    /* localStorage blocked (private browsing) */
  }
}

/** Override for the inactivity timeout (0 = disables expiry). Called after getGalileoConfig. */
export function configureShopperSession(idleMinutes: number): void {
  sessionIdleMinutes = idleMinutes >= 0 ? idleMinutes : 0;
  sessionConfigLoaded = true;
}

/** Generates a new UUID and restarts the Splunk Agent Observability journey (BTS button or inactivity expiry). */
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
      /* keeps the 5 min default */
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
    /* localStorage blocked */
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

/** Prefetch of the idle config (Providers on boot) — avoids a race on the 1st AI request. */
export function initShopperSessionConfig(): void {
  void ensureSessionConfig();
}

function sessionHeaders(): Record<string, string> {
  if (typeof window === "undefined") return {}; // SSR has no shopper journey
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
// Returns the current session's user, or null if the token is missing/expired.
export async function getMe(): Promise<User | null> {
  const r = await fetch(`${BASE}/api/auth/me`, { headers: authHeaders() });
  if (r.status === 401) return null;
  if (!r.ok) throw new Error(`me failed: ${r.status}`);
  return (await r.json()).user;
}
// Saves/edits the profile address (F-011). Requires a session; returns the updated user.
export async function updateAddress(address: string): Promise<User> {
  const r = await fetch(`${BASE}/api/auth/me`, {
    method: "PUT", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify({ address }),
  });
  if (!r.ok) throw new Error(`update failed: ${r.status}`);
  return (await r.json()).user;
}

export async function runConcierge(request: string): Promise<RunResult> {
  const r = await apiFetch(`${BASE}/api/run`, {
    method: "POST", headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({ request }),
  });
  return r.json();
}

export type SecurityDeleteResult = {
  deleted?: boolean;
  blocked?: boolean;
  sku?: string;
  reason?: string;
  error?: string;
};

export async function deleteCatalogProduct(
  sku: string,
  prompt?: string,
): Promise<SecurityDeleteResult> {
  const r = await apiFetch(`${BASE}/api/security/actions`, {
    method: "POST",
    headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({
      action: "delete_product",
      sku,
      ...(prompt ? { prompt } : {}),
    }),
  });
  if (!r.ok) throw new Error(`security action failed: ${r.status}`);
  return r.json();
}

// --- Open chat (F-050-CHAT) ------------------------------------------------
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
  const r = await apiFetch(`${BASE}/api/chat`, {
    method: "POST",
    headers: { "content-type": "application/json", ...authHeaders(), ...sessionHeaders() },
    body: JSON.stringify({ messages, context: context || undefined }),
  });
  if (!r.ok) throw new Error(`chat failed: ${r.status}`);
  return r.json();
}

export async function getCatalog(): Promise<Product[]> {
  return (await fetch(`${BASE}/api/catalog`)).json();
}

export async function getPolicies(): Promise<StorePolicy[]> {
  const data = await (await fetch(`${BASE}/api/policies`)).json();
  return data.policies ?? [];
}

export async function recommendGift(
  request = "a birthday gift under $300",
): Promise<{ answer: string; recommended: Record<string, unknown> | null; quality: Record<string, unknown> }> {
  const r = await apiFetch(`${BASE}/api/recommend/gift`, {
    method: "POST",
    headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({ request }),
  });
  if (!r.ok) throw new Error(`gift recommend failed: ${r.status}`);
  return r.json();
}

// --- AI-Product (F-022) -----------------------------------------------------
// Q&A grounded in the product data on the PDP. Honors the problem toggles.
export async function askProduct(sku: string, question: string): Promise<{ answer: string; grounded: boolean }> {
  const r = await apiFetch(`${BASE}/api/product/qa`, {
    method: "POST", headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({ sku, question }),
  });
  if (!r.ok) throw new Error(`qa failed: ${r.status}`);
  return r.json();
}

// --- Compare 2 products (F-029) ---------------------------------------------
// Simple orchestration: Compare Coordinator (agent) fetches the 2 products via a real tool and delegates
// the displayed verdict to the Comparator (agent). Content only. Honors the toggles + cache (same pair
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
  const r = await apiFetch(`${BASE}/api/compare`, {
    method: "POST", headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({ sku_a: skuA, sku_b: skuB }),
  });
  if (!r.ok) throw new Error(`compare failed: ${r.status}`);
  return r.json();
}

// --- AI-Cart (F-023) ----------------------------------------------------
// Cross-sell/bundle ("complete your purchase") from the SKUs in the cart.
// Honors the toggles; graceful offline fallback.
export type CartCrossSell = { products: Product[]; blurb: string };
export async function cartCrossSell(skus: string[]): Promise<CartCrossSell> {
  const r = await apiFetch(`${BASE}/api/cart/crosssell`, {
    method: "POST", headers: { "content-type": "application/json", ...sessionHeaders() },
    body: JSON.stringify({ skus }),
  });
  if (!r.ok) throw new Error(`crosssell failed: ${r.status}`);
  return r.json();
}

export async function createOrder(items: OrderItem[], customer: Customer): Promise<Order> {
  const r = await apiFetch(`${BASE}/api/orders`, {
    method: "POST", headers: { "content-type": "application/json", ...authHeaders(), ...sessionHeaders() },
    body: JSON.stringify({ items, customer }),
  });
  if (!r.ok) throw new Error(`order failed: ${r.status}`);
  return r.json();
}
// Purchase history for the logged-in user (F-008). Requires a session (401 without token).
export async function getOrders(): Promise<Order[]> {
  const r = await fetch(`${BASE}/api/orders`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`orders failed: ${r.status}`);
  return r.json();
}
// Order detail. Sends the session token (F-019): with a session, the backend only
// returns the user's OWN order (404 for someone else's order). Without a token it stays public (Admin).
export async function getOrder(id: string): Promise<Order> {
  const r = await fetch(`${BASE}/api/orders/${id}`, { headers: authHeaders() });
  if (!r.ok) throw new Error(`order not found: ${r.status}`);
  return r.json();
}
// --- AI-Notification (F-031) -------------------------------------------------
// Generated email copy for the order's current event (confirmation/shipped), reusing the
// simulated notification (F-005). Content only. Backend resolves the order (real grounding) + honors
// the toggles; offline → graceful fallback. Sends the bearer (getOrder's authorization, F-019).
export type NotificationCopy = {
  subject: string;
  body: string;
  channel: string;
  event: "confirmation" | "shipped";
  grounded: boolean;
};
export async function orderNotification(id: string): Promise<NotificationCopy> {
  const r = await apiFetch(`${BASE}/api/orders/${id}/notification`, {
    method: "POST", headers: { ...authHeaders(), ...sessionHeaders() },
  });
  if (!r.ok) throw new Error(`notification failed: ${r.status}`);
  return r.json();
}

// --- AI-Account (F-031) -------------------------------------------------------
// Insights from history + tier benefits + repurchase suggestion based on the logged-in
// user's real data. Content only. Backend resolves user/orders (real grounding) + honors the
// toggles; offline → graceful fallback. Requires a session.
export type AccountInsights = {
  summary: string;
  tier_benefits: string;
  repurchase: string;
  grounded: boolean;
};
export async function accountInsights(): Promise<AccountInsights> {
  const r = await apiFetch(`${BASE}/api/account/insights`, {
    headers: { ...authHeaders(), ...sessionHeaders() },
  });
  if (!r.ok) throw new Error(`account insights failed: ${r.status}`);
  return r.json();
}

// --- Returns/Refund Coordinator (F-029) -------------------------------------
// COMPLEX orchestration: starting from a DELIVERED order, the Returns Coordinator runs
// eligibility→policy→calc→abuse→process and marks the order REFUNDED when approved. Content only
// (steps + verdict). Sends the bearer (getOrder's authorization, F-019).
export type RefundStep = { label: string; ok: boolean; detail: string };
export type RefundResult = {
  eligible: boolean; approved: boolean; refunded: boolean; refund_amount: number;
  status: OrderStatus; reason: string; steps: RefundStep[]; order: Order;
};
export async function requestRefund(id: string): Promise<RefundResult> {
  const r = await apiFetch(`${BASE}/api/orders/${id}/refund`, {
    method: "POST", headers: { ...authHeaders(), ...sessionHeaders() },
  });
  if (!r.ok) {
    const err = new Error(`refund failed: ${r.status}`) as Error & { status?: number };
    err.status = r.status;
    throw err;
  }
  return r.json();
}

// --- AI-Checkout (F-024) ----------------------------------------------------
// Friendly explanation of a fraud block when the order is denied.
// the UI only shows the friendly explanation when true. Sends the bearer (getOrder's authorization).
export async function fraudExplain(id: string): Promise<{ explanation: string; fraud: boolean }> {
  const r = await apiFetch(`${BASE}/api/orders/${id}/fraud-explain`, {
    method: "POST", headers: { ...authHeaders(), ...sessionHeaders() },
  });
  if (!r.ok) throw new Error(`fraud explain failed: ${r.status}`);
  return r.json();
}

// --- Admin (BUSINESS layer — owner; F-014) --------------------------------
// Additive aggregation/admin endpoints; see ALL orders (unlike getOrders,
// which is session-scoped). No auth (consistent with the workshop controls).
export type SalesSummary = {
  orders: number;
  paid_orders: number;
  revenue: number; // = net_revenue (kept for compat)
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

// AI-Admin (F-024): sales insights + anomalies + restocking from aggregated data.
// Summary/anomalies phrased by the LLM (grounded in the numbers); restock is deterministic. Honors the
// toggles; offline → deterministic text. Controlled cost (cache/max_tokens on the backend).
export type AdminInsights = {
  period_days: number;
  metrics: { orders: number; paid: number; failed: number; revenue: number; avg_ticket: number };
  summary: string;
  anomalies: string[];
  restock: { sku: string; name: string; stock: number }[];
};
export async function getAdminInsights(): Promise<AdminInsights> {
  const r = await apiFetch(`${BASE}/api/admin/insights`, { headers: sessionHeaders() });
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
// Clear Sales (F-027): deletes all orders AND restores stock to initial levels.
export async function clearAdminOrders(): Promise<{ cleared: number; stock_restored: number; catalog_restored?: number }> {
  const r = await fetch(`${BASE}/api/admin/orders`, { method: "DELETE" });
  if (!r.ok) throw new Error(`clear failed: ${r.status}`);
  return r.json();
}

// --- Advanced simulator (F-018, ADR-014) ------------------------------------
// Concurrent-sessions engine: pool of N users + N journeys that browse and
// always buy (wait+roll loop). The dedicated screen (/admin/simulator) polls
// simStatus. Full config on start; stop/pause controls.
export type SimSession = {
  slot: number; user: string | null; tier: string | null;
  action: string; journeys: number; last: string | null;
};
export type SimConfig = {
  mode: "api" | "browser";   // F-039: API in-process vs. real browser (Playwright) for RUM
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
  const r = await apiFetch(`${BASE}/api/simulator/start`, {
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

// --- LLM Config (OWNER-only — F-020, ADR-015) ----------------------------
// Cascade providers managed by the owner. The API NEVER returns `api_key` (masked
// version: has_key + key_hint). All calls go with the owner's bearer (authHeaders).
export type ProviderKind = "openai" | "anthropic" | "bedrock";
export type LLMProvider = {
  id: string; name: string; kind: ProviderKind; base_url: string; model: string;
  enabled: boolean; order: number; has_key: boolean; key_hint: string | null;
};
// Create/edit input. `api_key` is write-only: empty KEEPS the saved key.
export type ProviderInput = {
  name: string; kind: ProviderKind; base_url: string; model: string; api_key?: string; enabled?: boolean;
};
export type ProviderTest = {
  ok: boolean; latency_ms?: number; model?: string; provider?: string;
  input_tokens?: number; output_tokens?: number; error?: string;
};

const CONFIG_BASE = `${BASE}/api/admin/config/providers`;

async function configJson<T>(r: Response, what: string): Promise<T> {
  await throwIfRateLimited(r);
  if (r.status === 401) throw new Error("not authenticated");
  if (r.status === 403) throw new Error("owner only");
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `${what} failed: ${r.status}`);
  return r.json();
}

export async function getProviders(): Promise<LLMProvider[]> {
  return configJson(await apiFetch(CONFIG_BASE, { headers: authHeaders() }), "providers");
}
export async function createProvider(input: ProviderInput): Promise<LLMProvider> {
  return configJson(await apiFetch(CONFIG_BASE, {
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
// "Live" test: optional edits (e.g. a freshly typed key) on top of the saved provider.
export async function testProvider(id: string, edits: Partial<ProviderInput> = {}): Promise<ProviderTest> {
  return configJson(await fetch(`${CONFIG_BASE}/${id}/test`, {
    method: "POST", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(edits),
  }), "test provider");
}

// --- Connection type presets (F-021) ----------------------------------------
// Catalog for the connection UI: choosing a Type prefills kind+base_url and suggests
// economical models (editable dropdown). Convenient/editable defaults — not authoritative.
export type LLMTypePreset = {
  type: string; label: string; kind: ProviderKind; base_url: string; models: string[];
};
export async function getLLMTypes(): Promise<LLMTypePreset[]> {
  return configJson(await fetch(`${BASE}/api/admin/config/llm-types`, { headers: authHeaders() }), "llm types");
}

// --- Per-agent config (F-021) ----------------------------------------------
// Each of the Concierge's 6 agents: `connection` (provider id or '' = full cascade),
// `model` (optional override), `role`, and `system_prompt`. No secrets (goes to the frontend raw).
export type AgentConfig = {
  agent: string; connection: string; model: string; role: string; system_prompt: string;
};
export type AgentInput = Partial<Pick<AgentConfig, "connection" | "model" | "role" | "system_prompt">>;
export type AgentTest = ProviderTest; // same shape (ok/latency/model/provider/tokens/error)

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
// "Live" test of an agent: optional edits (connection/model/role/system_prompt) on top
// of the saved config → 1 real call to the LLM resolved for that agent.
export async function testAgent(name: string, edits: AgentInput = {}): Promise<AgentTest> {
  return configJson(await fetch(`${AGENTS_BASE}/${name}/test`, {
    method: "POST", headers: { "content-type": "application/json", ...authHeaders() },
    body: JSON.stringify(edits),
  }), "test agent");
}

// --- Agent topology (visual editor — F-027) ---------------------------
// Derived from the real graph (ADR-029): ORCHESTRATED clusters (concierge hub-and-spoke; fulfillment /
// compare / returns ReAct) + STANDALONE agents (F-022 features). Owner-only.
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

// --- Config source: local | remote (hub/peer — F-026) ---------------------
// The owner chooses whether the store is standalone (local) or a hub client (remote: pulls the
// config from another store). Tokens never come back to the frontend (has_* flags). Connection status
// summarizes mode/target/health/last-sync + clients (on the hub).
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
  insecure: boolean;  // non-local HTTP: keys travel in the clear (DT-013) → warn the owner
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

export type HubTestProvider = { name: string; model: string; kind: string };
export type HubTestConnection = {
  source: string; mode: HubStatus["mode"]; ok: boolean; provider_count: number;
  providers: HubTestProvider[]; flags: Record<string, boolean>;
  remote: HubRemoteStatus | null; sync: Record<string, unknown> | null;
};
export async function testHubConnection(): Promise<HubTestConnection> {
  return configJson(await fetch(`${BASE}/api/admin/hub/test-connection`, {
    method: "POST", headers: authHeaders(),
  }), "test hub connection");
}

// --- Enrollment push by IP (F-027) -----------------------------------------
// The hub pushes `source=remote` to N stores (by IP/host), calling each one's enroll
// endpoint (token-gated by a shared lab secret). Result PER IP (ok/failure/timeout).
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

// --- LLM Inspector (OWNER-only, can be disabled — F-023, ADR-017) ----------------
// LOCAL capture of LLM activity (full content + metadata). Owner-only (stores
// prompts); reuses the config namespace's 401/403 handling.
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

// --- Menu/surfaces feature flags (F-033) ------------------------------
// The owner toggles menu areas on/off (what PARTICIPANTS see). Served from the same config
// source (local/hub): in `remote` mode the hub wins. Reading the EFFECTIVE flags is PUBLIC (the frontend
// decides menu/routes); editing is owner-only. `effective` ≠ `local` when the hub overrides.
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
/** Shopper journey UUID (`vega_shopper_session`) — filters the session in the Splunk Agent Observability Console. */
export function getShopperSessionId(): string | null {
  if (typeof window === "undefined") return null;
  return resolveShopperSession(false);
}

// --- Splunk RUM (Browser Agent) — snippet configurable by the owner (F-040-RUM) ---
// `getRum` is PUBLIC (the server-render layout consumes it to inject into <head>); editing
// (`getRumAdmin`/`setRum`) is owner-only (bearer via authHeaders).
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
