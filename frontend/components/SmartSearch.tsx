"use client";
// IA-Busca (F-022): busca em linguagem natural sobre a query atual da loja. Mapeia (no backend,
// via LLM, sem embeddings) p/ produtos do catálogo + interpretação + "você quis dizer". Aparece
// na área de resultados quando há busca; complementa o filtro instantâneo do header (caminho
// único: a barra de busca segue uma só; isto é a interpretação inteligente sob demanda).
import { useState } from "react";
import { Product, SemanticSearch, semanticSearch } from "@/lib/api";
import ProductCard from "./ProductCard";
import AiThinking from "./AiThinking";

export default function SmartSearch({
  query,
  onAdd,
  onApplySuggestion,
}: {
  query: string;
  onAdd: (p: Product) => void;
  onApplySuggestion: (q: string) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<SemanticSearch | null>(null);
  const [error, setError] = useState(false);

  async function run() {
    if (!query.trim() || loading) return;
    setLoading(true);
    setResult(null);
    setError(false);
    try {
      setResult(await semanticSearch(query));
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="ns-smart" aria-label="AI search">
      <div className="ns-smart-bar">
        <span className="ns-spark sm" aria-hidden>✦</span>
        <p className="ns-smart-lead">
          Looking for something specific? Let our AI interpret “{query}”.
        </p>
        <button type="button" className="ns-go" onClick={run} disabled={loading}>
          {loading ? "Searching…" : "Search with AI"}
        </button>
      </div>

      {loading && (
        <div style={{ marginTop: 14 }}>
          <AiThinking label="Interpreting your request" />
        </div>
      )}

      {!loading && error && (
        <div className="ns-note" role="status" style={{ marginTop: 14 }}>
          AI search isn’t available right now — your regular results are below.
        </div>
      )}

      {!loading && result && (
        <div className="ns-smart-result">
          {result.interpretation && <p className="ns-smart-interp">{result.interpretation}</p>}
          {result.suggestion && (
            <p className="ns-smart-dym">
              Did you mean{" "}
              <button type="button" className="ns-chip" onClick={() => onApplySuggestion(result.suggestion!)}>
                {result.suggestion}
              </button>
              ?
            </p>
          )}
          {result.products.length > 0 ? (
            <div className="ns-grid" style={{ marginTop: 14 }}>
              {result.products.map((p) => (
                <ProductCard
                  key={p.sku}
                  product={p}
                  onAdd={onAdd}
                />
              ))}
            </div>
          ) : (
            <div className="ns-note" role="status" style={{ marginTop: 6 }}>
              The AI couldn’t match that to our catalog. Try rephrasing your search.
            </div>
          )}
        </div>
      )}
    </section>
  );
}
