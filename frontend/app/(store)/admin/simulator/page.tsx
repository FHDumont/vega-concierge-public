"use client";
// SIMULADOR AVANÇADO — tela própria do dono (F-018, ADR-014). Fora do route group
// (store); reusa o chrome do Admin (ns-adm-*) + classes ns-sim-*. Form de config rica
// + controles Start/Pause/Stop + painel AO VIVO (poll de /api/simulator/status).
// É a ferramenta de tráfego (gera carga real chamando a API).
import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { SimConfig, SimStatus, simStart, simStop, simPause, simStatus, getRum } from "@/lib/api";
import FlagGuard from "@/components/FlagGuard";
import { WORKSHOP_SIMULATOR_ENABLED } from "@/lib/workshop-config";

// Categorias/tiers/problemas espelham o backend (simulator.py).
const CATEGORIES = ["Audio", "Wearables", "Home", "Gifts"] as const;
const TIERS = ["STANDARD", "GOLD", "PLATINUM"] as const;
const PROBLEMS: { key: string; label: string }[] = [
  { key: "price_hallucination", label: "Price hallucination" },
  { key: "payment_outage", label: "Payment outage" },
  { key: "inventory_outage", label: "Inventory outage" },
  { key: "fraud_false_positive", label: "Fraud false positive" },
];
// Presets de velocidade (multiplicador dos sleeps): demo rápido ↔ realista.
const SPEEDS: { label: string; value: number }[] = [
  { label: "Demo (fast)", value: 0.15 },
  { label: "Brisk", value: 0.4 },
  { label: "Realistic", value: 1 },
];

// Form = SimConfig com defaults espelhando SimConfig do backend.
// Modo de tráfego (F-039): API in-process (rápido, sem RUM) vs Browser real (Playwright,
// mais pesado, gera sessões de navegador p/ o RUM). Teto de N menor no modo browser.
const MODES: { value: SimConfig["mode"]; label: string; hint: string }[] = [
  { value: "api", label: "API", hint: "In-process — fast, no real browser." },
  { value: "browser", label: "Browser", hint: "Headless Chromium drives the UI — RUM-ready, heavier." },
];
const BROWSER_MAX_CONCURRENCY = 12; // espelha _BROWSER_MAX_CONCURRENCY no backend

const DEFAULTS: SimConfig = {
  mode: "api",
  concurrency: 5,
  wait_min_s: 1, wait_max_s: 4,
  think_min_s: 0.4, think_max_s: 1.5,
  actions_min: 2, actions_max: 6,
  concierge_pct: 40, problem_pct: 0,
  problems: PROBLEMS.map((p) => p.key),
  category_mix: { Audio: 1, Wearables: 1, Home: 1, Gifts: 1 },
  tier_mix: { STANDARD: 3, GOLD: 2, PLATINUM: 1 },
  speed: 0.15,
  target_kind: "none", target_value: 0,
  reset: false,
  max_lines: 3, max_qty: 2,
};

// Config persistida no navegador: volta ao último valor configurado ao recarregar.
const STORE_KEY = "vega.sim.config";
function saveCfg(c: SimConfig) {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(c));
  } catch {
    /* localStorage indisponível (modo privado) — segue sem persistir */
  }
}

function fmtUptime(s: number): string {
  const t = Math.floor(s);
  const m = Math.floor(t / 60);
  const sec = t % 60;
  return m > 0 ? `${m}m ${String(sec).padStart(2, "0")}s` : `${sec}s`;
}

// F-033: barrado p/ participantes quando a flag `simulator` está OFF (o owner passa — ADR-021).
// WORKSHOP_SIMULATOR_ENABLED=false oculta a superfície nesta versão do workshop (incl. owner).
function SimulatorWorkshopGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  useEffect(() => {
    if (!WORKSHOP_SIMULATOR_ENABLED) router.replace("/admin");
  }, [router]);
  if (!WORKSHOP_SIMULATOR_ENABLED) return null;
  return <>{children}</>;
}

export default function SimulatorPage() {
  return (
    <SimulatorWorkshopGuard>
      <FlagGuard flag="simulator">
        <Simulator />
      </FlagGuard>
    </SimulatorWorkshopGuard>
  );
}

