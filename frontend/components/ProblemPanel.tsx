"use client";
// Advanced problem toggles — F-GALILEO-6: slim cards + Simulate per toggle.
import Link from "next/link";
import { useState } from "react";
import type { GalileoConfig, Problems } from "@/lib/api";
import SimulateResult from "@/components/SimulateResult";
import { PROBLEM_CARDS } from "@/lib/galileo-workshop";
import { runSimulateByKey, type SimulateRunResult } from "@/lib/workshop-simulate";

export default function ProblemPanel({
  problems,
  onToggle,
  onPresetApplied,
  galileo,
  sessionId,
}: {
  problems: Problems;
  onToggle: (key: string) => void;
  onPresetApplied?: (flags: Problems) => void;
  galileo: GalileoConfig | null;
  sessionId: string | null;
}) {
  const [simResults, setSimResults] = useState<Record<string, SimulateRunResult | null>>({});
  const [simulating, setSimulating] = useState<string | null>(null);

  async function simulate(key: string) {
    setSimulating(key);
    try {
      const result = await runSimulateByKey(key);
      setSimResults((prev) => ({ ...prev, [key]: result }));
      onPresetApplied?.(result.flags);
    } catch (e) {
      setSimResults((prev) => ({
        ...prev,
        [key]: {
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
    <div className="ns-tech-card ns-pp ns-pp-advanced">
      <div className="ns-pp-head">
        <p className="ns-bts-side-title">Advanced</p>
        <p className="ns-bts-side-sub">
          Individual failure toggles — flip the switch or hit Simulate. Workshop tab covers the five Splunk Agent Observability use cases.
        </p>
      </div>
      <ul className="ns-pp-list">
        {PROBLEM_CARDS.map((p) => {
          const on = !!problems[p.key];
          const busy = simulating === p.key;
          const simResult = simResults[p.key];

          return (
            <li key={p.key} className={`ns-pp-card sev-${p.sev}${on ? " on" : ""}`}>
              <div className="ns-pp-card-head">
                <span className="ns-pp-title">{p.label}</span>
                <div className="ns-pp-badges">
                  {p.workshopUcs?.map((uc) => (
                    <span key={uc} className="ns-pp-uc-chip">
                      {uc}
                    </span>
                  ))}
                  <span className={`ns-pp-sev sev-${p.sev}`}>{on ? "Injected" : p.sev}</span>
                </div>
              </div>

              <p className="ns-pp-oneline">{p.what}</p>

              <div className="ns-pp-card-actions">
                <button
                  type="button"
                  role="switch"
                  aria-checked={on}
                  aria-label={`Inject: ${p.label}`}
                  onClick={() => onToggle(p.key)}
                  className={`ns-pp-switch${on ? " on" : ""}`}
                >
                  <span className="ns-pp-switch-track">
                    <span className="knob" />
                  </span>
                  <span className="ns-pp-switch-label">{on ? "ON" : "OFF"}</span>
                </button>
                <button
                  type="button"
                  className="ns-bts-uc-simulate"
                  onClick={() => simulate(p.key)}
                  disabled={busy}
                >
                  {busy && <span className="ns-spinner" aria-hidden />}
                  {busy ? "Simulating…" : "Simulate"}
                </button>
              </div>

              <SimulateResult result={simResult ?? null} sessionId={sessionId} galileo={galileo} />

              <details className="ns-bts-uc-details ns-pp-details">
                <summary>Signals &amp; where to try</summary>
                <dl className="ns-pp-detail">
                  <div>
                    <dt>How it breaks</dt>
                    <dd>{p.how}</dd>
                  </div>
                  <div>
                    <dt>What to do</dt>
                    <dd>{p.whatYouDo}</dd>
                  </div>
                  <div>
                    <dt>Where to try</dt>
                    <dd className="ns-pp-where">
                      <Link href={p.whereToTry} className="ns-pp-try-link">
                        {p.whereToTry}
                      </Link>
                      {p.tryPrompt && (
                        <>
                          {" "}
                          — <code>{p.tryPrompt}</code>
                        </>
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>In the app</dt>
                    <dd>{p.signalApp}</dd>
                  </div>
                  {p.signalGalileo && (
                    <div>
                      <dt>In Splunk Agent Observability</dt>
                      <dd>
                        {p.signalGalileo}
                        {p.galileoEvaluators && p.galileoEvaluators.length > 0 && (
                          <ul className="ns-pp-eval-list" aria-label="Evaluators">
                            {p.galileoEvaluators.map((ev) => (
                              <li key={ev}>
                                <span className="ns-pp-eval-chip">{ev}</span>
                              </li>
                            ))}
                          </ul>
                        )}
                        {p.galileoProtect && (
                          <p className="ns-pp-protect">
                            <strong>Protect:</strong> {p.galileoProtect}
                          </p>
                        )}
                      </dd>
                    </div>
                  )}
                  {p.signalO11y && (
                    <div>
                      <dt>In Splunk o11y</dt>
                      <dd>{p.signalO11y}</dd>
                    </div>
                  )}
                </dl>
              </details>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
