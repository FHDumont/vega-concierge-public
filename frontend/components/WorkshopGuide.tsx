"use client";
import Link from "next/link";
import type { Problems } from "@/lib/api";
import { applyProblemPreset, setProblems } from "@/lib/api";
import { WORKSHOP_UCS, GALILEO_EVALUATOR_INFO, problemKeysForPreset, type Enforcement } from "@/lib/galileo-workshop";
import { useChat } from "@/lib/chat-context";

const ENFORCEMENT_LABEL: Record<Enforcement, string> = {
  observe: "Observe",
  block: "Block",
  steer: "Steer",
};

export default function WorkshopGuide({
  problems,
  onPresetApplied,
  onSessionReset,
}: {
  problems: Problems;
  onPresetApplied: (flags: Problems) => void;
  onSessionReset?: () => void;
}) {
  const chat = useChat();

  async function toggleScenario(ucId: string, presetId: string, turnOn: boolean) {
    if (turnOn) {
      onSessionReset?.();
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

  return (
    <div className="ns-tech-card ns-pp ns-bts-workshop">
      <div className="ns-pp-head ns-bts-workshop-head">
        <div className="ns-bts-workshop-intro">
          <p className="ns-bts-side-title">Workshop scenarios</p>
          <p className="ns-bts-side-sub">
            Five Splunk Agent Observability use cases — one scenario at a time. Turn ON to inject the failure, run it
            in the store using the button or chips below, then open Console with the session ID above.
          </p>
        </div>
        <button type="button" className="ns-bts-workshop-clear" onClick={clearAll}>
          Clear all scenarios
        </button>
      </div>

      <ul className="ns-pp-list">
        {WORKSHOP_UCS.map((uc) => {
          const active = problems.active_scenario === uc.presetId;
          const ucLabel = uc.id.toUpperCase();

          return (
            <li key={uc.id} className={`ns-pp-card${active ? " on" : ""}`}>
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

              <p className="ns-pp-summary">{uc.summary}</p>

              <ol className="ns-pp-steps">
                {uc.steps.map((step, index) => (
                  <li key={step}>
                    <span className="ns-pp-step-label">Step {index + 1}.</span> {step}
                  </li>
                ))}
              </ol>

              {uc.navigateAction && (
                <div className="ns-pp-nav-action">
                  <Link href={uc.navigateAction.href} className="ns-pp-action-btn">
                    {uc.navigateAction.label} →
                  </Link>
                  {uc.navigateAction.loginHint && (
                    <p className="ns-pp-nav-hint">{uc.navigateAction.loginHint}</p>
                  )}
                </div>
              )}

              {uc.chatPrompts && uc.chatPrompts.length > 0 && (
                <div className="ns-pp-chat-prompts">
                  <p className="ns-pp-chat-prompts-label">Try in chatbot</p>
                  <div className="ns-concierge-chips">
                    {uc.chatPrompts.map((chip) => (
                      <button
                        key={chip.question}
                        type="button"
                        className="ns-chip"
                        onClick={async () => {
                          if (problems.active_scenario !== uc.presetId) {
                            onSessionReset?.();
                            onPresetApplied(await applyProblemPreset(uc.presetId));
                          }
                          chat.openChat({ seed: chip.question });
                        }}
                      >
                        {chip.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="ns-pp-console-check">
                <p className="ns-pp-console-check-label">Verify in Console</p>
                <p>{uc.consoleCheck}</p>
                <div className="ns-pp-eval-defs" aria-label="Galileo evaluators">
                  {uc.evaluators.map((ev) => {
                    const info = GALILEO_EVALUATOR_INFO[ev];
                    return (
                      <div key={ev} className="ns-pp-eval-def">
                        <span className="ns-pp-eval-chip">{ev}</span>
                        {info && <p className="ns-pp-eval-def-summary">{info.summary}</p>}
                      </div>
                    );
                  })}
                </div>
              </div>

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
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
