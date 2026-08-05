"use client";
// Store policies — leitura humana das políticas markdown (F-KNOWLEDGE-1).
import { useEffect, useState } from "react";
import { StorePolicy, getPolicies } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function PolicyBody({ markdown }: { markdown: string }) {
  const blocks = markdown.split(/\n(?=## )/).filter(Boolean);
  return (
    <article className="ns-policy-body">
      {blocks.map((block, i) => {
        const lines = block.trim().split("\n");
        const isTitle = lines[0]?.startsWith("# ");
        const isSection = lines[0]?.startsWith("## ");
        if (isTitle) {
          return (
            <h1 key={i} className="ns-policy-h1">
              {lines[0].slice(2).trim()}
            </h1>
          );
        }
        if (isSection) {
          const body = lines.slice(1).join("\n").trim();
          return (
            <section key={i} className="ns-policy-section">
              <h2 className="ns-policy-h2">{lines[0].slice(3).trim()}</h2>
              {body.split(/\n\n+/).map((para, j) => (
                <p key={j} className="ns-policy-p">
                  {para.replace(/\*\*(.+?)\*\*/g, "$1")}
                </p>
              ))}
            </section>
          );
        }
        return (
          <p key={i} className="ns-policy-p">
            {block.replace(/\*\*(.+?)\*\*/g, "$1")}
          </p>
        );
      })}
    </article>
  );
}

export default function PoliciesPage() {
  const { user } = useAuth();
  const [policies, setPolicies] = useState<StorePolicy[] | null>(null);
  const [active, setActive] = useState<string>("");
  const [error, setError] = useState(false);

  useEffect(() => {
    getPolicies()
      .then((list) => {
        setPolicies(list);
        if (list.length) setActive(list[0].slug);
      })
      .catch(() => setError(true));
  }, []);

  if (!user) {
    return (
      <div className="ns-account-page">
        <h1>Store policies</h1>
        <p className="ns-muted">Sign in to view store policies.</p>
      </div>
    );
  }

  const current = policies?.find((p) => p.slug === active) ?? policies?.[0];

  return (
    <div className="ns-account-page">
      <h1>Store policies</h1>
      <p className="ns-muted" style={{ marginBottom: 20 }}>
        Official shipping, returns, payment, privacy, and terms for Vega.
      </p>

      {error && (
        <div className="ns-alert error" role="alert">
          We couldn’t load policies. Please try again later.
        </div>
      )}

      {!policies && !error && <p className="ns-muted">Loading policies…</p>}

      {policies && policies.length > 0 && (
        <div className="ns-policy-layout">
          <nav className="ns-policy-nav" aria-label="Policy sections">
            {policies.map((p) => (
              <button
                key={p.slug}
                type="button"
                className={p.slug === current?.slug ? "on" : undefined}
                aria-current={p.slug === current?.slug ? "true" : undefined}
                onClick={() => setActive(p.slug)}
              >
                {p.title}
              </button>
            ))}
          </nav>
          <section className="ns-panelcard ns-policy-panel">
            {current && <PolicyBody markdown={current.markdown} />}
          </section>
        </div>
      )}
    </div>
  );
}
