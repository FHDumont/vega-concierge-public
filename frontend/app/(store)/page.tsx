"use client";
// LOJA (experiência do cliente) — e-commerce moderno, SEM dados técnicos.
// Shell custom (ADR-012): hero, banda do concierge e grade de cards,
// estilizado por variáveis de paleta (globals.css). Categorias só no header
// (ShopProvider compartilha estado de busca/categoria entre rotas).
import { useEffect, useMemo, useState } from "react";
import { Product, getCatalog } from "@/lib/api";
import { inCategory } from "@/lib/shop";
import { useShop } from "@/lib/store";
import ProductCard from "@/components/ProductCard";
import Concierge from "@/components/Concierge";
import SmartSearch from "@/components/SmartSearch";
import StoreFooter from "@/components/StoreFooter";

export default function Shop() {
  const shop = useShop();
  const [catalog, setCatalog] = useState<Product[] | null>(null);
  const [loadError, setLoadError] = useState(false);

  // Atalho "ask the concierge about this" vindo da página de detalhe (/?ask=…).
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

  // Home "default" = sem categoria e sem busca. Ao filtrar (categoria ou busca), a vitrine
  // fica enxuta: escondemos hero, promos e a banda do concierge e mostramos só a listagem
  // (F-011). O launcher flutuante (etapa 4) cobre o acesso ao concierge nessas telas.
  const filtering = shop.search.trim() !== "" || shop.category !== "All";
  const sectionTitle = filtering ? "Results" : "Popular right now";

  // Limpa busca E categoria de uma vez — volta à home com hero/widgets (F-028).
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
              onAdd={shop.addToCart}
              initialRequest={askPrefill ?? undefined}
              autoRun={askPrefill !== null}
            />
          </>
        )}

        {shop.search.trim() !== "" && (
          <SmartSearch
            query={shop.search.trim()}
            onAdd={shop.addToCart}
            onApplySuggestion={shop.setSearch}
          />
        )}

        {/* Saída clara da navegação por categoria/busca (F-028): breadcrumb Home › <filtro>
            sempre visível ao filtrar, com botão para limpar e voltar à home completa. */}
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
