"use client";
// LLM INSPECTOR — tela do DONO (owner-only) p/ ver a atividade de LLM (F-023, ADR-017).
// Lupa LOCAL de debug que mostra o conteúdo completo (system/user prompt + resposta) +
// metadados (modelo/provider/tokens/cache/latência).
// Gateada por papel (só `role === "OWNER"`); o backend é a fronteira real (401/403). Desligável
// (toggle que pausa a captura no backend; vira feature flag de verdade na F-025).
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import {
  LLMActivity, LLMActivityEntry,
  getLLMActivity, setLLMInspectorEnabled, clearLLMActivity,
} from "@/lib/api";
import FlagGuard from "@/components/FlagGuard";

// F-033: a flag `inspector` é o "desligável" do F-023 — esconde a superfície + barra a rota
// (o owner passa; ADR-021). A captura/pausa no backend respeita a mesma flag efetiva.
export default function LLMActivityPage() {
  return (
    <FlagGuard flag="inspector">
      <LLMActivityGate />
    </FlagGuard>
  );
}

function LLMActivityGate() {
  const { user, ready } = useAuth();
  if (!ready) return <Gate msg="Loading…" />;
  if (!user) return <Gate msg="Sign in as the owner to inspect LLM activity." cta />;
  if (user.role !== "OWNER")
    return <Gate msg="Owner only — you don’t have access to the LLM inspector." />;
  return <Inspector />;
}

// Borda fora da permissão: mantém o chrome, sem expor nada.
function Gate({ msg, cta }: { msg: string; cta?: boolean }) {
  return (
    <>
      <div className="ns-adm-wrap">
        <div className="ns-adm-top">
          <div>
            <h1>LLM Inspector</h1>
            <p className="sub">Local view of what each LLM call ran — owner only.</p>
          </div>
        </div>
        <div className="ns-adm-card">
          <p className="ns-adm-empty">{msg}</p>
          {cta && (
            <p style={{ marginTop: 10 }}>
              <a className="ns-adm-btn primary" href="/account">Go to sign in</a>
            </p>
          )}
        </div>
      </div>
    </>
  );
}

