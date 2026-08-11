"use client";
// CONFIG — OWNER-only screen for the LLM cascade providers (F-020, ADR-015).
// Role-gated: only `role === "OWNER"` sees the screen (and the link in the layer bar). The
// endpoints are already gated on the backend (401/403) — this is the UX layer. KEYS are
// secrets: the UI never receives the key (only `has_key`/`key_hint`); the key field is
// write-only (left blank keeps the saved one). The "Test" button per provider makes a real call.
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import {
  LLMProvider, ProviderInput, ProviderKind, ProviderTest, LLMTypePreset, HubSource,
  getProviders, createProvider, updateProvider, deleteProvider, reorderProviders, testProvider, getLLMTypes, getHubSource,
} from "@/lib/api";

// Fallback for the Types in case the backend catalog fails to load (the UI stays usable offline).
const FALLBACK_TYPES: LLMTypePreset[] = [
  { type: "custom", label: "Custom", kind: "openai", base_url: "", models: [] },
];
// For the "custom" Type the owner picks the kind (openai-compatible or Anthropic).
const KINDS: { value: ProviderKind; label: string }[] = [
  { value: "openai", label: "OpenAI-compatible" },
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "bedrock", label: "Amazon Bedrock" },
];

// Infers the Type of a saved provider (the table stores kind/base_url, not the Type): matches
// by the preset's base_url; otherwise "custom". Used when opening the edit form.
function inferType(types: LLMTypePreset[], p: { base_url: string; kind: ProviderKind }): string {
  const hit = types.find((t) => t.type !== "custom" && t.base_url === p.base_url && t.kind === p.kind);
  return hit?.type ?? "custom";
}

const EMPTY: ProviderInput = { name: "", kind: "openai", base_url: "", model: "", api_key: "", enabled: true };

export default function ConfigPage() {
  const { user, ready } = useAuth();
  if (!ready) return <Gate title="LLM Configuration" msg="Loading…" />;
  if (!user) return <Gate title="LLM Configuration" msg="Sign in as the owner to manage LLM providers." cta />;
  if (user.role !== "OWNER")
    return <Gate title="LLM Configuration" msg="Owner only — you don’t have access to the LLM configuration." />;
  return <ConfigManager />;
}

