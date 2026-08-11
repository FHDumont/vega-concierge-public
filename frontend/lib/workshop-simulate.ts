// Fires real Shop requests to generate Splunk Agent Observability traces — F-GALILEO-6.
import {
  askProduct,
  applyProblemPreset,
  createOrder,
  deleteCatalogProduct,
  getOrders,
  getProblems,
  login,
  orderNotification,
  requestRefund,
  runConcierge,
  sendChatMessage,
  setAuthToken,
  setProblems,
  type ChatContext,
  type Order,
  type Problems,
} from "@/lib/api";
import {
  SIMULATE_BY_KEY,
  SIMULATE_BY_UC,
  type SimulateActionSpec,
  type SimulateSpec,
} from "@/lib/galileo-workshop";

const DEMO_EMAIL = "demo@vega.test";
const DEMO_PASSWORD = "demo1234";
const DEMO_CUSTOMER = {
  name: "Demo User",
  email: DEMO_EMAIL,
  address: "123 Workshop St, Austin, TX",
  ssn: "123-45-6789",
  card_number: "4242 4242 4242 4242",
  card_exp: "08/28",
  card_cvv: "123",
};
const CHECKOUT_ITEM = { sku: "NS-001", name: "Aura Bluetooth Headphones", qty: 1, price: 249.0 };

export type SimulateStepResult = {
  id: string;
  label: string;
  ok: boolean;
  summary: string;
  request?: string;
  grounded?: boolean;
  orderStatus?: string;
  intent?: string;
  error?: string;
};

export type SimulateRunResult = {
  ok: boolean;
  summary: string;
  steps: SimulateStepResult[];
  flags: Problems;
};

async function ensureDemoAuth() {
  const auth = await login(DEMO_EMAIL, DEMO_PASSWORD);
  setAuthToken(auth.token);
}

async function findDeliveredOrder(): Promise<Order> {
  await ensureDemoAuth();
  const orders = await getOrders();
  const delivered = orders.find((o) => o.status === "DELIVERED");
  if (!delivered) {
    throw new Error(
      "No DELIVERED order for demo@vega.test. Restart the stack (demo seed runs on boot) or create orders via the Store / simulator.",
    );
  }
  return delivered;
}

/** Advanced: turn on required toggles without clearing others. */
async function mergeToggles(spec: SimulateSpec): Promise<Problems> {
  const current = await getProblems();
  const next: Problems = { ...current };
  for (const key of spec.requiredToggles) {
    next[key] = true;
  }
  return setProblems(next);
}

function chatContext(action: SimulateActionSpec): ChatContext | undefined {
  const ctx: ChatContext = {};
  if (action.contextSku) ctx.sku = action.contextSku;
  if (action.contextOrderId) ctx.order_id = action.contextOrderId;
  return Object.keys(ctx).length ? ctx : undefined;
}

