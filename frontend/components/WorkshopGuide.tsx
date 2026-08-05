"use client";
import Link from "next/link";
import { useState } from "react";
import type { GalileoConfig, Problems } from "@/lib/api";
import { applyProblemPreset, setProblems } from "@/lib/api";
import SimulateResult from "@/components/SimulateResult";
import { WORKSHOP_UCS, problemKeysForPreset, type Enforcement } from "@/lib/galileo-workshop";
import { runSimulateByUc, type SimulateRunResult } from "@/lib/workshop-simulate";

const ENFORCEMENT_LABEL: Record<Enforcement, string> = {
  observe: "Observe",
  block: "Block",
  steer: "Steer",
};

export default function WorkshopGuide({
  problems,
  onPresetApplied,
  galileo,
  sessionId,
}: {
  problems: Problems;
  onPresetApplied: (flags: Problems) => void;
  galileo: GalileoConfig | null;
  sessionId: string | null;
}) {
  const [simResults, setSimResults] = useState<Record<string, SimulateRunResult | null>>({});
  const [simulating, setSimulating] = useState<string | null>(null);

  async function toggleScenario(ucId: string, presetId: string, turnOn: boolean) {
    if (turnOn) {
      onPresetApplied(await applyProblemPreset(presetId));
      return;
    }
    const keys = problemKeysForPreset(presetId);
    const next: Problems = { ...problems };
    for (const key of keys) {
      next[key] = false;
    }
    next.active_scenario = "";
    onPresetApplied(await setProblems(next));
  }

  async function clearAll() {
    onPresetApplied(await applyProblemPreset("clear"));
  }

  async function simulate(ucId: string) {
    setSimulating(ucId);
    try {
      const result = await runSimulateByUc(ucId);
      setSimResults((prev) => ({ ...prev, [ucId]: result }));
      onPresetApplied(result.flags);
    } catch (e) {
      setSimResults((prev) => ({
        ...prev,
        [ucId]: {
          ok: false,
          summary: e instanceof Error ? e.message : String(e),
          steps: [],
          flags: problems,
        },
      }));
    } finally {
      setSimulating(null);
    }
  }

  return (
    <div className="ns-tech-card ns-pp ns-bts-workshop">
      <div className="ns-pp-head ns-bts-workshop-head">
        <div className="ns-bts-workshop-intro">
          <p className="ns-bts-side-title">Workshop scenarios</p>
          <p className="ns-bts-side-sub">
            Five Splunk Agent Observability use cases — one scenario at a time. Turn ON to inject the failure, Simulate to run the real
            store request, then open Console with the session ID above.
          </p>
        </div>
        <button type="button" className="ns-bts-workshop-clear" onClick={clearAll}>
          Clear all scenarios
        </button>
      </div>

      <ul className="ns-pp-list">
        {WORKSHOP_UCS.map((uc) => {
          const active = problems.active_scenario === uc.presetId;
          const simResult = simResults[uc.id];
          const busy = simulating === uc.id;
          const ucLabel = uc.id.toUpperCase();

          return (
            <li key={uc.id} className={`ns-pp-card sev-${uc.sev}${active ? " on" : ""}`}>
              <div className="ns-pp-card-head">
                <span className="ns-pp-title">{uc.shortTitle}</span>
                <div className="ns-pp-badges">
                  <span className="ns-pp-uc-chip">{ucLabel}</span>
                  <span className={`ns-bts-uc-enforcement enforcement-${uc.enforcement}`}>
                    {ENFORCEMENT_LABEL[uc.enforcement]}
                  </span>
                  <span className={`ns-pp-sev sev-${uc.sev}`}>{active ? "Active" : uc.sev}</span>
                </div>
              </div>

              <dl className="ns-pp-detail ns-pp-detail-visible">
                <div>
                  <dt>What happens</dt>
                  <dd>{uc.what}</dd>
                </div>
                <div>
                  <dt>How it breaks</dt>
                  <dd>{uc.how}</dd>
                </div>
                <div>
                  <dt>Where to try</dt>
                  <dd className="ns-pp-where">
                    <Link href={uc.storePath} className="ns-pp-try-link">
                      {uc.storePath}
                    </Link>
                    {uc.tryPrompt && (
                      <>
                        {" "}
                        — prompt: <code>{uc.tryPrompt}</code>
                      </>
                    )}
                  </dd>
                </div>
                <div>
                  <dt>In the app</dt>
                  <dd>{uc.signalApp}</dd>
                </div>
                <div>
                  <dt>In Splunk Agent Observability</dt>
                  <dd>
                    {uc.signalGalileo}
                    <ul className="ns-pp-eval-list" aria-label="Evaluators to enable">
                      {uc.evaluators.map((ev) => (
                        <li key={ev}>
                          <span className="ns-pp-eval-chip">{ev}</span>
                        </li>
                      ))}
                    </ul>
                    {uc.galileoProtect && (
                      <p className="ns-pp-protect">
                        <strong>Protect:</strong> {uc.galileoProtect}
                      </p>
                    )}
                  </dd>
                </div>
              </dl>

              {uc.toggleKeys.length > 0 && (
                <p className="ns-bts-uc-toggles-label">
                  Toggles when active:{" "}
                  {uc.toggleKeys.map((key) => (
                    <code key={key} className={problems[key] === true ? "on" : ""}>
                      {key}
                    </code>
                  ))}
                </p>
              )}

              <div className="ns-pp-card-actions">
                <button
                  type="button"
                  role="switch"
                  aria-checked={active}
                  aria-label={`Scenario ${ucLabel}: ${active ? "on" : "off"}`}
                  onClick={() => toggleScenario(uc.id, uc.presetId, !active)}
                  className={`ns-pp-switch${active ? " on" : ""}`}
                >
                  <span className="ns-pp-switch-track">
                    <span className="knob" />
                  </span>
                  <span className="ns-pp-switch-label">{active ? "Scenario ON" : "Scenario OFF"}</span>
                </button>
                <button
                  type="button"
                  className="ns-bts-uc-simulate"
                  onClick={() => simulate(uc.id)}
                  disabled={busy}
                >
                  {busy && <span className="ns-spinner" aria-hidden />}
                  {busy ? "Simulating…" : "Simulate"}
                </button>
              </div>

              <SimulateResult result={simResult ?? null} sessionId={sessionId} galileo={galileo} />

              {uc.hints && uc.hints.length > 0 && (
                <details className="ns-bts-uc-details">
                  <summary>Tips</summary>
                  {uc.hints.map((hint) => (
                    <p key={hint} className="ns-bts-uc-hint">
                      {hint}
                    </p>
                  ))}
                </details>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
