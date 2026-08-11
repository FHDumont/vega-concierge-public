"use client";
// Checkout on its OWN PAGE (F-011) — used to be a continuation of the cart slide-over.
// Details → Payment → Confirmation flow in the custom design driven by palettes
// (ADR-012/013). Sends the REAL cart (items+qty) + customer to POST /api/orders, which
// creates/persists the order and goes through the pipeline (fraud/stock) — the "problems"
// break the flow visibly, WITHOUT technical data in the Store. Simulated payment: the card
// is NOT sent (no real gateway). Customer state comes from ShopProvider; session from AuthProvider.
import { useEffect, useState } from "react";
import Link from "next/link";
import { Customer, Order, createOrder, fraudExplain } from "@/lib/api";
import { emojiOf, formatMoney, gradientOf } from "@/lib/shop";
import { useShop } from "@/lib/store";
import { useAuth } from "@/lib/auth";
import StatusPill from "@/components/StatusPill";
import NotificationPreview from "@/components/NotificationPreview";
import AuthForms from "@/components/AuthForms";

type Stage = "details" | "payment" | "placing" | "confirmed" | "failed";

const EMPTY_CUSTOMER: Customer = { name: "", email: "", address: "" };

// Fictitious demo card (F-012) — prefilled in the Payment step; never sent to the backend.
const DEMO_CARD = { number: "4242 4242 4242 4242", expiry: "12/29", cvc: "123" };

const STEPS = ["Details", "Payment", "Confirmation"] as const;
// Maps each stage to the active step in the bar (Details → Payment → Confirmation).
const STEP_OF: Record<Stage, number> = {
  details: 0,
  payment: 1,
  placing: 1,
  failed: 1,
  confirmed: 2,
};

const CHECKOUT_FAILURE_COPY: Record<string, { title: string; body: string }> = {
  inventory_unavailable: {
    title: "Inventory service unavailable",
    body:
      "We couldn't verify stock — our inventory service returned error 503 while checking your cart. " +
      "Your card was not charged. This is a temporary outage on our side; please try again in a few minutes.",
  },
  fraud_blocked: {
    title: "Order held for review",
    body:
      "We couldn't complete your purchase because our fraud check flagged this order. " +
      "Your card was not charged. Contact support if you believe this is a mistake.",
  },
  payment_failed: {
    title: "Payment could not be processed",
    body:
      "Your bank or our payment gateway declined the charge. Nothing was captured — please verify your card and try again.",
  },
  out_of_stock: {
    title: "Item out of stock",
    body:
      "One or more items in your cart are no longer available at the quantity requested. " +
      "Update your cart and try again.",
  },
  unknown: {
    title: "We couldn't place your order",
    body: "Something went wrong while completing your purchase. Please try again.",
  },
};

function CheckoutFailureMessage({ order }: { order: Order | null }) {
  const key = order?.failure_reason || "unknown";
  const copy = CHECKOUT_FAILURE_COPY[key] || CHECKOUT_FAILURE_COPY.unknown;
  return (
    <div className="ns-alert error">
      <b>{copy.title}</b>
      <div style={{ marginTop: 4 }}>{copy.body}</div>
    </div>
  );
}

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

// AI-Checkout (F-024): friendly explanation when the order is blocked for fraud. Only appears
// when the backend flags `fraud` (fraud_false_positive toggle) — otherwise the failure stays generic.
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

  // Address (F-011): "saved" uses the profile's address (doesn't ask again); "new" asks and
  // offers to save it to the profile. `null` until the session resolves. Guest/no address → "new".
  const [addressChoice, setAddressChoice] = useState<"saved" | "new" | null>(null);
  const [saveToProfile, setSaveToProfile] = useState(true);

  // Prefills name/email/address from the logged-in user.
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
        // Saves the address to the profile if the customer opted in ("new" stage, logged in).
        if (user && addressChoice === "new" && saveToProfile && customer.address.trim()) {
          await saveAddress(customer.address).catch(() => {});
        }
        shop.clear(); // empty the cart on confirmation
        refresh(); // reflect the updated spend/tier
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

  // Empty cart (and no confirmed order): nothing to pay — invites you back to the store.
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

      {/* CONFIRMATION — persisted order (real id, items, total, status with severity) */}
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
        /* FAILED — order blocked (fraud/stock) or error */
        <div className="ns-panelcard ns-checkout-done">
          <CheckoutFailureMessage order={order} />
          {order?.failure_reason === "fraud_blocked" && order && <FraudExplain orderId={order.id} />}
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
            {/* DETAILS — customer data */}
            {stage === "details" && (
              <>
                <h2 className="ns-card-title">Your details</h2>
                <Field label="Full name" value={customer.name} onChange={(v) => setCustomer({ ...customer, name: v })} placeholder="Jane Doe" />
                <Field label="Email" type="email" value={customer.email} onChange={(v) => setCustomer({ ...customer, email: v })} placeholder="jane@example.com" />

                {addressChoice === "saved" && user ? (
                  /* Address saved in the profile: prefilled, we don't ask again (F-011). */
                  <div className="ns-field">
                    <label className="ns-label">Shipping address</label>
                    <div className="ns-saved-address">
                      <span>{user.address}</span>
                      <button
                        type="button"
                        className="ns-link"
                        onClick={() => {
                          setAddressChoice("new");
                          setSaveToProfile(false); // use a different address just this once by default
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

            {/* PAYMENT — simulated payment (fake card, not sent) */}
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

          {/* Order summary (sticky on desktop) */}
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
