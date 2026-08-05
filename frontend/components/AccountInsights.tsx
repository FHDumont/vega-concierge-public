"use client";
// IA-Conta (F-031): insights do histórico de compras + explicação dos benefícios do tier +
// sugestão de recompra, a partir dos dados REAIS do usuário (pedidos/tier). Aparece na página
// da Conta. Mostra só o conteúdo gerado. Backend resolve user/pedidos (grounding real) e honra
// os toggles; offline → fallback gracioso. Indicador AiThinking (F-028). Estilizado pelas
// variáveis de paleta.
import { useEffect, useState } from "react";
import { AccountInsights as Insights, accountInsights } from "@/lib/api";
import AiThinking from "@/components/AiThinking";

export default function AccountInsights() {
  const [data, setData] = useState<Insights | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setFailed(false);
    accountInsights()
      .then((d) => alive && setData(d))
      .catch(() => alive && setFailed(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  if (failed) return null; // silencioso: a conta já mostra perfil/tier/histórico

  return (
    <section className="ns-panelcard">
      <h2 className="ns-card-title">
        <span className="ns-spark sm" aria-hidden>✦</span> Your Vega insights
      </h2>
      {loading ? (
        <AiThinking label="Reviewing your history…" />
      ) : data ? (
        <div className="ns-acct-ai">
          <p>{data.summary}</p>
          <div className="ns-acct-ai-row">
            <span className="lbl">🏆 Tier</span>
            <p>{data.tier_benefits}</p>
          </div>
          <div className="ns-acct-ai-row">
            <span className="lbl">🔁 Buy again</span>
            <p>{data.repurchase}</p>
          </div>
        </div>
      ) : null}
    </section>
  );
}
