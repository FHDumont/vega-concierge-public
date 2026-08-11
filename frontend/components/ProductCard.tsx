"use client";
// Product card — custom design (ADR-012): gradient placeholder + emoji, rating,
// stock severity, price, and add-to-cart. Image + name lead to the detail page
// (/product/[sku]); buttons don't navigate. Styled via the palette variables
// (globals.css › .ns-card). NO @splunk/react-ui.
import Link from "next/link";
import { Product } from "@/lib/api";
import { emojiOf, formatMoney, gradientOf, ratingOf, stockState } from "@/lib/shop";

export default function ProductCard({
  product,
  highlight,
  onAdd,
}: {
  product: Product;
  highlight?: boolean;
  onAdd: (p: Product) => void;
}) {
  const rating = ratingOf(product.sku);
  const stock = stockState(product);
  const outOfStock = stock === "out";

  return (
    <article className={`ns-card${highlight ? " hl" : ""}`}>
      {highlight && <span className="ns-reco">Recommended</span>}
      <Link
        href={`/product/${product.sku}`}
        className="ns-ph"
        style={{ background: gradientOf(product.sku) }}
        aria-label={`View ${product.name}`}
      >
        <span aria-hidden>{emojiOf(product)}</span>
      </Link>

      <div className="body">
        <Link href={`/product/${product.sku}`} className="name">
          {product.name}
        </Link>
        <div className="rate">
          <span>
            <span className="ns-star" aria-hidden>★</span> {rating.toFixed(1)}
          </span>
          {stock === "low" && <span className="ns-stock low">Low stock</span>}
          {stock === "out" && <span className="ns-stock out">Out of stock</span>}
        </div>
        <div className="row">
          <span className="ns-price">{formatMoney(product.price)}</span>
          <button
            type="button"
            className="ns-add"
            disabled={outOfStock}
            onClick={() => onAdd(product)}
          >
            {outOfStock ? "Sold out" : "Add +"}
          </button>
        </div>
      </div>
    </article>
  );
}