function Inspector() {
  const [data, setData] = useState<LLMActivity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  // Paginação no cliente (F-030): a captura já chega inteira (até `data.max`);
  // paginamos no front p/ não renderizar todas as linhas de uma vez.
  const [pageSize, setPageSize] = useState(25);
  const [page, setPage] = useState(1);

  const load = useCallback(async () => {
    setError(null);
    try { setData(await getLLMActivity()); }
    catch (e) { setError((e as Error).message); setData((d) => d); }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function toggle() {
    if (!data) return;
    setBusy(true); setError(null);
    try {
      const enabled = await setLLMInspectorEnabled(!data.enabled);
      setData((d) => (d ? { ...d, enabled } : d));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function clear() {
    setBusy(true); setError(null); setConfirmClear(false);
    try { await clearLLMActivity(); await load(); setExpanded(null); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  const entries = data?.entries ?? [];
  const totalPages = Math.max(1, Math.ceil(entries.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const start = (safePage - 1) * pageSize;
  const slice = entries.slice(start, start + pageSize);

  return (
    <>
      <div className="ns-adm-wrap">
        <div className="ns-adm-top">
          <div>
            <h1>LLM Inspector</h1>
            <p className="sub">
              What each LLM call actually ran — prompts, response, model, tokens, cache, latency.
              Local to this machine.
            </p>
          </div>
          <div className="ns-adm-actions">
            {error && <span className="ns-adm-note" style={{ color: "var(--sev-critical)" }}>{error}</span>}
            <label className="ns-cfg-switch" title={data?.enabled ? "Capturing" : "Paused"}>
              <input type="checkbox" checked={!!data?.enabled} onChange={toggle} disabled={busy || !data} />
              <span>{data?.enabled ? "Capture on" : "Capture off"}</span>
            </label>
            <button type="button" className="ns-adm-btn" onClick={load} disabled={busy}>Refresh</button>
            {confirmClear ? (
              <span className="ns-adm-confirm">
                Clear all?
                <button type="button" className="ns-adm-btn danger" onClick={clear} disabled={busy}>Yes</button>
                <button type="button" className="ns-adm-btn" onClick={() => setConfirmClear(false)} disabled={busy}>No</button>
              </span>
            ) : (
              <button type="button" className="ns-adm-btn danger" onClick={() => setConfirmClear(true)}
                disabled={busy || entries.length === 0}>Clear</button>
            )}
          </div>
        </div>

        {data && !data.enabled && (
          <div className="ns-adm-card" style={{ marginBottom: 14 }}>
            <p className="ns-adm-empty">
              Capture is <b>off</b> — no new calls are being recorded. Existing entries are kept until cleared.
              Turn capture on to resume.
            </p>
          </div>
        )}

        {data === null ? (
          <div className="ns-adm-empty">Loading activity…</div>
        ) : entries.length === 0 ? (
          <div className="ns-adm-card">
            <p className="ns-adm-empty">
              No LLM activity recorded yet. Use the concierge or a store AI feature (product Q&amp;A,
              search, picks) and refresh — the last {data.max} calls show here.
            </p>
          </div>
        ) : (
          <div className="ns-llm">
            <div className="ns-llm-count">
              Showing {entries.length} of last {data.max} calls (most recent first).
            </div>
            <div className="ns-llm-table" role="table" aria-label="LLM activity">
              <div className="ns-llm-row head" role="row">
                <span role="columnheader">Time</span>
                <span role="columnheader">Feature</span>
                <span role="columnheader">Model</span>
                <span role="columnheader">Tokens</span>
                <span role="columnheader">Cache</span>
                <span role="columnheader">Latency</span>
                <span role="columnheader" aria-label="Expand" />
              </div>
              {slice.map((e) => (
                <ActivityRow key={e.id} e={e} open={expanded === e.id}
                  onToggle={() => setExpanded((id) => (id === e.id ? null : e.id))} />
              ))}
            </div>

            <div className="ns-adm-pager">
              <label className="size">
                Per page
                <select
                  value={pageSize}
                  onChange={(ev) => { setPageSize(Number(ev.target.value)); setPage(1); }}
                >
                  {[10, 25, 50].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </label>
              <span className="range">
                {start + 1}–{Math.min(start + pageSize, entries.length)} of {entries.length}
              </span>
              <div className="nav">
                <button type="button" className="ns-adm-btn" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>
                  ← Prev
                </button>
                <span className="page">Page {safePage} of {totalPages}</span>
                <button type="button" className="ns-adm-btn" disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>
                  Next →
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

function timeOf(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleTimeString();
}

function cacheClass(cache: string | null): string {
  if (cache === "hit") return "hit";
  if (cache === "rate_limited") return "warn";
  return "";
}

function ActivityRow({ e, open, onToggle }: {
  e: LLMActivityEntry; open: boolean; onToggle: () => void;
}) {
  return (
    <>
      <div className={`ns-llm-row ${open ? "open" : ""}`} role="row" onClick={onToggle}>
        <span role="cell" className="mono">{timeOf(e.ts)}</span>
        <span role="cell"><b>{e.feature}</b></span>
        <span role="cell" className="ns-llm-model">
          {e.model}
          <em>{e.provider}{e.fallback ? " · fallback" : ""}</em>
        </span>
        <span role="cell" className="mono" title={
          (e.prompt_cache_tokens ?? 0) > 0
            ? `${e.input_tokens} in / ${e.output_tokens} out · ${e.prompt_cache_tokens} prompt-cache`
            : undefined
        }>
          {e.input_tokens} / {e.output_tokens}
          {(e.prompt_cache_tokens ?? 0) > 0 ? ` · c${e.prompt_cache_tokens}` : ""}
        </span>
        <span role="cell">
          {e.cache
            ? <span className={`ns-llm-cache ${cacheClass(e.cache)}`}>{e.cache}</span>
            : <span className="ns-llm-cache none">—</span>}
        </span>
        <span role="cell" className="mono">{e.latency_ms} ms</span>
        <span role="cell" className="ns-llm-exp" aria-hidden>{open ? "▾" : "▸"}</span>
      </div>
      {open && (
        <div className="ns-llm-detail" role="row">
          <div className="ns-llm-meta">
            <span><b>System</b> {e.family}</span>
            <span><b>Provider</b> {e.provider}</span>
            {(e.prompt_cache_tokens ?? 0) > 0 && (
              <span><b>Prompt cache</b> {e.prompt_cache_tokens} tok</span>
            )}
          </div>
          <Field label="System prompt" value={e.system} />
          <Field label="User prompt" value={e.prompt} />
          <Field label="Response" value={e.response} />
        </div>
      )}
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="ns-llm-field">
      <div className="ns-llm-flabel">{label}</div>
      <pre className="ns-llm-pre">{value || <span className="ns-cfg-nokey">(empty)</span>}</pre>
    </div>
  );
}
