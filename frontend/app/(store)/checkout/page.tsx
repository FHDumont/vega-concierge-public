"use client";
// Checkout em PÁGINA PRÓPRIA (F-011) — antes era continuação do carrinho slide-over.
// Fluxo Details → Payment → Confirmation no design custom dirigido por paletas
// (ADR-012/013). Envia o carrinho REAL (itens+qty) + cliente para POST /api/orders, que
// cria/persiste o pedido e passa pelo pipeline (fraude/estoque) — os "problemas" quebram
// o fluxo de forma visível, SEM dados técnicos na Loja. Pagamento simulado: o cartão NÃO é
// enviado (sem gateway real). Estado de cliente vem do ShopProvider; sessão do AuthProvider.
import { useEffect, useState } from "react";
import Link from "next/link";
import { Customer, Order, createOrder, giftMessage, fraudExplain } from "@/lib/api";
import { emojiOf, formatMoney, gradientOf } from "@/lib/shop";
import { useShop } from "@/lib/store";
import { useAuth } from "@/lib/auth";
import StatusPill from "@/components/StatusPill";
import OrderStatusSummary from "@/components/OrderStatusSummary";
import NotificationPreview from "@/components/NotificationPreview";
import AiThinking from "@/components/AiThinking";
import AuthForms from "@/components/AuthForms";

type Stage = "details" | "payment" | "placing" | "confirmed" | "failed";

const EMPTY_CUSTOMER: Customer = { name: "", email: "", address: "" };

// Cartão fictício de demo (F-012) — pré-preenchido no passo Payment; nunca é enviado ao backend.
const DEMO_CARD = { number: "4242 4242 4242 4242", expiry: "12/29", cvc: "123" };

const STEPS = ["Details", "Payment", "Confirmation"] as const;
// Mapeia cada etapa para o passo ativo na barra (Details → Payment → Confirmation).
const STEP_OF: Record<Stage, number> = {
  details: 0,
  payment: 1,
  placing: 1,
  failed: 1,
  confirmed: 2,
};

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <div className="ns-field">
      <label className="ns-label">{label}</label>
      <input
        className="ns-input"
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
      />
    </div>
  );
}

const GIFT_PRESETS = [
  "Birthday for my sister, warm and playful",
  "Thank-you gift for a colleague, professional",
  "Housewarming, casual and friendly",
];

// IA-Checkout (F-024): gerador de mensagem de presente a partir de um breve input. Opcional —
// só uma conveniência no checkout (não é persistida na ordem; o shape de Order não muda).
function GiftMessageField() {
  const [open, setOpen] = useState(false);
  const [brief, setBrief] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  async function generate() {
    if (busy) return;
    setBusy(true);
    try {
      setMessage((await giftMessage(brief)).message);
    } catch {
      /* silencioso: presente é opcional */
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="ns-link" style={{ marginTop: 14 }} onClick={() => setOpen(true)}>
        🎁 Add a gift message
      </button>
    );
  }
  return (
    <div className="ns-gift">
      <div className="ns-gift-head">
        <span className="ns-spark sm" aria-hidden>✦</span>
        <label className="ns-label" style={{ margin: 0 }}>Gift message</label>
      </div>
      <textarea
        className="ns-input"
        value={brief}
        onChange={(e) => setBrief(e.target.value)}
        placeholder="e.g. birthday for my sister, warm and playful"
        aria-label="Gift message brief"
        style={{ minHeight: 96, resize: "vertical" }}
      />
      <div className="ns-pai-chips" style={{ marginTop: 10 }}>
        {GIFT_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            className="ns-chip"
            onClick={() => setBrief(preset)}
            disabled={busy}
          >
            {preset}
          </button>
        ))}
      </div>
      <button type="button" className="ns-btn-ghost" style={{ marginTop: 10 }} onClick={generate} disabled={busy}>
        {busy ? "Writing…" : message ? "Regenerate" : "Generate message"}
      </button>
      {busy && (
        <div style={{ marginTop: 10 }}>
          <AiThinking label="Writing your gift message" />
        </div>
      )}
      {!busy && message && (
        <textarea
          className="ns-input"
          style={{ marginTop: 10, minHeight: 140, resize: "vertical" }}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          aria-label="Generated gift message"
        />
      )}
    </div>
  );
}

