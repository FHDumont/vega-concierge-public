"use client";
// Config card for a SINGLE agent (F-021): connection/model/role/system prompt + Test + Save.
// Extracted from app/admin/config/page.tsx in F-027 to be reused by the visual agent editor
// (clicking a diagram node opens the SAME config). No secrets here (goes raw to the front end).
import { useEffect, useState } from "react";
import {
  AgentConfig, AgentInput, AgentTest, LLMProvider, updateAgent, testAgent,
} from "@/lib/api";

export default function AgentCard({ agent, providers, onSaved }: {
  agent: AgentConfig; providers: LLMProvider[]; onSaved: () => void;
}) {
  const [f, setF] = useState<AgentConfig>(agent);
  const [busy, setBusy] = useState(false);
  const [test, setTest] = useState<AgentTest | "loading" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Reacts to a change of the selected agent (reused in the visual editor — props change in-place).
  useEffect(() => { setF(agent); setTest(null); setErr(null); }, [agent]);
  // dirty: some editable field changed relative to what was saved.
  const dirty = (["connection", "model", "role", "system_prompt"] as const).some((k) => f[k] !== agent[k]);

  function set<K extends keyof AgentConfig>(k: K, v: AgentConfig[K]) {
    setF((prev) => ({ ...prev, [k]: v }));
  }
  const patch: AgentInput = {
    connection: f.connection, model: f.model, role: f.role, system_prompt: f.system_prompt,
  };

  async function save() {
    setBusy(true); setErr(null);
    try { await updateAgent(agent.agent, patch); onSaved(); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  }
  async function runTest() {
    setTest("loading"); setErr(null);
    try { setTest(await testAgent(agent.agent, patch)); }
    catch (e) { setTest({ ok: false, error: (e as Error).message }); }
  }

  return (
    <div className="ns-cfg-card ns-cfg-agent">
      <div className="ns-cfg-body">
        <div className="ns-cfg-head">
          <span className="name">{agent.agent}</span>
          {f.role && <span className="kind">{f.role}</span>}
          {dirty && <span className="ns-cfg-pill">unsaved</span>}
        </div>
        <div className="ns-cfg-grid">
          <div className="ns-sim-field">
            <label>Connection</label>
            <select value={f.connection} onChange={(e) => set("connection", e.target.value)}>
              <option value="">Cascade (all enabled)</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>{p.name} · {p.model}{p.enabled ? "" : " (disabled)"}</option>
              ))}
            </select>
          </div>
          <div className="ns-sim-field">
            <label>Model override <span className="ns-sim-sub">(blank = inherit)</span></label>
            <input type="text" value={f.model} placeholder="inherit from connection"
              onChange={(e) => set("model", e.target.value)} />
          </div>
          <div className="ns-sim-field">
            <label>Role</label>
            <input type="text" value={f.role} placeholder="e.g. Pricing agent"
              onChange={(e) => set("role", e.target.value)} />
          </div>
          <div className="ns-sim-field ns-cfg-sysfield">
            <label>System prompt</label>
            <textarea value={f.system_prompt} rows={2} placeholder="Instruction sent to the LLM…"
              onChange={(e) => set("system_prompt", e.target.value)} />
          </div>
        </div>
        {test && (
          <div className={`ns-cfg-test ${test === "loading" ? "" : test.ok ? "ok" : "err"}`}>
            {test === "loading"
              ? "Testing…"
              : test.ok
                ? `✓ OK · ${test.provider ?? "?"} · ${test.model ?? "?"} · ${test.latency_ms ?? "?"} ms · ${(test.input_tokens ?? 0) + (test.output_tokens ?? 0)} tok`
                : `✗ ${test.error ?? "failed"}`}
          </div>
        )}
      </div>
      <div className="ns-cfg-actions">
        {err && <span className="ns-adm-note" style={{ color: "var(--sev-critical)" }}>{err}</span>}
        <button type="button" className="ns-adm-btn" onClick={runTest} disabled={busy || test === "loading"}>
          {test === "loading" ? "Testing…" : "Test"}
        </button>
        <button type="button" className="ns-adm-btn primary" onClick={save} disabled={busy || !dirty}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