function Simulator() {
  const [cfg, setCfg] = useState<SimConfig>(DEFAULTS);
  const [status, setStatus] = useState<SimStatus | null>(null);
  const [rumOn, setRumOn] = useState<boolean | null>(null); // F-040-RUM: status p/ o modo browser
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const running = status?.status === "running";
  const paused = status?.status === "paused";
  const active = running || paused;

  const poll = useCallback(async () => {
    try {
      setStatus(await simStatus());
    } catch {
      /* server pode estar reiniciando; mantém o último estado */
    }
  }, []);

  // Poll do status a cada 1s (painel ao vivo). Lê o estado real mesmo se outra aba mexeu.
  useEffect(() => {
    poll();
    timer.current = setInterval(poll, 1000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [poll]);

  // Restaura a última config salva ao montar (sobrepõe os defaults; campos novos ficam no default).
  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (raw) setCfg((c) => ({ ...c, ...JSON.parse(raw) }));
    } catch {
      /* ignore */
    }
  }, []);

  // Status do Splunk RUM (F-040-RUM): o modo browser só gera RUM se o owner ligou o snippet.
  useEffect(() => {
    getRum().then((r) => setRumOn(r.enabled)).catch(() => setRumOn(null));
  }, []);

  // Mutators salvam a cada edição (persiste no navegador).
  function set<K extends keyof SimConfig>(key: K, value: SimConfig[K]) {
    setCfg((c) => {
      const next = { ...c, [key]: value };
      saveCfg(next);
      return next;
    });
  }
  function setMix(field: "category_mix" | "tier_mix", key: string, value: number) {
    setCfg((c) => {
      const next = { ...c, [field]: { ...c[field], [key]: Math.max(0, value) } };
      saveCfg(next);
      return next;
    });
  }
  function toggleProblem(key: string) {
    setCfg((c) => {
      const problems = c.problems.includes(key) ? c.problems.filter((p) => p !== key) : [...c.problems, key];
      const next = { ...c, problems };
      saveCfg(next);
      return next;
    });
  }

  async function onStart() {
    setBusy(true);
    setNote(null);
    try {
      setStatus(await simStart(cfg));
      setNote(`Started ${cfg.concurrency} concurrent ${cfg.mode === "browser" ? "browser" : "API"} sessions.`);
    } catch {
      setNote("Start failed.");
    } finally {
      setBusy(false);
    }
  }
  async function onStop() {
    setBusy(true);
    try {
      setStatus(await simStop());
      setNote("Stopped.");
    } catch {
      setNote("Stop failed.");
    } finally {
      setBusy(false);
    }
  }
  async function onPause() {
    setBusy(true);
    try {
      setStatus(await simPause(!paused));
    } catch {
      setNote("Pause failed.");
    } finally {
      setBusy(false);
    }
  }

  const statusLabel = status?.status ?? "—";

  return (
    <>
      <div className="ns-adm-wrap ns-sim-wrap">
        <div className="ns-adm-top">
          <div>
            <h1>Traffic simulator</h1>
            <p className="sub">
              Concurrent shoppers that browse, optionally ask the concierge and always check out.
            </p>
          </div>
          <div className="ns-adm-actions">
            {note && <span className="ns-adm-note">{note}</span>}
            <span className={`ns-sim-state ${status?.status ?? "stopped"}`}>
              <span className="dot" />
              {statusLabel}
            </span>
            {!active ? (
              <button type="button" className="ns-adm-btn primary" onClick={onStart} disabled={busy}>
                {busy ? "Working…" : "Start"}
              </button>
            ) : (
              <>
                <button type="button" className="ns-adm-btn" onClick={onPause} disabled={busy}>
                  {paused ? "Resume" : "Pause"}
                </button>
                <button type="button" className="ns-adm-btn danger" onClick={onStop} disabled={busy}>
                  Stop
                </button>
              </>
            )}
          </div>
        </div>

        <div className="ns-adm-main ns-sim-main">
          {/* --- painel AO VIVO --- */}
          <LivePanel status={status} fmtUptime={fmtUptime} />

          {/* --- config --- */}
          <ConfigForm
            cfg={cfg}
            active={!!active}
            rumOn={rumOn}
            set={set}
            setMix={setMix}
            toggleProblem={toggleProblem}
          />
        </div>
      </div>
    </>
  );
}

