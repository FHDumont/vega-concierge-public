"use client";
// Cart AI (F-023): cross-sell/bundle inside the cart slide-over. "Complete your purchase"
// with suggestions generated from the cart items (on the backend, reuses the F-022 cost layer
// + honors the toggles). Opt-in: LLM only on click (F-LLM-ON-DEMAND).
import { useEffect, useState } from "react";
import { Product, cartCrossSell } from "@/lib/api";
import { emojiOf, formatMoney, gradientOf } from "@/lib/shop";
import AiThinking from "./AiThinking";

type XSellState = "idle" | "loading" | "loaded";

export default function CartCrossSell({
  skus,
  onAdd,
}: {
  skus: string[];
  onAdd: (p: Product) => void;
}) {
  const [state, setState] = useState<XSellState>("idle");
  const [products, setProducts] = useState<Product[]>([]);
  const [blurb, setBlurb] = useState("");

  useEffect(() => {
    if (skus.length === 0) {
      setState("idle");
      setProducts([]);
      setBlurb("");
    }
  }, [skus.length]);

  async function loadSuggestions() {
    if (skus.length === 0 || state === "loading") return;
    setState("loading");
    try {
      const r = await cartCrossSell(skus);
      setProducts(r.products);
      setBlurb(r.blurb);
      setState("loaded");
    } catch {
      setProducts([]);
      setState("loaded");
    }
  }

  if (skus.length === 0) return null;

  if (state === "idle") {
    return (
      <div className="ns-xsell" aria-label="Complete your purchase">
        <div className="ns-xsell-head">
          <span className="ns-spark sm" aria-hidden>✦</span>
          <span className="ns-xsell-title">Complete your purchase</span>
        </div>
        <p className="ns-xsell-blurb">
          Our AI can suggest items that pair well with what&apos;s in your cart. Nothing loads until
          you tap the button below.
        </p>
        <button type="button" className="ns-xsell-cta" onClick={loadSuggestions}>
          ✦ Suggest add-ons
        </button>
      </div>
    );
  }

  if (state === "loading") {
    return (
      <div className="ns-xsell" aria-label="Complete your purchase">
        <div className="ns-xsell-head">
          <span className="ns-spark sm" aria-hidden>✦</span>
          <span className="ns-xsell-title">Complete your purchase</span>
        </div>
        <AiThinking className="block" label="Finding items that go well together" />
      </div>
    );
  }

  if (products.length === 0) return null;

  return (
    <div className="ns-xsell" aria-label="Complete your purchase">
      <div className="ns-xsell-head">
        <span className="ns-spark sm" aria-hidden>✦</span>
        <span className="ns-xsell-title">Complete your purchase</span>
      </div>
      {blurb ? <p className="ns-xsell-blurb">{blurb}</p> : null}
      <div className="ns-xsell-list">
        {products.map((p) => (
          <div className="ns-xsell-item" key={p.sku}>
            <div className="thumb" style={{ background: gradientOf(p.sku) }} aria-hidden>
              {emojiOf(p)}
            </div>
            <div className="info">
              <div className="nm">{p.name}</div>
              <div className="pr">{formatMoney(p.price)}</div>
            </div>
            <button
              type="button"
              className="ns-xsell-add"
              onClick={() => onAdd(p)}
              aria-label={`Add ${p.name} to cart`}
            >
              Add +
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
