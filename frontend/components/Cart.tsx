"use client";
// Carrinho (slide-over) — visualização rápida do carrinho no design custom dirigido por
// paletas (ADR-012/013). Desde a F-011 o CHECKOUT vive em página própria (/checkout): o
// botão "Checkout" navega para lá (este painel não conduz mais o fluxo de pagamento).
// Estilizado pelas variáveis de paleta (globals.css › .ns-panel etc.). SEM @splunk/react-ui.
import { Product } from "@/lib/api";
import { CartItem, emojiOf, formatMoney, gradientOf } from "@/lib/shop";
import CartCrossSell from "./CartCrossSell";

export default function Cart({
  open,
  items,
  onClose,
  onSetQty,
  onRemove,
  onAdd,
  onCheckout,
}: {
  open: boolean;
  items: CartItem[];
  onClose: () => void;
  onSetQty: (sku: string, qty: number) => void;
  onRemove: (sku: string) => void;
  onAdd: (p: Product) => void; // adiciona uma sugestão de cross-sell (F-023)
  onCheckout: () => void; // navega para /checkout (F-011) e fecha o painel
}) {
  const total = items.reduce((s, i) => s + i.product.price * i.qty, 0);
  const count = items.reduce((s, i) => s + i.qty, 0);

  return (
    <div className="ns-overlay" aria-hidden={!open}>
      <div className="ns-backdrop" onClick={onClose} />
      <aside className="ns-panel" role="dialog" aria-label="Shopping cart" aria-modal={open}>
        <div className="ns-panel-head">
          <h2>
            Your cart
            {count > 0 && <span className="n"> ({count})</span>}
          </h2>
          <button type="button" className="ns-close" onClick={onClose} aria-label="Close cart">
            <span aria-hidden>✕</span>
          </button>
        </div>

        <div className="ns-panel-body">
          {items.length === 0 ? (
            <div className="ns-empty">
              <div>
                <div className="big" aria-hidden>
                  🛒
                </div>
                <p>Your cart is empty.</p>
              </div>
            </div>
          ) : (
            items.map(({ product, qty }) => (
              <div className="ns-line" key={product.sku}>
                <div className="thumb" style={{ background: gradientOf(product.sku) }} aria-hidden>
                  {emojiOf(product)}
                </div>
                <div className="info">
                  <div className="nm">{product.name}</div>
                  <div className="pr">{formatMoney(product.price)}</div>
                  <div className="ns-qty">
                    <button
                      type="button"
                      onClick={() => onSetQty(product.sku, qty - 1)}
                      aria-label={`Decrease ${product.name}`}
                    >
                      −
                    </button>
                    <span className="v">{qty}</span>
                    <button
                      type="button"
                      onClick={() => onSetQty(product.sku, qty + 1)}
                      aria-label={`Increase ${product.name}`}
                    >
                      +
                    </button>
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div className="sub">{formatMoney(product.price * qty)}</div>
                  <button type="button" className="ns-remove" onClick={() => onRemove(product.sku)}>
                    Remove
                  </button>
                </div>
              </div>
            ))
          )}

          {items.length > 0 && (
            <CartCrossSell skus={items.map((i) => i.product.sku)} onAdd={onAdd} />
          )}
        </div>

        {items.length > 0 && (
          <div className="ns-panel-foot">
            <div className="ns-total">
              <span>Total</span>
              <span className="v">{formatMoney(total)}</span>
            </div>
            <button type="button" className="ns-btn-primary block" onClick={onCheckout}>
              Checkout
            </button>
          </div>
        )}
      </aside>
    </div>
  );
}
