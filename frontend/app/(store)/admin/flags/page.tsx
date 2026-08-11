"use client";
// FEATURE FLAGS — OWNER-only screen. Turns menu areas that PARTICIPANTS see on/off
// (unlocks Behind the Scenes only when teaching, hides Admin/Simulator, controls the
// Inspector). Owner-gated in the UI; the backend is the edit boundary (401/403). The flags are
// served from the SAME config source (F-026): in `remote` mode the HUB wins — what the owner
// edits here is the LOCAL value, overridden by the hub (the screen warns about this). Refreshes
// the global state after editing (the menu/routes react on their own). Classes ns-adm-* + ns-ff-switch.
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useFlags } from "@/lib/flags";
import { AdminFlags, FeatureFlags, getAdminFlags, setFlags } from "@/lib/api";

// Order + copy (English). `key` matches the backend's FeatureFlags.
// `controls` = what the flag shows/hides; `example` = when to use it in the workshop.
const FLAGS: {
  key: keyof FeatureFlags;
  label: string;
  icon: string;
  desc: string;
  controls: string;
  example: string;
}[] = [
  {
    key: "behind_the_scenes",
    label: "Use cases",
    icon: "🔬",
    desc: "Workshop use-case scenarios — load presets, simulate requests, inspect in Splunk Agent Observability.",
    controls: "Hides the “Use Cases” button in the store header and blocks /use-cases.",
    example: "Keep it OFF during the intro so the store looks like a normal shop; flip it ON when you start the workshop scenarios.",
  },
  {
    key: "admin",
    label: "Admin",
    icon: "🛠",
    desc: "The business admin layer — overview, orders, products — plus every workshop and owner tool nested under it.",
    controls: "Hides the “Admin” item and blocks the whole /admin/* area.",
    example: "Turn OFF so participants can’t wander into the back office; you (owner) still see it — flags never block the owner.",
  },
  {
    key: "simulator",
    label: "Simulator",
    icon: "⚙",
    desc: "The concurrent-sessions traffic simulator that drives synthetic checkouts and load.",
    controls: "Hides the “Simulator” item and blocks /admin/simulator.",
    example: "Turn ON for the load/“blast radius” exercise; OFF the rest of the time to keep the store quiet and the data clean.",
  },
  {
    key: "inspector",
    label: "LLM Inspector",
    icon: "🔍",
    desc: "The local LLM activity capture (F-023) — a live feed of every model call and cache hit.",
    controls: "Hides the “Inspector” item in Global Settings and pauses capture in the backend (saves memory).",
    example: "Turn ON to show prompts, tokens and cache hits while explaining cost control; OFF to stop recording entirely.",
  },
];

export default function FeatureFlagsPage() {
  const { user, ready } = useAuth();
  if (!ready) return <Gate msg="Loading…" />;
  if (!user) return <Gate msg="Sign in as the owner to manage feature flags." cta />;
  if (user.role !== "OWNER")
    return <Gate msg="Owner only — you don’t have access to feature flags." />;
  return <FlagsManager />;
}

function Gate({ msg, cta }: { msg: string; cta?: boolean }) {
  return (
    <div className="ns-adm-wrap">
      <div className="ns-adm-top">
        <div>
          <h1>Feature Flags</h1>
          <p className="sub">What participants see in the menu — owner only.</p>
        </div>
      </div>
      <div className="ns-adm-card">
        <p className="ns-adm-empty">{msg}</p>
        {cta && <p style={{ marginTop: 10 }}><a className="ns-adm-btn primary" href="/account">Go to sign in</a></p>}
      </div>
    </div>
  );
}

function FlagsManager() {
  const { refresh: refreshGlobal } = useFlags(); // reflects in this session's menu/routes (owner)
  const [data, setData] = useState<AdminFlags | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await getAdminFlags());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load flags");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function toggle(key: keyof FeatureFlags) {
    if (!data) return;
    setBusy(key);
    setError(null);
    try {
      const next = !data.local[key];
      const updated = await setFlags({ [key]: next });
      setData(updated);
      await refreshGlobal(); // AppNav/guards react to the new effective flag
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to update flag");
    } finally {
      setBusy(null);
    }
  }

  const remote = data?.source === "remote";

  return (
    <div className="ns-adm-wrap">
      <div className="ns-adm-top">
        <div>
          <h1>Feature Flags</h1>
          <p className="sub">Toggle which menu surfaces participants can see and reach.</p>
        </div>
      </div>

      {error && <div className="ns-adm-card"><p className="ns-adm-empty">{error}</p></div>}

      {remote && (
        <div className="ns-ff-hub">
          <span className="ns-ff-hub-icon" aria-hidden>🔗</span>
          <p>
            This store pulls config from a <strong>hub</strong> (source: remote). The hub’s flags
            win — your edits below set the <em>local</em> values, which only take effect if this
            store goes back to local. The owner is never blocked by flags.
          </p>
        </div>
      )}

      <ul className="ns-ff-list">
        {FLAGS.map(({ key, label, icon, desc, controls, example }) => {
          const localOn = !!data?.local[key];
          const effOn = !!data?.effective[key];
          const override = remote && effOn !== localOn;
          return (
            <li key={key} className={`ns-ff-card${localOn ? " on" : ""}`}>
              <div className="ns-ff-card-main">
                <span className="ns-ff-icon" aria-hidden>{icon}</span>
                <div className="ns-ff-body">
                  <div className="ns-ff-titlerow">
                    <h2 className="ns-ff-title">{label}</h2>
                    {override && (
                      <span className="ns-ff-override">hub override: {effOn ? "ON" : "OFF"}</span>
                    )}
                  </div>
                  <p className="ns-ff-desc">{desc}</p>
                  <dl className="ns-ff-detail">
                    <div>
                      <dt>Controls</dt>
                      <dd>{controls}</dd>
                    </div>
                    <div>
                      <dt>Example</dt>
                      <dd>{example}</dd>
                    </div>
                  </dl>
                </div>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={localOn}
                aria-label={`Toggle ${label}`}
                disabled={!data || busy === key}
                onClick={() => toggle(key)}
                className={`ns-ff-switch${localOn ? " on" : ""}`}
              >
                <span className="ns-ff-switch-track">
                  <span className="knob" />
                </span>
                <span className="ns-ff-switch-label">{localOn ? "Visible — ON" : "Hidden — OFF"}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
