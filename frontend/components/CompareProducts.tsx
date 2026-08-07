"use client";
// Compare 2 produtos (F-029) — ponto de entrada na página de produto. O cliente escolhe outro
// produto e a IA orquestra a comparação. Mostra veredito estruturado (facts + bullets) como no chat.
import { useEffect, useState } from "react";
import { AnswerLayoutBlock, AnswerLayout, hasLayoutContent } from "@/components/AnswerLayout";
import { CompareResult, Product, compareProducts, getCatalog } from "@/lib/api";
import { formatMoney } from "@/lib/shop";
import AiThinking from "./AiThinking";

export default function CompareProducts({ sku, name }: { sku: string; name: string }) {
  const [others, setOthers] = useState<Product[]>([]);
  const [pick, setPick] = useState("");
  const [result, setResult] = useState<CompareResult | null>(null);
  const [running, setRunning] = useState(false);
  const [failed, setFailed] = useState(false);

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
    if (!others.some((p) => p.sku === pick)) return;
    setRunning(true);
    setResult(null);
    setFailed(false);
    try {
      setResult(await compareProducts(sku, pick));
    } catch {
      setFailed(true);
    } finally {
      setRunning(false);
    }
  }

  const layout = result?.layout as AnswerLayout | undefined;

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

      {(running || result || failed) && (
        <div className="ns-pai-answer">
          {running && <AiThinking label={`Comparing the ${name}`} />}
          {!running && result && (
            <div className="ns-chat-artifact ns-chat-compare">
              <div className="ns-chat-compare-grid">
                <div className="ns-chat-mini-card">
                  <b>{result.product_a.name}</b>
                  <span>{formatMoney(result.product_a.price)}</span>
                </div>
                <div className="ns-chat-mini-card">
                  <b>{result.product_b.name}</b>
                  <span>{formatMoney(result.product_b.price)}</span>
                </div>
              </div>
              <AnswerLayoutBlock layout={layout} />
              {!hasLayoutContent(layout) && result.verdict && (
                <p className="ns-chat-verdict">{result.verdict}</p>
              )}
            </div>
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
