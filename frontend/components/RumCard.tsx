"use client";
// Splunk RUM (Browser Agent) — card do DONO na tela de Connection (F-040-RUM). O owner cola o
// snippet BRUTO do manual do Splunk e liga o toggle; o frontend injeta no <head> (server-render
// em app/layout.tsx) p/ TODAS as sessões de navegador (visitantes reais + simulador modo Browser
// F-039). Off por default. Owner-gated pela tela que o hospeda (backend é a fronteira real).
import { useCallback, useEffect, useState } from "react";
import { RumConfig, getRumAdmin, setRum } from "@/lib/api";

const PLACEHOLDER = `<script src="https://cdn.observability.splunkcloud.com/o11y-gdi-rum/<version>/splunk-otel-web.js" crossorigin="anonymous"></script>
<script>
  SplunkRum.init({
    realm: '<realm>',
    rumAccessToken: 'your-splunk-rum-access-token',
    applicationName: 'vega',
    version: '1.0.0',
    deploymentEnvironment: 'workshop'
  });
</script>`;

export default function RumCard() {
  const [cfg, setCfg] = useState<RumConfig | null>(null);
  const [snippet, setSnippet] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const c = await getRumAdmin();
      setCfg(c);
      setSnippet(c.snippet);
      setEnabled(c.enabled);
    } catch {
      setNote("Could not load RUM config.");
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function save() {
    setBusy(true);
    setNote(null);
    try {
      const c = await setRum({ enabled, snippet });
      setCfg(c);
      setSnippet(c.snippet);
      setEnabled(c.enabled);
      setNote("Saved. Reload pages to apply.");
    } catch {
      setNote("Save failed.");
    } finally {
      setBusy(false);
    }
  }

  const dirty = !!cfg && (cfg.enabled !== enabled || cfg.snippet !== snippet);

  return (
    <div className="ns-adm-card">
      <div className="ns-adm-top" style={{ marginBottom: 8 }}>
        <div>
          <h2>Splunk RUM (browser agent)</h2>
          <p className="sub">
            Paste the RUM snippet from the Splunk manual. When enabled it&apos;s injected into every
            page&apos;s <code>&lt;head&gt;</code> — real visitors and the Browser simulator (F-039).
          </p>
        </div>
        <div className="ns-adm-actions">
          {note && <span className="ns-adm-note">{note}</span>}
          <span className={`ns-sim-state ${enabled ? "running" : "stopped"}`}>
            <span className="dot" />
            {enabled ? "on" : "off"}
          </span>
        </div>
      </div>

      <label className="ns-sim-check">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Inject the RUM agent on every page
      </label>

      <div className="ns-sim-field" style={{ marginTop: 10 }}>
        <label>RUM snippet</label>
        <textarea
          value={snippet}
          onChange={(e) => setSnippet(e.target.value)}
          placeholder={PLACEHOLDER}
          spellCheck={false}
          rows={12}
          style={{ width: "100%", fontFamily: "var(--font-mono, monospace)", fontSize: 12.5, lineHeight: 1.5 }}
        />
        <span className="ns-sim-sub">
          Replace <code>&lt;version&gt;</code>, <code>&lt;realm&gt;</code> and the access token per the
          manual. Pasted verbatim — not validated.
        </span>
      </div>

      <div className="ns-adm-actions" style={{ marginTop: 10 }}>
        <button type="button" className="ns-adm-btn primary" onClick={save} disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