// --- painel ao vivo ---------------------------------------------------------
function LivePanel({ status, fmtUptime }: { status: SimStatus | null; fmtUptime: (s: number) => string }) {
  const s = status;
  const sessions = s?.sessions ?? [];
  const byStatus = s?.by_status ?? {};
  return (
    <>
      <div className="ns-adm-kpis ns-sim-kpis">
        <Kpi label="Sessions" value={s ? String(s.pool_size) : "—"} />
        <Kpi label="Completed" value={s ? String(s.completed) : "—"} />
        <Kpi label="Paid" value={s ? String(s.paid) : "—"} />
        <Kpi label="Orders / min" value={s ? String(s.orders_per_min) : "—"} />
        <Kpi label="Errors" value={s ? String(s.errors) : "—"} accent={s && s.errors > 0 ? "crit" : undefined} />
        <Kpi label="Uptime" value={s && s.status !== "stopped" ? fmtUptime(s.uptime_s) : "—"} />
      </div>

      <div className="ns-adm-card">
        <h2>Live sessions</h2>
        {sessions.length === 0 ? (
          <p className="ns-adm-empty">Idle — configure on the right and press Start.</p>
        ) : (
          <table className="ns-adm-table ns-sim-sessions">
            <thead>
              <tr>
                <th style={{ width: 48 }}>Slot</th>
                <th>Shopper</th>
                <th style={{ width: 96 }}>Tier</th>
                <th style={{ width: 188 }}>Current action</th>
                <th style={{ width: 78 }}>Last</th>
                <th style={{ width: 78, textAlign: "right" }}>Journeys</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((row) => (
                <tr key={row.slot}>
                  <td className="ns-adm-sku">#{row.slot}</td>
                  <td className="ns-sim-shopper">{row.user ?? <span className="ns-sim-muted">—</span>}</td>
                  <td>
                    {row.tier
                      ? <span className={`ns-sim-tier ${row.tier.toLowerCase()}`}>{row.tier}</span>
                      : <span className="ns-sim-muted">—</span>}
                  </td>
                  <td>
                    <span className={`ns-sim-action ${status?.status === "running" ? "live" : ""}`}>
                      {row.action}
                    </span>
                  </td>
                  <td>
                    {row.last
                      ? <span className={`ns-sim-last ${row.last.toLowerCase()}`}>{row.last}</span>
                      : <span className="ns-sim-muted">—</span>}
                  </td>
                  <td className="num">{row.journeys}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {Object.keys(byStatus).length > 0 && (
        <div className="ns-adm-card">
          <h2>Orders by status</h2>
          <div className="ns-sim-chips">
            {Object.entries(byStatus).map(([st, n]) => (
              <span key={st} className={`ns-sim-chip ${st.toLowerCase()}`}>
                {st} <b>{n}</b>
              </span>
            ))}
          </div>
          {s && s.injected > 0 && (
            <p className="ns-sim-cap">
              {s.injected} of {s.completed} journeys injected a problem (blast radius hits concurrent checkouts).
            </p>
          )}
        </div>
      )}
    </>
  );
}

function Kpi({ label, value, accent }: { label: string; value: string; accent?: "crit" }) {
  return (
    <div className="ns-adm-kpi">
      <p className="lbl">{label}</p>
      <p className="val" style={accent === "crit" ? { color: "var(--sev-critical)" } : undefined}>{value}</p>
    </div>
  );
}

// --- formulário de config ---------------------------------------------------
function ConfigForm({
  cfg, active, rumOn, set, setMix, toggleProblem,
}: {
  cfg: SimConfig;
  active: boolean;
  rumOn: boolean | null;
  set: <K extends keyof SimConfig>(key: K, value: SimConfig[K]) => void;
  setMix: (field: "category_mix" | "tier_mix", key: string, value: number) => void;
  toggleProblem: (key: string) => void;
}) {
  return (
    <div className="ns-adm-card ns-sim-form">
      <h2>Configuration</h2>
      {active
        ? <p className="ns-sim-hint">Stop the run to change the configuration.</p>
        : <p className="ns-sim-sub">Settings are saved automatically and restored on reload.</p>}
      <fieldset disabled={active} className="ns-sim-fields">
        <div className="ns-sim-field">
          <label>Traffic mode</label>
          <div className="ns-sim-seg">
            {MODES.map((m) => (
              <button type="button" key={m.value} className={cfg.mode === m.value ? "on" : ""}
                onClick={() => {
                  set("mode", m.value);
                  if (m.value === "browser" && cfg.concurrency > BROWSER_MAX_CONCURRENCY)
                    set("concurrency", BROWSER_MAX_CONCURRENCY);
                }}>
                {m.label}
              </button>
            ))}
          </div>
          <span className="ns-sim-sub">{MODES.find((m) => m.value === cfg.mode)?.hint}</span>
          {cfg.mode === "browser" && rumOn !== null && (
            <span className="ns-sim-sub">
              Splunk RUM: <b>{rumOn ? "on" : "off"}</b> —{" "}
              <a href="/admin/connection">{rumOn ? "configure" : "paste the snippet to capture sessions"}</a>.
            </span>
          )}
        </div>

        <div className="ns-sim-compact">
          <Num label="Concurrent sessions (N)" value={cfg.concurrency} min={1}
            max={cfg.mode === "browser" ? BROWSER_MAX_CONCURRENCY : 50}
            onChange={(v) => set("concurrency", v)} hint="Pool size = concurrent journeys" />
          <Range2 label="Wait between journeys (s)" lo={cfg.wait_min_s} hi={cfg.wait_max_s}
            onLo={(v) => set("wait_min_s", v)} onHi={(v) => set("wait_max_s", v)} step={0.5} />
          <Range2 label="Think-time between actions (s)" lo={cfg.think_min_s} hi={cfg.think_max_s}
            onLo={(v) => set("think_min_s", v)} onHi={(v) => set("think_max_s", v)} step={0.1} />
          <Range2 label="Actions per journey" lo={cfg.actions_min} hi={cfg.actions_max}
            onLo={(v) => set("actions_min", v)} onHi={(v) => set("actions_max", v)} step={1} ints />
        </div>

        <Slider label="Concierge usage" value={cfg.concierge_pct} onChange={(v) => set("concierge_pct", v)} />
        <Slider label="Problem injection" value={cfg.problem_pct} onChange={(v) => set("problem_pct", v)} />

        <div className="ns-sim-field">
          <label>Inject which problems</label>
          <div className="ns-sim-checks">
            {PROBLEMS.map((p) => (
              <label key={p.key} className="ns-sim-check">
                <input type="checkbox" checked={cfg.problems.includes(p.key)}
                  onChange={() => toggleProblem(p.key)} />
                {p.label}
              </label>
            ))}
          </div>
        </div>

        <div className="ns-sim-compact">
          <Mix label="Category mix (weights)" keys={CATEGORIES as unknown as string[]}
            mix={cfg.category_mix} onChange={(k, v) => setMix("category_mix", k, v)} />
          <Mix label="Created tier distribution" keys={TIERS as unknown as string[]}
            mix={cfg.tier_mix} onChange={(k, v) => setMix("tier_mix", k, v)} />
        </div>

        <div className="ns-sim-field">
          <label>Speed</label>
          <div className="ns-sim-seg">
            {SPEEDS.map((sp) => (
              <button type="button" key={sp.value} className={cfg.speed === sp.value ? "on" : ""}
                onClick={() => set("speed", sp.value)}>
                {sp.label}
              </button>
            ))}
          </div>
        </div>

        <div className="ns-sim-field">
          <label>Target</label>
          <div className="ns-sim-row">
            <select value={cfg.target_kind} onChange={(e) => set("target_kind", e.target.value as SimConfig["target_kind"])}>
              <option value="none">Run until stopped</option>
              <option value="orders">Stop after N orders</option>
              <option value="duration">Stop after N seconds</option>
            </select>
            {cfg.target_kind !== "none" && (
              <input type="number" min={1} value={cfg.target_value}
                onChange={(e) => set("target_value", Math.max(0, Number(e.target.value) || 0))}
                aria-label="Target value" />
            )}
          </div>
        </div>

        <label className="ns-sim-check ns-sim-reset">
          <input type="checkbox" checked={cfg.reset} onChange={(e) => set("reset", e.target.checked)} />
          Clear all orders before starting
        </label>
      </fieldset>
    </div>
  );
}

// --- campos reutilizáveis ---------------------------------------------------
function Num({ label, value, min, max, onChange, hint }: {
  label: string; value: number; min: number; max: number; onChange: (v: number) => void; hint?: string;
}) {
  return (
    <div className="ns-sim-field">
      <label>{label}</label>
      <input type="number" min={min} max={max} value={value}
        onChange={(e) => onChange(Math.max(min, Math.min(max, Number(e.target.value) || min)))} />
      {hint && <span className="ns-sim-sub">{hint}</span>}
    </div>
  );
}

function Range2({ label, lo, hi, onLo, onHi, step, ints }: {
  label: string; lo: number; hi: number; onLo: (v: number) => void; onHi: (v: number) => void;
  step: number; ints?: boolean;
}) {
  const parse = (s: string) => (ints ? Math.max(0, Math.round(Number(s) || 0)) : Math.max(0, Number(s) || 0));
  return (
    <div className="ns-sim-field">
      <label>{label}</label>
      <div className="ns-sim-row">
        <input type="number" min={0} step={step} value={lo} onChange={(e) => onLo(parse(e.target.value))}
          aria-label={`${label} min`} />
        <span className="ns-sim-dash">to</span>
        <input type="number" min={0} step={step} value={hi} onChange={(e) => onHi(parse(e.target.value))}
          aria-label={`${label} max`} />
      </div>
    </div>
  );
}

function Slider({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <div className="ns-sim-field">
      <label>{label} <b className="ns-sim-pct">{value}%</b></label>
      <input type="range" min={0} max={100} value={value}
        onChange={(e) => onChange(Number(e.target.value))} className="ns-sim-range" />
    </div>
  );
}

function Mix({ label, keys, mix, onChange }: {
  label: string; keys: string[]; mix: Record<string, number>; onChange: (k: string, v: number) => void;
}) {
  return (
    <div className="ns-sim-field">
      <label>{label}</label>
      <div className="ns-sim-mix">
        {keys.map((k) => (
          <div key={k} className="ns-sim-mixrow">
            <span>{k}</span>
            <input type="number" min={0} max={100} value={mix[k] ?? 0}
              onChange={(e) => onChange(k, Number(e.target.value) || 0)} aria-label={`${label} ${k}`} />
          </div>
        ))}
      </div>
    </div>
  );
}
