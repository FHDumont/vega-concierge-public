"use client";
// Compare 2 produtos (F-029) — ponto de entrada na página de produto. O cliente escolhe outro
// produto e a IA orquestra a comparação: Compare Coordinator (agente) busca os 2 produtos via
// tool real e o Comparator (agente) gera o veredito exibido. Mostra só o conteúdo. Estilizado
// pelas variáveis de paleta. Indicador global AiThinking (F-028) enquanto a comparação roda.
import { useEffect, useState } from "react";
import { Product, compareProducts, getCatalog } from "@/lib/api";
import { formatMoney } from "@/lib/shop";
import AiThinking from "./AiThinking";

export default function CompareProducts({ sku, name }: { sku: string; name: string }) {
  const [others, setOthers] = useState<Product[]>([]);
  const [pick, setPick] = useState("");
  const [verdict, setVerdict] = useState<{ text: string; other: Product } | null>(null);
  const [running, setRunning] = useState(false);
  const [failed, setFailed] = useState(false);

  // Catálogo p/ o seletor (exclui o produto atual). Reusa GET /api/catalog (sem endpoint novo).
  useEffect(() => {
    let alive = true;
    getCatalog()
      .then((c) => alive && setOthers(c.filter((p) => p.sku !== sku)))
      .catch(() => alive && setOthers([]));
    return () => {
      alive = false;
    };
  }, [sku]);

  async function run() {
    if (!pick || running) return;
    const other = others.find((p) => p.sku === pick);
    if (!other) return;
    setRunning(true);
    setVerdict(null);
    setFailed(false);
    try {
      const res = await compareProducts(sku, pick);
      setVerdict({ text: res.verdict, other });
    } catch {
      setFailed(true);
    } finally {
      setRunning(false);
    }
  }

  return (
    <section className="ns-pai ns-compare" aria-label="Compare with another product">
      <div className="ns-pai-head">
        <span className="ns-spark sm" aria-hidden>✦</span>
        <h2>Compare with another product</h2>
      </div>

      <div className="ns-compare-pick">
        <select
          className="ns-input"
          value={pick}
          onChange={(e) => setPick(e.target.value)}
          aria-label="Pick a product to compare with"
        >
          <option value="">Choose a product…</option>
          {others.map((p) => (
            <option key={p.sku} value={p.sku}>
              {p.name} — {formatMoney(p.price)}
            </option>
          ))}
        </select>
        <button type="button" className="ns-go" onClick={run} disabled={!pick || running}>
          {running ? "Comparing…" : "Compare"}
        </button>
      </div>

      {(running || verdict || failed) && (
        <div className="ns-pai-answer">
          {running && <AiThinking label={`Comparing the ${name}`} />}
          {!running && verdict && (
            <>
              <p className="ns-compare-vs">
                <b>{name}</b> vs <b>{verdict.other.name}</b>
              </p>
              <p>{verdict.text}</p>
            </>
          )}
          {!running && failed && (
            <div className="ns-note" role="status">
              We couldn’t compare those right now. Please try again.
            </div>
          )}
        </div>
      )}
    </section>
  );
}