// IA-Checkout (F-024): explicação amigável quando o pedido é barrado por fraude. Só aparece
// quando o backend sinaliza `fraud` (toggle fraud_false_positive) — senão a falha segue genérica.
function FraudExplain({ orderId }: { orderId: string }) {
  const [text, setText] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    fraudExplain(orderId)
      .then((d) => alive && d.fraud && setText(d.explanation))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [orderId]);

  if (!text) return null;
  return (
    <div className="ns-aisum" style={{ marginTop: 14 }} aria-label="Order review explanation">
      <span className="ns-spark sm" aria-hidden>✦</span>
      <p>{text}</p>
    </div>
  );
}

export default function CheckoutPage() {
  const shop = useShop();
  const { user, ready, refresh, saveAddress } = useAuth();

  const [stage, setStage] = useState<Stage>("details");
  const [customer, setCustomer] = useState<Customer>(EMPTY_CUSTOMER);
  const [card, setCard] = useState(DEMO_CARD);
  const [order, setOrder] = useState<Order | null>(null);

  // Endereço (F-011): "saved" usa o endereço do perfil (não pede de novo); "new" pede e
  // oferece salvar no perfil. `null` até a sessão resolver. Convidado/sem endereço → "new".
  const [addressChoice, setAddressChoice] = useState<"saved" | "new" | null>(null);
  const [saveToProfile, setSaveToProfile] = useState(true);

  // Pré-preenche nome/e-mail/endereço pelo usuário logado.
  useEffect(() => {
    if (!ready || !user) return;
    const savedAddress = user.address.trim();
    setCustomer((prev) => ({
      name: prev.name || user.name,
      email: prev.email || user.email,
      address: prev.address || savedAddress,
    }));
    setAddressChoice((c) => c ?? (savedAddress ? "saved" : "new"));
  }, [ready, user]);

  const items = shop.cart;
  const total = items.reduce((s, i) => s + i.product.price * i.qty, 0);

  const customerValid =
    customer.name.trim() !== "" &&
    /.+@.+\..+/.test(customer.email) &&
    customer.address.trim() !== "";
  const cardValid =
    card.number.replace(/\s/g, "").length >= 12 && card.expiry.trim() !== "" && card.cvc.trim() !== "";

  async function pay() {
    setStage("placing");
    try {
      const placed = await createOrder(
        items.map((i) => ({
          sku: i.product.sku,
          name: i.product.name,
          qty: i.qty,
          price: i.product.price,
        })),
        customer,
      );
      setOrder(placed);
      if (placed.status === "PAID") {
        // Salva o endereço no perfil se o cliente optou por isso (etapa "new", logado).
        if (user && addressChoice === "new" && saveToProfile && customer.address.trim()) {
          await saveAddress(customer.address).catch(() => {});
        }
        shop.clear(); // esvazia o carrinho ao confirmar
        refresh(); // refletir gasto/tier atualizados
        setStage("confirmed");
      } else {
        setStage("failed");
      }
    } catch {
      setStage("failed");
    }
  }

  const showSteps = stage !== "failed";

  if (!ready) {
    return (
      <main className="ns-wrap ns-checkout">
        <div className="ns-center" style={{ padding: "48px 0" }}>
          <span className="ns-spinner" aria-hidden />
        </div>
      </main>
    );
  }

  // Carrinho vazio (e sem pedido confirmado): nada a pagar — convida a voltar à loja.
  if (items.length === 0 && stage !== "confirmed") {
    return (
      <main className="ns-wrap ns-checkout">
        <div className="ns-panelcard ns-checkout-empty">
          <div className="big" aria-hidden>🛒</div>
          <h1>Your cart is empty</h1>
          <p className="ns-muted">Add a few products before heading to checkout.</p>
          <Link href="/" className="ns-btn-primary">Continue shopping</Link>
        </div>
      </main>
    );
  }

  if (!user) {
    return (
      <main className="ns-wrap ns-checkout">
        <h1 className="ns-checkout-title">Checkout</h1>
        <p className="ns-muted" style={{ margin: "0 0 20px", maxWidth: 520 }}>
          Sign in to place your order. Your cart is saved — you&apos;ll pick up right where you left off.
        </p>
        <AuthForms
          heroTitle="Sign in to checkout"
          heroBody="Orders are tied to your account so you can track status, tiers, and history."
          formTitle="Sign in to continue"
          formSub="Use an existing account or register — then you can complete payment."
        />
        <Link href="/" className="ns-link" style={{ display: "inline-block", marginTop: 20 }}>
          ← Back to store
        </Link>
      </main>
    );
  }

  return (
    <main className="ns-wrap ns-checkout">
      <h1 className="ns-checkout-title">Checkout</h1>

      {showSteps && (
        <div className="ns-steps" aria-hidden>
          {STEPS.map((label, i) => {
            const active = STEP_OF[stage];
            const cls = i < active ? "done" : i === active ? "on" : "";
            return (
              <div key={label} style={{ display: "contents" }}>
                {i > 0 && <span className="ns-step-sep" />}
                <span className={`ns-step ${cls}`}>
                  <span className="n">{i + 1}</span>
                  {label}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* CONFIRMATION — pedido persistido (id real, itens, total, status com severidade) */}
      {stage === "confirmed" && order ? (
        <div className="ns-panelcard ns-checkout-done">
          <div className="ns-alert success">
            <b>Thank you for your order!</b>
            <div style={{ marginTop: 4 }}>Order {order.id} is confirmed.</div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 10, margin: "16px 0" }}>
            <span className="ns-muted" style={{ fontSize: 13 }}>Status</span>
            <StatusPill status={order.status} />
          </div>
          {/* IA-Pedido (F-024): resumo de status em linguagem natural. */}
          <OrderStatusSummary orderId={order.id} />
          {/* IA-Notificação (F-031): prévia da copy de e-mail de confirmação. */}
          <NotificationPreview orderId={order.id} />
          {order.items.map((it) => (
            <div className="ns-sumrow" key={it.sku}>
              <span>
                {it.name} <span className="q">× {it.qty}</span>
              </span>
              <span>{formatMoney(it.price * it.qty)}</span>
            </div>
          ))}
          <div className="ns-total" style={{ marginTop: 12 }}>
            <span>Total</span>
            <span className="v">{formatMoney(order.total)}</span>
          </div>
          <Link href="/" className="ns-btn-primary block" style={{ marginTop: 18 }}>
            Continue shopping
          </Link>
        </div>
      ) : stage === "failed" ? (
        /* FAILED — pedido bloqueado (fraude/estoque) ou erro */
        <div className="ns-panelcard ns-checkout-done">
          <div className="ns-alert error">
            <b>We couldn’t place your order</b>
            <div style={{ marginTop: 4 }}>
              Something went wrong while completing your purchase. Please try again.
            </div>
          </div>
          {/* IA-Checkout (F-024): explicação amigável quando barrado por fraude (se aplicável) */}
          {order && <FraudExplain orderId={order.id} />}
          <div className="ns-btn-row" style={{ marginTop: 16 }}>
            <Link href="/" className="ns-btn-ghost block">Back to store</Link>
            <button type="button" className="ns-btn-primary block" onClick={() => setStage("payment")}>
              Try again
            </button>
          </div>
        </div>
      ) : (
        <div className="ns-checkout-grid">
          <section className="ns-panelcard">
            {/* DETAILS — dados do cliente */}
            {stage === "details" && (
              <>
                <h2 className="ns-card-title">Your details</h2>
                <Field label="Full name" value={customer.name} onChange={(v) => setCustomer({ ...customer, name: v })} placeholder="Jane Doe" />
                <Field label="Email" type="email" value={customer.email} onChange={(v) => setCustomer({ ...customer, email: v })} placeholder="jane@example.com" />

                {addressChoice === "saved" && user ? (
                  /* Endereço salvo no perfil: pré-preenchido, não pedimos de novo (F-011). */
                  <div className="ns-field">
                    <label className="ns-label">Shipping address</label>
                    <div className="ns-saved-address">
                      <span>{user.address}</span>
                      <button
                        type="button"
                        className="ns-link"
                        onClick={() => {
                          setAddressChoice("new");
                          setSaveToProfile(false); // usar outro endereço só desta vez por padrão
                        }}
                      >
                        Use a different address
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <Field
                      label="Shipping address"
                      value={customer.address}
                      onChange={(v) => setCustomer({ ...customer, address: v })}
                      placeholder="123 Main St, City"
                    />
                    {user && (
                      <label className="ns-check">
                        <input
                          type="checkbox"
                          checked={saveToProfile}
                          onChange={(e) => setSaveToProfile(e.target.checked)}
                        />
                        {user.address.trim() ? "Update my saved address" : "Save this address to my profile"}
                      </label>
                    )}
                    {user && user.address.trim() !== "" && (
                      <button
                        type="button"
                        className="ns-link"
                        style={{ marginTop: 4 }}
                        onClick={() => {
                          setAddressChoice("saved");
                          setCustomer((prev) => ({ ...prev, address: user.address }));
                        }}
                      >
                        Use my saved address
                      </button>
                    )}
                  </>
                )}

                {/* IA-Checkout (F-024): gerador opcional de mensagem de presente */}
                <GiftMessageField />

                <div className="ns-btn-row" style={{ marginTop: 18 }}>
                  <Link href="/" className="ns-btn-ghost block">Back to store</Link>
                  <button
                    type="button"
                    className="ns-btn-primary block"
                    disabled={!customerValid}
                    onClick={() => setStage("payment")}
                  >
                    Continue to payment
                  </button>
                </div>
              </>
            )}

            {/* PAYMENT — pagamento simulado (cartão fake, não enviado) */}
            {(stage === "payment" || stage === "placing") && (
              <>
                <h2 className="ns-card-title">Payment</h2>
                <div className="ns-alert" style={{ marginBottom: 16 }}>
                  Demo checkout — no real payment is processed.
                </div>
                <Field label="Card number" value={card.number} onChange={(v) => setCard({ ...card, number: v })} placeholder="4242 4242 4242 4242" />
                <div className="ns-row2">
                  <Field label="Expiry" value={card.expiry} onChange={(v) => setCard({ ...card, expiry: v })} placeholder="12/29" />
                  <Field label="CVC" value={card.cvc} onChange={(v) => setCard({ ...card, cvc: v })} placeholder="123" />
                </div>
                <div className="ns-btn-row" style={{ marginTop: 18 }}>
                  <button
                    type="button"
                    className="ns-btn-ghost block"
                    disabled={stage === "placing"}
                    onClick={() => setStage("details")}
                  >
                    Back
                  </button>
                  <button
                    type="button"
                    className="ns-btn-primary block"
                    disabled={!cardValid || stage === "placing"}
                    onClick={pay}
                  >
                    {stage === "placing" && <span className="ns-spinner" aria-hidden />}
                    {stage === "placing" ? "Processing…" : `Pay ${formatMoney(total)}`}
                  </button>
                </div>
              </>
            )}
          </section>

          {/* Resumo do pedido (sticky no desktop) */}
          <aside className="ns-panelcard ns-checkout-summary">
            <h2 className="ns-card-title">Order summary</h2>
            {items.map(({ product, qty }) => (
              <div className="ns-line" key={product.sku}>
                <div className="thumb" style={{ background: gradientOf(product.sku) }} aria-hidden>
                  {emojiOf(product)}
                </div>
                <div className="info">
                  <div className="nm">{product.name}</div>
                  <div className="pr">{formatMoney(product.price)} × {qty}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div className="sub">{formatMoney(product.price * qty)}</div>
                </div>
              </div>
            ))}
            <div className="ns-total" style={{ marginTop: 12 }}>
              <span>Total</span>
              <span className="v">{formatMoney(total)}</span>
            </div>
          </aside>
        </div>
      )}
    </main>
  );
}
