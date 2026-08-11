"use client";
// STORE (customer experience) — modern e-commerce, WITHOUT technical data.
// Custom shell (ADR-012): hero, concierge band, and card grid,
// styled by palette variables (globals.css). Categories only in the header
// (ShopProvider shares search/category state between routes).
import { useEffect, useMemo, useState } from "react";
import { Product, getCatalog } from "@/lib/api";
import { inCategory } from "@/lib/shop";
import { useShop } from "@/lib/store";
import ProductCard from "@/components/ProductCard";
import Concierge from "@/components/Concierge";
import StoreFooter from "@/components/StoreFooter";

export default function Shop() {
  const shop = useShop();
  const [catalog, setCatalog] = useState<Product[] | null>(null);
  const [loadError, setLoadError] = useState(false);

  // "ask the concierge about this" shortcut coming from the detail page (/?ask=…).
  const [askPrefill, setAskPrefill] = useState<string | null>(null);

  useEffect(() => {
    getCatalog()
      .then(setCatalog)
      .catch(() => setLoadError(true));
  }, []);

  useEffect(() => {
    const ask = new URLSearchParams(window.location.search).get("ask");
    if (ask) setAskPrefill(ask);
  }, []);

  const visible = useMemo(() => {
    if (!catalog) return [];
    const q = shop.search.trim().toLowerCase();
    return catalog.filter(
      (p) =>
        inCategory(p, shop.category) &&
        (!q ||
          p.name.toLowerCase().includes(q) ||
          p.tags.some((t) => t.toLowerCase().includes(q))),
    );
  }, [catalog, shop.category, shop.search]);

  // "default" home = no category and no search. When filtering (category or search), the
  // storefront gets leaner: we hide the hero, promos, and the concierge band and show only
  // the listing (F-011). The floating launcher (step 4) covers concierge access on those screens.
  const filtering = shop.search.trim() !== "" || shop.category !== "All";
  const sectionTitle = filtering ? "Results" : "Popular right now";

  // Clears search AND category at once — returns to the home with hero/widgets (F-028).
  function clearFilters() {
    shop.setSearch("");
    shop.setCategory("All");
  }

  return (
    <>
      <main className="ns-wrap">
        {!filtering && (
          <>
            <section className="ns-hero">
              <div className="ns-hero-card">
                <h1>Find the perfect gift, faster.</h1>
                <p>Tell our AI concierge what you need and get curated picks in seconds.</p>
                <a className="ns-btn" href="#shop">Shop now →</a>
              </div>
              <div className="ns-hero-side">
                <div className="ns-promo accent">
                  <span className="k">This week</span>
                  <span className="v">Up to 30% off Audio</span>
                </div>
                <div className="ns-promo">
                  <span className="k">New arrivals</span>
                  <span className="v">Smart home, just landed</span>
                </div>
              </div>
            </section>

            <Concierge
              initialRequest={askPrefill ?? undefined}
              autoRun={askPrefill !== null}
            />
          </>
        )}

        {/* Clear exit from category/search navigation (F-028): Home › <filter> breadcrumb
            always visible while filtering, with a button to clear and return to the full home. */}
        {filtering && (
          <nav className="ns-crumbs" aria-label="Breadcrumb">
            <button type="button" className="ns-crumb-home" onClick={clearFilters}>
              ← Home
            </button>
            <span className="sep" aria-hidden>›</span>
            <span className="cur" aria-current="page">
              {shop.category !== "All" ? shop.category : "Search"}
              {shop.search.trim() !== "" ? `: “${shop.search.trim()}”` : ""}
            </span>
          </nav>
        )}

        <div className="ns-sec-head" id="shop">
          <h2>{sectionTitle}</h2>
          {filtering && (
            <button type="button" className="ns-chip" onClick={clearFilters}>
              All products
            </button>
          )}
        </div>

        {loadError ? (
          <div className="ns-note" role="alert">
            We couldn’t load the catalog. Please refresh the page.
          </div>
        ) : !catalog ? (
          <div className="ns-grid">
            {Array.from({ length: 12 }).map((_, i) => (
              <div key={i} className="ns-skel ns-pulse">
                <div className="tile" />
                <div style={{ padding: 15, display: "flex", flexDirection: "column", gap: 10 }}>
                  <div className="bar" style={{ width: "75%" }} />
                  <div className="bar" style={{ width: "33%" }} />
                </div>
              </div>
            ))}
          </div>
        ) : visible.length === 0 ? (
          <div className="ns-note" role="status">No products match your search.</div>
        ) : (
          <div className="ns-grid">
            {visible.map((p) => (
              <ProductCard
                key={p.sku}
                product={p}
                onAdd={shop.addToCart}
              />
            ))}
          </div>
        )}
      </main>
      <StoreFooter />
    </>
  );
}
