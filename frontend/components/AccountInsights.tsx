"use client";
// Account AI (F-031): purchase history insights — opt-in via button (F-WORKSHOP-SURFACE-1).
import { useState } from "react";
import { AccountInsights as Insights, accountInsights } from "@/lib/api";
import AiThinking from "./AiThinking";

export default function AccountInsights() {
  const [data, setData] = useState<Insights | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [requested, setRequested] = useState(false);

  async function load() {
    setRequested(true);
    setLoading(true);
    setFailed(false);
    try {
      setData(await accountInsights());
    } catch {
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="ns-panelcard">
      <h2 className="ns-card-title">
        <span className="ns-spark sm" aria-hidden>✦</span> Your Vega insights
      </h2>
      <p className="ns-muted" style={{ margin: "0 0 14px", fontSize: 14 }}>
        AI reads your purchase history and tier to suggest benefits and repurchase ideas — optional, on demand.
      </p>
      {!requested && (
        <button type="button" className="ns-btn-ghost" onClick={load}>
          Show my insights
        </button>
      )}
      {requested && loading && <AiThinking label="Reviewing your history…" />}
      {requested && failed && (
        <div className="ns-note" role="status">We couldn’t load insights right now. Please try again.</div>
      )}
      {requested && !loading && data && (
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
      )}
    </section>
  );
}
