"use client";
// CONNECTION / HUB — OWNER-only screen. Separate from the LLM config (F-026 UX): this is about
// "WHERE the config comes from" (independent/local or a hub's client) and "serving as a hub"
// (token + connected clients). Owner-gated in the UI; the backend is the real boundary (401/403).
// The clients table is full-width with filter + pagination + connected-since/last-seen (scenario
// with many participants). Tokens never reach the frontend (has_* flags).
import { useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "@/lib/auth";
import RumCard from "@/components/RumCard";
import {
  HubStatus, HubSource, HubClient, EnrollPushResult, HubTestConnection,
  getHubStatus, getHubSource, setHubSource, syncHubNow, enrollPush, testHubConnection,
} from "@/lib/api";

const MODE_LABEL: Record<HubStatus["mode"], string> = {
  standalone: "Independent",
  client: "Client of a hub",
  hub: "Serving as hub",
  "hub-idle": "Hub (no clients yet)",
};
const MODE_SEV: Record<HubStatus["mode"], string> = {
  standalone: "--sev-normal", client: "--accent", hub: "--sev-notice", "hub-idle": "--sev-info",
};

const PAGE_SIZE = 15;

function defaultHubConfigUrl(): string {
  if (typeof window === "undefined") return "";
  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}:8000/api/hub/config`;
}

export default function ConnectionPage() {
  const { user, ready } = useAuth();
  if (!ready) return <Gate msg="Loading…" />;
  if (!user) return <Gate msg="Sign in as the owner to manage the hub connection." cta />;
  if (user.role !== "OWNER")
    return <Gate msg="Owner only — you don’t have access to the hub connection." />;
  return <ConnectionManager />;
}

function Gate({ msg, cta }: { msg: string; cta?: boolean }) {
  return (
    <div className="ns-adm-wrap">
      <div className="ns-adm-top">
        <div>
          <h1>Connection / Hub</h1>
          <p className="sub">Where this store’s config comes from — owner only.</p>
        </div>
      </div>
      <div className="ns-adm-card">
        <p className="ns-adm-empty">{msg}</p>
        {cta && <p style={{ marginTop: 10 }}><a className="ns-adm-btn primary" href="/account">Go to sign in</a></p>}
      </div>
    </div>
  );
}

function ConnectionManager() {
  const [status, setStatus] = useState<HubStatus | null>(null);
  const [src, setSrc] = useState<HubSource | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // local edits (write-only for tokens — blank fields keep the saved ones)
  const [hubUrl, setHubUrl] = useState("");
  const [enrollToken, setEnrollToken] = useState("");
  const [serveToken, setServeToken] = useState("");
  const [interval, setIntervalS] = useState(45);
  const [hubTest, setHubTest] = useState<HubTestConnection | null>(null);
  const [hubTestBusy, setHubTestBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [s, c] = await Promise.all([getHubSource(), getHubStatus()]);
      setSrc(s); setStatus(c);
      setHubUrl(s.hub_url); setIntervalS(s.pull_interval_s);
    } catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  // Auto-refresh of status (last-sync/clients change on their own).
  useEffect(() => {
    const id = setInterval(() => { getHubStatus().then(setStatus).catch(() => {}); }, 8000);
    return () => clearInterval(id);
  }, []);

  // On-demand refresh of just the status (clients/last-sync), without reloading the source —
  // doesn't clobber the local edits to hub URL/interval (F-032).
  const refreshStatus = useCallback(async () => {
    try { setStatus(await getHubStatus()); }
    catch (e) { setError((e as Error).message); }
  }, []);

  async function apply(patch: Parameters<typeof setHubSource>[0]) {
    setBusy(true); setError(null);
    try { setSrc(await setHubSource(patch)); setEnrollToken(""); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function sync() {
    setBusy(true); setError(null);
    try { await syncHubNow(); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function runHubTest() {
    setHubTestBusy(true); setError(null);
    try { setHubTest(await testHubConnection()); await load(); }
    catch (e) { setError((e as Error).message); setHubTest(null); }
    finally { setHubTestBusy(false); }
  }

  if (!src || !status) {
    return <div className="ns-adm-wrap"><div className="ns-adm-empty">Loading connection…</div></div>;
  }
  const remote = src.source === "remote";
  return (
    <div className="ns-adm-wrap">
      <div className="ns-adm-top">
        <div>
          <h1>Connection / Hub</h1>
          <p className="sub">Independent, or client of a hub — and serve config to other stores.</p>
        </div>
        <div className="ns-adm-actions">
          <div className="ns-cfg-mode" style={{ borderColor: `var(${MODE_SEV[status.mode]})` }}>
            <span className="dot" style={{ background: `var(${MODE_SEV[status.mode]})` }} />
            <b>{MODE_LABEL[status.mode]}</b>
            <code className="ns-cfg-env">{status.env}</code>
          </div>
          <button type="button" className="ns-adm-btn" disabled={busy || hubTestBusy} onClick={runHubTest}>
            {hubTestBusy ? "Testing…" : "Test hub connection"}
          </button>
        </div>
      </div>

      {error && <div className="ns-cfg-test err" style={{ marginBottom: 12 }}>{error}</div>}

      {hubTest && (
        <div className={`ns-adm-card ns-cfg-health ${hubTest.ok ? "ok" : "err"}`} style={{ marginBottom: 12 }}>
          <span className="dot" />
          {hubTest.ok
            ? <>Effective cascade ({hubTest.source}): <b>{hubTest.provider_count}</b> provider{hubTest.provider_count === 1 ? "" : "s"}
              {hubTest.providers.length > 0 && (
                <span className="muted"> — {hubTest.providers.map((p) => `${p.name}/${p.model}`).join(" → ")}</span>
              )}</>
            : <>Hub connection failed{hubTest.remote?.last_error ? `: ${hubTest.remote.last_error}` : ""}</>}
        </div>
      )}

      <div className="ns-cfg-conn-grid">
        {/* CLIENT side: local|remote source + hub target */}
        <div className="ns-adm-card ns-cfg-conn-card">
          <h3 className="ns-cfg-group">Config source</h3>
          <div className="ns-cfg-seg">
            <button type="button" className={`ns-adm-btn ${!remote ? "primary" : ""}`}
              disabled={busy || !remote} onClick={() => apply({ source: "local" })}>Local (independent)</button>
            <button type="button" className={`ns-adm-btn ${remote ? "primary" : ""}`}
              disabled={busy || remote} onClick={() => apply({ source: "remote" })}>Remote (hub)</button>
          </div>

          {remote && (
            <div className="ns-cfg-grid" style={{ marginTop: 12 }}>
              <label className="ns-sim-field">
                <span>Hub URL</span>
                <input value={hubUrl} onChange={(e) => setHubUrl(e.target.value)}
                  placeholder="http://hub-host:8000/api/hub/config" />
              </label>
              <label className="ns-sim-field">
                <span>Enrollment token {src.has_enrollment_token && <em className="ns-cfg-pill">set</em>}</span>
                <input type="password" value={enrollToken} onChange={(e) => setEnrollToken(e.target.value)}
                  placeholder={src.has_enrollment_token ? "•••• (unchanged)" : "paste token"} />
              </label>
              <label className="ns-sim-field">
                <span>Pull interval (s)</span>
                <input type="number" min={5} value={interval}
                  onChange={(e) => setIntervalS(Number(e.target.value))} />
              </label>
              <div className="ns-cfg-actions" style={{ alignSelf: "end" }}>
                <button type="button" className="ns-adm-btn primary" disabled={busy}
                  onClick={() => apply({ hub_url: hubUrl, pull_interval_s: interval, ...(enrollToken ? { enrollment_token: enrollToken } : {}) })}>
                  Save &amp; connect
                </button>
                <button type="button" className="ns-adm-btn" disabled={busy} onClick={sync}>Sync now</button>
              </div>
            </div>
          )}

          {remote && status.remote && (
            <div className={`ns-cfg-health ${status.remote.last_ok ? "ok" : "err"}`}>
              <span className="dot" />
              {status.remote.last_ok
                ? <>Connected to <code>{status.remote.hub_env ?? "hub"}</code> · {status.remote.cached_providers} providers · last sync {fmtTime(status.remote.last_sync)}</>
                : <>{status.remote.has_cache
                    ? <>Hub unreachable ({status.remote.last_error}) — running on cached config ({status.remote.cached_providers} providers)</>
                    : <>Not yet synced{status.remote.last_error ? ` (${status.remote.last_error})` : ""}</>}</>}
            </div>
          )}
          {remote && status.remote?.insecure && (
            <div className="ns-cfg-health err" style={{ marginTop: 8 }}>
              <span className="dot" />
              Insecure transport: LLM keys travel over plain HTTP. Use HTTPS for the hub in the lab.
            </div>
          )}
          {!remote && (
            <p className="ns-adm-note" style={{ marginTop: 10 }}>
              Using {status.local_providers} local provider{status.local_providers === 1 ? "" : "s"} + offline stub.
            </p>
          )}
        </div>

        {/* HUB side: serving token */}
        <div className="ns-adm-card ns-cfg-conn-card">
          <h3 className="ns-cfg-group">Serve as hub</h3>
          <p className="ns-adm-note">
            Let other stores pull this store’s config. Protected by an enrollment token.
            {status.serving ? " Serving is ON." : " Serving is OFF (no token)."}
          </p>
          <div className="ns-cfg-grid" style={{ marginTop: 10 }}>
            <label className="ns-sim-field">
              <span>Serve token {src.has_serve_token && <em className="ns-cfg-pill">set</em>}</span>
              <input type="password" value={serveToken} onChange={(e) => setServeToken(e.target.value)}
                placeholder={src.has_serve_token ? "•••• (set new or clear)" : "set a token to enable serving"} />
            </label>
            <div className="ns-cfg-actions" style={{ alignSelf: "end" }}>
              <button type="button" className="ns-adm-btn primary" disabled={busy}
                onClick={() => { apply({ serve_token: serveToken }); setServeToken(""); }}>Save token</button>
              {status.serving && (
                <button type="button" className="ns-adm-btn" disabled={busy}
                  onClick={() => apply({ serve_token: "" })}>Stop serving</button>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* IP-based enrollment push — forces N stores to become clients of this hub via API (F-027) */}
      <PushEnrollSection serving={status.serving} defaultHubUrl={defaultHubConfigUrl()} pullIntervalS={src.pull_interval_s} />

      {/* Connected clients — full-width (many participants): filter + pagination + timestamps */}
      <ClientsTable clients={status.clients} intervalS={src.pull_interval_s} onRefresh={refreshStatus} />

      {/* Splunk RUM (F-040-RUM): snippet injected into the <head> of every browser session */}
      <RumCard />
    </div>
  );
}

// --- IP-based enrollment push (F-027) ----------------------------------------
// Pushes `source=remote → this hub` to a list of IPs/hosts, calling each store's enroll
// endpoint (token-gated by a shared lab secret). Result per IP. Mechanism = API.
function PushEnrollSection({ serving, defaultHubUrl, pullIntervalS }: {
  serving: boolean; defaultHubUrl: string; pullIntervalS: number;
}) {
  const [ips, setIps] = useState("");
  const [hubUrl, setHubUrl] = useState(defaultHubUrl);
  const [enrollSecret, setEnrollSecret] = useState("");
  const [enrollToken, setEnrollToken] = useState("");
  const [interval, setIntervalS] = useState(pullIntervalS);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<EnrollPushResult | null>(null);

  useEffect(() => {
    if (defaultHubUrl) setHubUrl((u) => u || defaultHubUrl);
  }, [defaultHubUrl]);
  useEffect(() => { setIntervalS(pullIntervalS); }, [pullIntervalS]);

  const ipList = useMemo(
    () => ips.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean), [ips]);
  const valid = ipList.length > 0 && hubUrl.trim() && enrollSecret.trim() && enrollToken.trim();

  async function push() {
    setBusy(true); setErr(null); setResult(null);
    try {
      setResult(await enrollPush({
        ips: ipList, hub_url: hubUrl.trim(), enroll_token: enrollSecret.trim(),
        enrollment_token: enrollToken.trim(), pull_interval_s: interval,
      }));
    } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }

  return (
    <section className="ns-adm-card ns-cfg-push">
      <h3 className="ns-cfg-group" style={{ marginTop: 0 }}>Push enrollment (by IP)</h3>
      <p className="ns-adm-note">
        Force other stores to become clients of this hub. Each store is reached over the API and must
        share the lab enroll secret (<code>ENROLL_TOKEN</code> from each target&apos;s <code>.env</code>).
        {!serving && " Tip: set a serve token above so the enrolled stores can pull."}
      </p>
      <div className="ns-cfg-grid" style={{ marginTop: 10 }}>
        <label className="ns-sim-field ns-cfg-push-ips">
          <span>Target IPs / hosts <span className="ns-sim-sub">(one per line or comma-separated)</span></span>
          <textarea value={ips} rows={3} placeholder={"10.0.0.21\n10.0.0.22:8000\nhttp://store-7:8000"}
            onChange={(e) => setIps(e.target.value)} />
        </label>
        <label className="ns-sim-field">
          <span>This hub URL <span className="ns-sim-sub">(how targets reach it)</span></span>
          <input value={hubUrl} onChange={(e) => setHubUrl(e.target.value)}
            placeholder="http://this-hub:8000/api/hub/config" />
        </label>
        <label className="ns-sim-field">
          <span>Enroll secret <span className="ns-sim-sub">(shared lab ENROLL_TOKEN)</span></span>
          <input type="password" value={enrollSecret} onChange={(e) => setEnrollSecret(e.target.value)}
            placeholder="shared secret to authenticate to targets" />
        </label>
        <label className="ns-sim-field">
          <span>Enrollment token <span className="ns-sim-sub">(targets pull with — this hub’s serve token)</span></span>
          <input type="password" value={enrollToken} onChange={(e) => setEnrollToken(e.target.value)}
            placeholder="token the targets use to pull" />
        </label>
        <label className="ns-sim-field">
          <span>Pull interval (s)</span>
          <input type="number" min={5} value={interval} onChange={(e) => setIntervalS(Number(e.target.value))} />
        </label>
        <div className="ns-cfg-actions" style={{ alignSelf: "end" }}>
          <button type="button" className="ns-adm-btn primary" disabled={busy || !valid} onClick={push}>
            {busy ? "Pushing…" : `Push to ${ipList.length || 0} host${ipList.length === 1 ? "" : "s"}`}
          </button>
        </div>
      </div>

      {err && <div className="ns-cfg-test err" style={{ marginTop: 10 }}>{err}</div>}

      {result && (
        <div className="ns-cfg-push-result">
          <div className="ns-cfg-push-summary">
            <b>{result.ok}</b> ok · <b>{result.failed}</b> failed · {result.total} total
          </div>
          <ul className="ns-cfg-push-list">
            {result.results.map((r) => (
              <li key={r.ip} className={r.ok ? "ok" : "err"}>
                <span className="dot" />
                <code>{r.ip}</code>
                {r.ok
                  ? <span className="msg">enrolled{r.env ? ` · ${r.env}` : ""}{r.mode ? ` · ${r.mode}` : ""}</span>
                  : <span className="msg">{r.error ?? "failed"}{r.status ? ` (HTTP ${r.status})` : ""}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

// --- Connected clients table --------------------------------------------------
// Full-width. Filter by env/IP, client-side pagination (the list arrives whole), "active" =
// last pull within 3x the pull interval. Relative times (last seen) + absolute (since).
function ClientsTable({ clients, intervalS, onRefresh }: { clients: HubClient[]; intervalS: number; onRefresh: () => Promise<void> | void }) {
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [busy, setBusy] = useState(false);
  const [, force] = useState(0);
  // tick to recompute "active"/relative without a new fetch
  useEffect(() => { const id = setInterval(() => force((n) => n + 1), 5000); return () => clearInterval(id); }, []);

  const activeWindowMs = Math.max(intervalS * 3, 30) * 1000;
  const now = Date.now();
  const activeCount = clients.filter((c) => now - new Date(c.last_pull).getTime() <= activeWindowMs).length;

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term) return clients;
    return clients.filter((c) =>
      c.env.toLowerCase().includes(term) || (c.ip ?? "").toLowerCase().includes(term));
  }, [clients, q]);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const cur = Math.min(page, pages - 1);
  const slice = filtered.slice(cur * PAGE_SIZE, cur * PAGE_SIZE + PAGE_SIZE);

  return (
    <section className="ns-cfg-clients-wrap">
      <div className="ns-cfg-clients-head">
        <h3 className="ns-cfg-group" style={{ margin: 0 }}>
          Connected clients <span className="ns-cfg-count">{clients.length}</span>
          {clients.length > 0 && (
            <span className="ns-cfg-active" title="Pulled within 3× the pull interval">
              {activeCount} active
            </span>
          )}
        </h3>
        <div className="ns-cfg-clients-actions">
          <input className="ns-cfg-filter" placeholder="Filter by env or IP…"
            value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
          <button type="button" className="ns-adm-btn" disabled={busy}
            onClick={async () => { setBusy(true); try { await onRefresh(); } finally { setBusy(false); } }}>
            {busy ? "Refreshing…" : "↻ Refresh"}
          </button>
        </div>
      </div>

      {clients.length === 0 ? (
        <div className="ns-adm-card"><p className="ns-adm-empty">No clients have pulled yet.</p></div>
      ) : filtered.length === 0 ? (
        <div className="ns-adm-card"><p className="ns-adm-empty">No clients match “{q}”.</p></div>
      ) : (
        <>
          <div className="ns-adm-table-wrap">
            <table className="ns-adm-table ns-cfg-clients-table">
              <thead>
                <tr>
                  <th>Env</th><th>IP</th><th>Agent</th>
                  <th>Connected since</th><th>Last seen</th><th className="num">Pulls</th>
                </tr>
              </thead>
              <tbody>
                {slice.map((c) => {
                  const active = now - new Date(c.last_pull).getTime() <= activeWindowMs;
                  return (
                    <tr key={c.env + (c.ip ?? "")}>
                      <td><code>{c.env}</code></td>
                      <td className="muted">{c.ip ?? "—"}</td>
                      <td className="ns-cfg-cl-agent" title={c.agent ?? ""}>{shortAgent(c.agent)}</td>
                      <td className="muted" title={c.first_seen ?? ""}>{fmtAbs(c.first_seen)}</td>
                      <td>
                        <span className={`ns-cfg-seen ${active ? "on" : ""}`} title={c.last_pull}>
                          <span className="dot" />{fmtRel(c.last_pull, now)}
                        </span>
                      </td>
                      <td className="num">{c.pulls}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {pages > 1 && (
            <div className="ns-adm-pager">
              <button type="button" className="ns-adm-btn" disabled={cur === 0} onClick={() => setPage(cur - 1)}>‹ Prev</button>
              <span className="ns-adm-pageinfo">Page {cur + 1} / {pages}</span>
              <button type="button" className="ns-adm-btn" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>Next ›</button>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function shortAgent(agent: string | undefined): string {
  if (!agent) return "—";
  // Extracts the main product/engine (e.g.: "Mozilla/5.0 … Chrome/148 …" → "Chrome/148").
  const m = agent.match(/(Chrome|Firefox|Safari|Edg|Electron|curl|python-requests|Vega)[\/ ]?[\d.]*/i);
  return m ? m[0] : agent.slice(0, 28);
}
function fmtAbs(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleString();
}
function fmtRel(iso: string | null | undefined, now: number): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (isNaN(t)) return "—";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 5) return "just now";
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleTimeString();
}