// State/edge outside of permission: keeps the screen chrome, without exposing anything.
function Gate({ title, msg, cta }: { title: string; msg: string; cta?: boolean }) {
  return (
    <>
      <div className="ns-adm-wrap">
        <div className="ns-adm-top">
          <div>
            <h1>{title}</h1>
            <p className="sub">LLM providers and fallback cascade — owner only.</p>
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

function ConfigManager() {
  const [providers, setProviders] = useState<LLMProvider[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | "new" | null>(null); // id being edited, "new", or none
  const [tests, setTests] = useState<Record<string, ProviderTest | "loading">>({});
  const [confirmDel, setConfirmDel] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [types, setTypes] = useState<LLMTypePreset[]>(FALLBACK_TYPES);
  const [hubSource, setHubSource] = useState<HubSource | null>(null);
  const remoteSource = hubSource?.source === "remote";

  const load = useCallback(async () => {
    setError(null);
    try {
      setProviders(await getProviders());
    } catch (e) {
      setError((e as Error).message);
      setProviders([]);
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { getLLMTypes().then((t) => t.length && setTypes(t)).catch(() => {}); }, []);
  useEffect(() => { getHubSource().then(setHubSource).catch(() => {}); }, []);

  async function save(input: ProviderInput, id: string | "new") {
    setBusy(true);
    setError(null);
    try {
      if (id === "new") await createProvider(input);
      else await updateProvider(id, input);
      setEditing(null);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function toggleEnabled(p: LLMProvider) {
    setBusy(true);
    try { await updateProvider(p.id, { enabled: !p.enabled }); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function move(index: number, dir: -1 | 1) {
    if (!providers) return;
    const j = index + dir;
    if (j < 0 || j >= providers.length) return;
    const ids = providers.map((p) => p.id);
    [ids[index], ids[j]] = [ids[j], ids[index]];
    setBusy(true);
    try { setProviders(await reorderProviders(ids)); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function remove(id: string) {
    setBusy(true);
    setConfirmDel(null);
    try { await deleteProvider(id); await load(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }

  async function runTest(id: string) {
    setTests((t) => ({ ...t, [id]: "loading" }));
    try {
      const result = await testProvider(id);
      setTests((t) => ({ ...t, [id]: result }));
    } catch (e) {
      setTests((t) => ({ ...t, [id]: { ok: false, error: (e as Error).message } }));
    }
  }

  return (
    <>
      <div className="ns-adm-wrap">
        <div className="ns-adm-top">
          <div>
            <h1>LLM Configuration</h1>
            <p className="sub">Providers tried top-to-bottom; on failure the next is used, with the offline stub last.</p>
          </div>
          <div className="ns-adm-actions">
            {error && <span className="ns-adm-note" style={{ color: "var(--sev-critical)" }}>{error}</span>}
            {editing !== "new" && (
              <button type="button" className="ns-adm-btn primary" onClick={() => setEditing("new")} disabled={busy}>
                + Add provider
              </button>
            )}
          </div>
        </div>

        {remoteSource && (
          <div className="ns-ff-hub" style={{ marginBottom: 12 }}>
            <span className="ns-ff-hub-icon" aria-hidden>🔗</span>
            <span>
              This store pulls LLM config from a <strong>hub</strong>. The list below is the <strong>local SQLite copy</strong> — chat uses the hub cascade.
              Use <a className="ns-cfg-link" href="/admin/connection">Connection / Hub → Test hub connection</a> or <a className="ns-cfg-link" href="/admin/agents">Agents → Test</a> to validate the effective cascade.
            </span>
          </div>
        )}

        {editing === "new" && (
          <ProviderForm initial={EMPTY} editingExisting={false} busy={busy} types={types}
            onCancel={() => setEditing(null)} onSave={(i) => save(i, "new")} />
        )}

        <div className="ns-cfg-list">
          {providers === null ? (
            <div className="ns-adm-empty">Loading providers…</div>
          ) : providers.length === 0 ? (
            <div className="ns-adm-card">
              <p className="ns-adm-empty">
                No providers yet. The concierge runs on the offline stub. Add a provider to use a real LLM.
              </p>
            </div>
          ) : (
            providers.map((p, i) =>
              editing === p.id ? (
                <ProviderForm key={p.id} initial={{ ...p, api_key: "" }} editingExisting keyHint={p.key_hint}
                  types={types} busy={busy} onCancel={() => setEditing(null)} onSave={(input) => save(input, p.id)} />
              ) : (
                <ProviderRow
                  key={p.id} p={p} index={i} total={providers.length} busy={busy}
                  test={tests[p.id]} confirmDel={confirmDel === p.id} remoteSource={remoteSource}
                  onMove={move} onToggle={() => toggleEnabled(p)} onTest={() => runTest(p.id)}
                  onEdit={() => { setEditing(p.id); setError(null); }}
                  onAskDelete={() => setConfirmDel(p.id)} onCancelDelete={() => setConfirmDel(null)}
                  onDelete={() => remove(p.id)}
                />
              )
            )
          )}
          {/* Stub is always the cascade's last resort (offline standalone) — informational. */}
          {providers && providers.length > 0 && (
            <div className="ns-cfg-stub">↳ Offline stub — always last, so the app never goes silent.</div>
          )}
        </div>

        {/* PER-AGENT config left this screen (F-027): it now lives in the visual agent editor
            (/admin/agents) — clicking a node in the diagram opens/edits the config. Only the
            LLM providers/cascade stay here. */}
        <p className="ns-adm-note" style={{ marginTop: 18 }}>
          Looking for per-agent settings (model, role, system prompt)? They now live in the{" "}
          <a className="ns-cfg-link" href="/admin/agents">Agents</a> editor — click an agent in the diagram.
        </p>
      </div>
    </>
  );
}

// (The Connection/Hub section became its own screen — `app/admin/connection/page.tsx` — in the
// F-026 UX split; the Agents section became the visual editor `app/admin/agents/page.tsx` — F-027.)

// --- provider row -------------------------------------------------------------
function ProviderRow({
  p, index, total, busy, test, confirmDel, remoteSource,
  onMove, onToggle, onTest, onEdit, onAskDelete, onCancelDelete, onDelete,
}: {
  p: LLMProvider; index: number; total: number; busy: boolean;
  test: ProviderTest | "loading" | undefined; confirmDel: boolean; remoteSource: boolean;
  onMove: (i: number, d: -1 | 1) => void; onToggle: () => void; onTest: () => void;
  onEdit: () => void; onAskDelete: () => void; onCancelDelete: () => void; onDelete: () => void;
}) {
  return (
    <div className={`ns-cfg-card ${p.enabled ? "" : "off"}`}>
      <div className="ns-cfg-rank">
        <button type="button" aria-label="Move up" disabled={busy || index === 0} onClick={() => onMove(index, -1)}>▲</button>
        <span className="num">{index + 1}</span>
        <button type="button" aria-label="Move down" disabled={busy || index === total - 1} onClick={() => onMove(index, 1)}>▼</button>
      </div>

      <div className="ns-cfg-body">
        <div className="ns-cfg-head">
          <span className="name">{p.name}</span>
          <span className="kind">{p.kind === "anthropic" ? "Anthropic" : "OpenAI-compatible"}</span>
          {!p.enabled && <span className="ns-cfg-pill off">disabled</span>}
        </div>
        <div className="ns-cfg-meta">
          <span><b>Model</b> {p.model}</span>
          {p.base_url && <span><b>Base URL</b> {p.base_url}</span>}
          <span>
            <b>Key</b> {p.has_key ? <code>{p.key_hint}</code> : <em className="ns-cfg-nokey">none</em>}
          </span>
        </div>
        {test && (
          <div className={`ns-cfg-test ${test === "loading" ? "" : test.ok ? "ok" : "err"}`}>
            {test === "loading"
              ? "Testing…"
              : test.ok
                ? `${remoteSource ? "✓ Local SQLite only · " : "✓ OK · "}${test.model ?? "?"} · ${test.latency_ms ?? "?"} ms · ${(test.input_tokens ?? 0) + (test.output_tokens ?? 0)} tok`
                : `✗ ${test.error ?? "failed"}`}
          </div>
        )}
      </div>

      <div className="ns-cfg-actions">
        <label className="ns-cfg-switch" title={p.enabled ? "Enabled" : "Disabled"}>
          <input type="checkbox" checked={p.enabled} onChange={onToggle} disabled={busy} />
          <span>{p.enabled ? "On" : "Off"}</span>
        </label>
        <button type="button" className="ns-adm-btn" onClick={onTest} disabled={busy || test === "loading"}
          title={remoteSource ? "Tests local SQLite, not the hub cascade in use at runtime" : undefined}>
          {test === "loading" ? "Testing…" : "Test"}
        </button>
        <button type="button" className="ns-adm-btn" onClick={onEdit} disabled={busy}>Edit</button>
        {confirmDel ? (
          <span className="ns-adm-confirm">
            Delete?
            <button type="button" className="ns-adm-btn danger" onClick={onDelete} disabled={busy}>Yes</button>
            <button type="button" className="ns-adm-btn" onClick={onCancelDelete} disabled={busy}>No</button>
          </span>
        ) : (
          <button type="button" className="ns-adm-btn danger" onClick={onAskDelete} disabled={busy}>Delete</button>
        )}
      </div>
    </div>
  );
}

// --- create/edit form -----------------------------------------------------
function ProviderForm({
  initial, editingExisting, keyHint, types, busy, onCancel, onSave,
}: {
  initial: ProviderInput; editingExisting: boolean; keyHint?: string | null;
  types: LLMTypePreset[]; busy: boolean; onCancel: () => void; onSave: (input: ProviderInput) => void;
}) {
  const [f, setF] = useState<ProviderInput>(initial);
  // Type drives the prefill (kind + base_url + suggested models). When editing, it's inferred from the saved value.
  const [type, setType] = useState<string>(() => inferType(types, initial));
  const preset = types.find((t) => t.type === type);
  const isCustom = type === "custom" || !preset;
  const isBedrock = f.kind === "bedrock";
  const valid = f.name.trim() && f.model.trim() && (editingExisting || f.api_key?.trim());

  function set<K extends keyof ProviderInput>(k: K, v: ProviderInput[K]) {
    setF((prev) => ({ ...prev, [k]: v }));
  }

  // Switching the Type prefills kind + base_url and (if the model field is empty) the 1st suggested model.
  // Everything stays editable; "custom" leaves base_url/models free and exposes the kind selector.
  function pickType(t: string) {
    setType(t);
    const ps = types.find((x) => x.type === t);
    if (!ps) return;
    setF((prev) => ({
      ...prev,
      kind: ps.kind,
      base_url: ps.type === "custom" ? prev.base_url : ps.base_url,
      model: prev.model.trim() ? prev.model : (ps.models[0] ?? prev.model),
    }));
  }

  const modelListId = `ns-models-${type}`;

  return (
    <div className="ns-adm-card ns-cfg-form">
      <h2>{editingExisting ? "Edit provider" : "Add provider"}</h2>
      <div className="ns-cfg-grid">
        <div className="ns-sim-field">
          <label>Name</label>
          <input type="text" value={f.name} placeholder="groq / openai / claude…"
            onChange={(e) => set("name", e.target.value)} />
        </div>
        <div className="ns-sim-field">
          <label>Type <span className="ns-sim-sub">(prefills Base URL + models)</span></label>
          <select value={type} onChange={(e) => pickType(e.target.value)}>
            {types.map((t) => <option key={t.type} value={t.type}>{t.label}</option>)}
          </select>
        </div>
        {isCustom && (
          <div className="ns-sim-field">
            <label>Adapter</label>
            <select value={f.kind} onChange={(e) => set("kind", e.target.value as ProviderKind)}>
              {KINDS.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}
            </select>
          </div>
        )}
        <div className="ns-sim-field">
          <label>Model {!isCustom && preset!.models.length > 0 &&
            <span className="ns-sim-sub">(suggested — editable)</span>}</label>
          <input type="text" value={f.model} list={modelListId} autoComplete="off"
            placeholder="llama-3.1-8b-instant / claude-3-5-haiku…"
            onChange={(e) => set("model", e.target.value)} />
          {preset && preset.models.length > 0 && (
            <datalist id={modelListId}>
              {preset.models.map((m) => <option key={m} value={m} />)}
            </datalist>
          )}
        </div>
        <div className="ns-sim-field">
          <label>{isBedrock ? "AWS region" : "Base URL"} {f.kind === "anthropic" && !isBedrock && <span className="ns-sim-sub">(optional for Anthropic)</span>}</label>
          <input type="text" value={f.base_url}
            placeholder={isBedrock ? "us-east-1" : (preset?.base_url || "https://api.example.com/v1")}
            onChange={(e) => set("base_url", e.target.value)} />
        </div>
        <div className="ns-sim-field ns-cfg-keyfield">
          <label>{isBedrock ? "Bedrock API key" : "API key"} {isBedrock && <span className="ns-sim-sub">(long-term key from AWS Console)</span>}
            {editingExisting && <span className="ns-sim-sub">(blank keeps {keyHint ?? "current"})</span>}</label>
          <input type="password" value={f.api_key ?? ""} autoComplete="off"
            placeholder={editingExisting ? "•••••••• (unchanged)" : (isBedrock ? "Bedrock API key — stored server-side" : "secret — stored server-side, never shown again")}
            onChange={(e) => set("api_key", e.target.value)} />
        </div>
        <label className="ns-sim-check ns-cfg-enable">
          <input type="checkbox" checked={f.enabled ?? true} onChange={(e) => set("enabled", e.target.checked)} />
          Enabled (part of the cascade)
        </label>
      </div>
      <div className="ns-cfg-formactions">
        <button type="button" className="ns-adm-btn primary" disabled={busy || !valid} onClick={() => onSave(f)}>
          {busy ? "Saving…" : editingExisting ? "Save changes" : "Add provider"}
        </button>
        <button type="button" className="ns-adm-btn" disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      <p className="ns-cfg-secnote">🔒 Keys are stored server-side and never sent back to the browser.</p>
    </div>
  );
}
