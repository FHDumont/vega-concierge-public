"use client";
// IA-Pedido (F-024): resumo de status do pedido em linguagem natural. Aparece na confirmação
// do checkout e no detalhe do histórico. Mostra só o conteúdo gerado. Backend resolve a ordem
// (grounding real) e honra os toggles; offline → fallback gracioso. Estilizado pelas variáveis
// de paleta.
import { useEffect, useState } from "react";
import { orderSummary } from "@/lib/api";
import { dedupedFetch } from "@/lib/requestDedup";

export default function OrderStatusSummary({ orderId }: { orderId: string }) {
  const [summary, setSummary] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setSummary(null);
    setFailed(false);
    dedupedFetch(`order-summary:${orderId}`, () => orderSummary(orderId))
      .then((d) => alive && setSummary(d.summary))
      .catch(() => alive && setFailed(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [orderId]);

  if (failed) return null; // silencioso: o status já está visível na timeline/pill

  return (
    <div className="ns-aisum" aria-label="Order status summary">
      <span className="ns-spark sm" aria-hidden>✦</span>
      {loading ? (
        <span className="ns-skel ns-pulse bar" style={{ height: 14, width: "70%" }} />
      ) : (
        <p>{summary}</p>
      )}
    </div>
  );
}
