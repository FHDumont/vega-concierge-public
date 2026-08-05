"use client";
// Banda do Concierge na vitrine — CTA abre o widget flutuante (F-051).
// Picks personalizados sob demanda via chip "Curate picks for me" (F-LLM-ON-DEMAND).
import { useEffect, useRef, useState } from "react";
import { Product, homePicks } from "@/lib/api";
import { useChat } from "@/lib/chat-context";
import ProductCard from "./ProductCard";

const GENERIC_CHIPS = [
  "Birthday gift under $300",
  "Something compact for travel",
  "Gift for a coffee lover",
];

export default function Concierge({
  initialRequest,
  onAdd,
  autoRun,
}: {
  initialRequest?: string;
  onAdd?: (p: Product) => void;
  autoRun?: boolean;
}) {
  const chat = useChat();
  const [request, setRequest] = useState(initialRequest ?? "");
  const seeded = useRef(false);

  const [picksProducts, setPicksProducts] = useState<Product[] | null>(null);
  const [picksBlurb, setPicksBlurb] = useState("");
  const [picksLoading, setPicksLoading] = useState(false);
  const [picksError, setPicksError] = useState(false);

  useEffect(() => {
    if (initialRequest) setRequest(initialRequest);
  }, [initialRequest]);

  useEffect(() => {
    if ((autoRun || initialRequest) && initialRequest && !seeded.current) {
      seeded.current = true;
      chat.openChat({ seed: initialRequest });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoRun, initialRequest]);

  function openWithSeed(seed?: string) {
    const text = (seed ?? request).trim();
    if (!text) return;
    chat.openChat({ seed: text });
  }

  async function loadPicks() {
    if (picksLoading || !onAdd) return;
    setPicksLoading(true);
    setPicksError(false);
    setPicksProducts(null);
    setPicksBlurb("");
    try {
      const r = await homePicks([]);
      setPicksProducts(r.products);
      setPicksBlurb(r.blurb);
    } catch {
      setPicksError(true);
    } finally {
      setPicksLoading(false);
    }
  }

  const showPicks = picksLoading || picksProducts !== null || picksError;

  return (
    <section className="ns-concierge" aria-label="AI concierge">
      <div className="ns-concierge-row">
        <div className="ns-spark" aria-hidden>✦</div>
        <div className="ns-ask">
          <input
            value={request}
            onChange={(e) => setRequest(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && openWithSeed()}
            placeholder="e.g. a birthday gift under $300 that arrives by Friday"
            aria-label="Describe what you're looking for"
          />
        </div>
        <button type="button" className="ns-go" onClick={() => openWithSeed()} disabled={!request.trim()}>
          Ask concierge
        </button>
      </div>

      <div className="ns-concierge-chips">
        {GENERIC_CHIPS.map((s) => (
          <button key={s} type="button" className="ns-chip" onClick={() => openWithSeed(s)}>
            {s}
          </button>
        ))}
        <button
          type="button"
          className="ns-chip"
          onClick={loadPicks}
          disabled={picksLoading || !onAdd}
        >
          Curate picks for me
        </button>
      </div>

      {showPicks && (
        <div className="ns-picks ns-concierge-picks">
          <div className="ns-picks-head">
            <span className="ns-spark sm" aria-hidden>✦</span>
            <div>
              <h2>Picked for you</h2>
              {picksBlurb ? <p className="ns-picks-blurb">{picksBlurb}</p> : null}
            </div>
          </div>

          {picksLoading ? (
            <div className="ns-grid">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="ns-skel ns-pulse">
                  <div className="tile" />
                  <div style={{ padding: 15, display: "flex", flexDirection: "column", gap: 10 }}>
                    <div className="bar" style={{ width: "75%" }} />
                    <div className="bar" style={{ width: "33%" }} />
                  </div>
                </div>
              ))}
            </div>
          ) : picksError || (picksProducts && picksProducts.length === 0) ? (
            <div className="ns-note" role="status">
              We couldn’t load picks right now. Please try again.
            </div>
          ) : picksProducts ? (
            <div className="ns-grid">
              {picksProducts.map((p) => (
                <ProductCard
                  key={p.sku}
                  product={p}
                  onAdd={onAdd!}
                />
              ))}
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
