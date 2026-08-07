"use client";
// Returns/Refund Coordinator (F-029) — ponto de entrada na Conta, num pedido DELIVERED. Dispara
// a cadeia agêntica complexa (eligibility→policy→calc→abuse→process) que marca o pedido REFUNDED
// quando aprovado. Mostra só os passos + veredito. Indicador global AiThinking (F-028) enquanto
// roda. Estilizado por paletas.
import { useState } from "react";
import { Order, RefundResult, requestRefund } from "@/lib/api";
import AiThinking from "./AiThinking";

export default function ReturnRefund({ order, onRefunded }: { order: Order; onRefunded?: (o: Order) => void }) {
  const [result, setResult] = useState<RefundResult | null>(null);
  const [running, setRunning] = useState(false);
  const [failed, setFailed] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  if (order.status === "REFUNDED") {
    return (
      <div className="ns-refund ns-refund-done" role="status">
        <span className="ns-spark sm" aria-hidden>✦</span>
        <p>This order was refunded. The amount has been returned to your payment method.</p>
      </div>
    );
  }

  async function run() {
    if (running) return;
    setRunning(true);
    setResult(null);
    setFailed(false);
    setErrorMessage(null);
    try {
      const res = await requestRefund(order.id);
      setResult(res);
      if (res.refunded && onRefunded) onRefunded(res.order);
    } catch (err) {
      setFailed(true);
      const status = typeof err === "object" && err !== null && "status" in err
        ? Number((err as { status?: number }).status)
        : undefined;
      if (status === 401) {
        setErrorMessage("Please sign in to request a refund on this order.");
      } else if (status === 409) {
        setErrorMessage("Only delivered orders can be refunded.");
      } else if (status === 404) {
        setErrorMessage("We couldn't find this order. It may belong to another account.");
      } else {
        setErrorMessage("We couldn't process that right now. Please try again.");
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="ns-refund">
      <div className="ns-refund-head">
        <span className="ns-spark sm" aria-hidden>✦</span>
        <div>
          <p className="ns-refund-title">Need to return this?</p>
          <p className="ns-refund-sub">Our AI checks eligibility, policy and processes the refund.</p>
        </div>
        {!result && (
          <button type="button" className="ns-btn-ghost" onClick={run} disabled={running}>
            {running ? "Working…" : "Request refund"}
          </button>
        )}
      </div>

      {running && <AiThinking label="Reviewing your return request" />}

      {!running && failed && (
        <div className="ns-note" role="status">{errorMessage ?? "We couldn't process that right now. Please try again."}</div>
      )}

      {!running && result && (
        <div className="ns-refund-result">
          <p className={`ns-refund-verdict ${result.approved ? "ok" : "no"}`}>{result.reason}</p>
          <ul className="ns-refund-steps">
            {result.steps.map((s) => (
              <li key={s.label} className={s.ok ? "ok" : "no"}>
                <span className="ic" aria-hidden>{s.ok ? "✓" : "✕"}</span>
                <span className="bd">
                  <span className="lb">{s.label}</span>
                  <span className="dt">{s.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
