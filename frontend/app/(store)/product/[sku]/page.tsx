"use client";
// Product detail page — custom design (ADR-012). Reuses GET /api/catalog
// (small, in-memory catalog) and filters by SKU on the client — no new endpoint (F-004).
// add-to-cart uses the shared state (ShopProvider); shortcut leads to the concierge on the home.
// Styled by the palette variables (globals.css). WITHOUT @splunk/react-ui.
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { Product, getCatalog } from "@/lib/api";
import { categoriesOf, emojiOf, formatMoney, gradientOf, ratingOf, stockState } from "@/lib/shop";
import { useShop } from "@/lib/store";
import ProductAI from "@/components/ProductAI";
import CompareProducts from "@/components/CompareProducts";
import StoreFooter from "@/components/StoreFooter";

function Shell({ children }: { children: React.ReactNode }) {
  return <main className="ns-wrap">{children}</main>;
}

// Next 15+/16: `params` is a Promise; unwrapped with React.use() (F-036).
export default function ProductDetail({ params }: { params: Promise<{ sku: string }> }) {
  const { sku } = use(params);
  const shop = useShop();
  const [product, setProduct] = useState<Product | null | undefined>(undefined); // undefined=loading, null=not found
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    getCatalog()
      .then((catalog) => setProduct(catalog.find((p) => p.sku === sku) ?? null))
      .catch(() => setLoadError(true));
  }, [sku]);

  if (loadError) {
    return (
      <Shell>
        <Link href="/" className="ns-back">← Back to the store</Link>
        <div className="ns-note" role="alert">We couldn’t load this product. Please try again.</div>
      </Shell>
    );
  }

  if (product === undefined) {
    return (
      <Shell>
        <Link href="/" className="ns-back">← Back to the store</Link>
        <div className="ns-detail">
          <div className="ns-skel ns-pulse" style={{ aspectRatio: "1 / 1", borderRadius: 24 }} />
          <div style={{ display: "flex", flexDirection: "column", gap: 14, paddingTop: 8 }}>
            <div className="ns-skel ns-pulse bar" style={{ height: 28, width: "66%" }} />
            <div className="ns-skel ns-pulse bar" style={{ height: 20, width: "25%" }} />
            <div className="ns-skel ns-pulse bar" style={{ height: 90, width: "100%" }} />
          </div>
        </div>
      </Shell>
    );
  }

  if (product === null) {
    return (
      <Shell>
        <Link href="/" className="ns-back">← Back to the store</Link>
        <div className="ns-note" role="status">
          We couldn’t find that product. <Link href="/" style={{ color: "var(--accent)" }}>Back to the store</Link>
        </div>
      </Shell>
    );
  }

  const rating = ratingOf(product.sku);
  const cats = categoriesOf(product);
  const stock = stockState(product);
  const outOfStock = stock === "out";

  return (
    <>
      <Shell>
        <Link href="/" className="ns-back">← Back to the store</Link>
        <div className="ns-detail">
          <div className="ns-detail-img" style={{ background: gradientOf(product.sku) }}>
            <span className="ns-detail-glyph" aria-hidden>{emojiOf(product)}</span>
          </div>

          <div className="ns-detail-info">
            <div className="ns-chips-row">
              {cats.map((c) => (
                <span key={c} className="ns-tag">{c}</span>
              ))}
            </div>
            <h1>{product.name}</h1>
            <div className="rate" style={{ marginTop: 10, display: "flex", alignItems: "center", gap: 10, color: "var(--muted)", fontSize: 14 }}>
              <span><span className="ns-star" aria-hidden>★</span> {rating.toFixed(1)}</span>
              {stock === "low" && <span className="ns-stock low">Only a few left</span>}
              {stock === "out" && <span className="ns-stock out">Out of stock</span>}
            </div>
            <div className="ns-detail-price">{formatMoney(product.price)}</div>
            {product.description ? (
              <section className="ns-detail-about" aria-label="About this product">
                <h2 className="ns-detail-about-title">About this product</h2>
                <p className="ns-detail-desc">{product.description}</p>
              </section>
            ) : null}

            <div className="ns-detail-actions">
              <button
                type="button"
                className="ns-btn-primary"
                disabled={outOfStock}
                onClick={() => shop.addToCart(product)}
              >
                {outOfStock ? "Sold out" : "Add to cart"}
              </button>
            </div>
          </div>
        </div>

        <ProductAI sku={product.sku} name={product.name} tags={product.tags} />
        <CompareProducts sku={product.sku} name={product.name} />
      </Shell>
      <StoreFooter />
    </>
  );
}
