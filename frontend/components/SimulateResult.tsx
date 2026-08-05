"use client";
import type { GalileoConfig } from "@/lib/api";
import type { SimulateRunResult } from "@/lib/workshop-simulate";
import { buildConsoleLinks } from "@/lib/galileo-workshop";

export default function SimulateResult({
  result,
  sessionId,
  galileo,
}: {
  result: SimulateRunResult | null;
  sessionId: string | null;
  galileo: GalileoConfig | null;
}) {
  if (!result) return null;

  const consoleUrl = galileo?.enabled ? buildConsoleLinks(galileo).logStream : null;

  return (
    <div className={`ns-sim-result${result.ok ? " ok" : " fail"}`} role="status">
      <p className="ns-sim-result-head">{result.ok ? "Simulate complete" : "Simulate finished with errors"}</p>
      <ul className="ns-sim-result-steps">
        {result.steps.map((step) => (
          <li key={step.id} className={step.ok ? "ok" : "fail"}>
            <strong>{step.label}</strong>
            {step.request && (
              <div className="ns-sim-request">
                <code>{step.request}</code>
              </div>
            )}
            {step.ok ? (
              <>
                {step.summary}
                {step.grounded === false && <span className="ns-sim-flag"> · grounded=false</span>}
                {step.orderStatus && <span className="ns-sim-flag"> · status={step.orderStatus}</span>}
                {step.intent && <span className="ns-sim-flag"> · intent={step.intent}</span>}
              </>
            ) : (
              <> — {step.error}</>
            )}
          </li>
        ))}
      </ul>
      <p className="ns-sim-result-hint">
        Trace should appear in Splunk Agent Observability Console under this session
        {sessionId ? `: ${sessionId.slice(0, 8)}…` : "."}
        {consoleUrl && (
          <>
            {" "}
            <a href={consoleUrl} target="_blank" rel="noopener noreferrer" className="ns-sim-console-link">
              Open Console
            </a>
          </>
        )}
      </p>
    </div>
  );
}