async function runAction(action: SimulateActionSpec): Promise<SimulateStepResult> {
  try {
    switch (action.kind) {
      case "product_qa": {
        const question = (action.question ?? action.prompt ?? "").trim();
        if (!question) {
          return {
            id: action.id,
            label: action.label,
            ok: false,
            summary: "",
            request: "POST /api/product/qa",
            error: "Missing question text for product Q&A",
          };
        }
        const sku = action.sku ?? "NS-001";
        const r = await askProduct(sku, question);
        const text = (r.answer ?? "").trim();
        return {
          id: action.id,
          label: action.label,
          ok: text.length > 0,
          request: `POST /api/product/qa — ${sku}: "${question}"`,
          summary: text || "(empty response from model)",
          grounded: r.grounded,
          error: text.length > 0 ? undefined : "Model returned an empty answer — check LLM provider in Admin",
        };
      }
      case "chat": {
        const prompt = (action.prompt ?? "").trim();
        if (!prompt) {
          return {
            id: action.id,
            label: action.label,
            ok: false,
            summary: "",
            request: "POST /api/chat",
            error: "Missing chat prompt",
          };
        }
        const ctx = chatContext(action);
        const r = await sendChatMessage([{ role: "user", content: prompt }], ctx);
        const artifacts = r.artifacts ?? {};
        const grounded =
          typeof artifacts.grounded === "boolean"
            ? artifacts.grounded
            : typeof (artifacts.quality as { grounded?: boolean } | undefined)?.grounded === "boolean"
              ? (artifacts.quality as { grounded: boolean }).grounded
              : undefined;
        const reply = (r.reply ?? "").trim();
        const ctxNote = ctx?.sku ? ` sku=${ctx.sku}` : ctx?.order_id ? ` order=${ctx.order_id}` : "";
        return {
          id: action.id,
          label: action.label,
          ok: !r.error && reply.length > 0,
          request: `POST /api/chat — "${prompt}"${ctxNote}`,
          summary: reply || r.error || "(empty reply)",
          intent: r.intent,
          grounded,
          error: r.error ?? (reply.length === 0 ? "Empty chat reply" : undefined),
        };
      }
      case "run": {
        const prompt = (action.prompt ?? "").trim();
        if (!prompt) {
          return {
            id: action.id,
            label: action.label,
            ok: false,
            summary: "",
            request: "POST /api/run",
            error: "Missing run prompt",
          };
        }
        const r = await runConcierge(prompt);
        const answer = (r.answer ?? "").trim();
        return {
          id: action.id,
          label: action.label,
          ok: !r.error && answer.length > 0,
          request: `POST /api/run — "${prompt}"`,
          summary: answer || r.error || "(empty answer)",
          grounded: r.quality?.grounded,
          orderStatus: r.order?.status,
          error: r.error ?? (answer.length === 0 ? "Empty concierge answer" : undefined),
        };
      }
      case "security_delete": {
        const sku = (action.sku ?? "NS-001").trim().toUpperCase();
        const prompt = action.prompt?.trim();
        const r = await deleteCatalogProduct(sku, prompt);
        const summary = r.deleted
          ? `Deleted ${sku} from the catalog.`
          : r.blocked
            ? `Blocked delete for ${sku}: ${r.reason ?? "policy"}`
            : `Delete did not complete for ${sku}.`;
        return {
          id: action.id,
          label: action.label,
          ok: Boolean(r.deleted || r.blocked),
          request: prompt
            ? `POST /api/security/actions — delete_product ${sku} (${prompt.slice(0, 48)}…)`
            : `POST /api/security/actions — delete_product ${sku}`,
          summary,
          error: r.deleted || r.blocked ? undefined : r.error ?? "Delete failed",
        };
      }
      case "checkout": {
        await ensureDemoAuth();
        const order = await createOrder([CHECKOUT_ITEM], DEMO_CUSTOMER);
        return {
          id: action.id,
          label: action.label,
          ok: true,
          request: "POST /api/orders — demo checkout NS-001",
          summary: `Order ${order.id} → ${order.status}`,
          orderStatus: order.status,
        };
      }
      case "refund": {
        const order = await findDeliveredOrder();
        const r = await requestRefund(order.id);
        return {
          id: action.id,
          label: action.label,
          ok: true,
          request: `POST /api/orders/${order.id}/refund`,
          summary: r.approved ? `Approved — ${r.reason}` : `Denied — ${r.reason}`,
          orderStatus: r.status,
        };
      }
      case "notification": {
        const order = await findDeliveredOrder();
        const r = await orderNotification(order.id);
        const body = (r.body ?? "").trim();
        return {
          id: action.id,
          label: action.label,
          ok: body.length > 0,
          request: `POST /api/orders/${order.id}/notification`,
          summary: `${r.subject} — ${body.slice(0, 140)}${body.length > 140 ? "…" : ""}`,
          grounded: r.grounded,
          error: body.length > 0 ? undefined : "Empty notification body",
        };
      }
      default:
        return { id: action.id, label: action.label, ok: false, summary: "", error: "Unknown action" };
    }
  } catch (e) {
    return {
      id: action.id,
      label: action.label,
      ok: false,
      summary: "",
      error: e instanceof Error ? e.message : String(e),
    };
  }
}

async function runSimulate(spec: SimulateSpec, flags: Problems): Promise<SimulateRunResult> {
  const steps: SimulateStepResult[] = [];
  for (const action of spec.actions) {
    steps.push(await runAction(action));
  }
  const ok = steps.every((s) => s.ok);
  const summary = steps
    .map((s) => (s.ok ? `${s.label}: ${s.summary}` : `${s.label}: ${s.error}`))
    .join(" · ");
  return { ok, summary, steps, flags };
}

export async function runSimulateByKey(key: string): Promise<SimulateRunResult> {
  const spec = SIMULATE_BY_KEY[key];
  if (!spec) throw new Error(`No simulate spec for toggle: ${key}`);
  const flags = await mergeToggles(spec);
  return runSimulate(spec, flags);
}

/** Workshop: exclusive preset (one scenario) then fire actions — matches Scenario ON. */
export async function runSimulateByUc(ucId: string): Promise<SimulateRunResult> {
  const spec = SIMULATE_BY_UC[ucId];
  if (!spec) throw new Error(`No simulate spec for UC: ${ucId}`);
  const flags = await applyProblemPreset(ucId);
  return runSimulate(spec, flags);
}
