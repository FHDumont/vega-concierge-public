"use client";
import type { GalileoConfig } from "@/lib/api";
import { buildConsoleLinks } from "@/lib/galileo-workshop";

export default function GalileoStatusBanner({
  config,
  sessionId,
  onCopySession,
  copied,
  onNewSession,
  newSessionStarted,
}: {
  config: GalileoConfig | null;
  sessionId: string | null;
  onCopySession: () => void;
  copied: boolean;
  onNewSession: () => void;
  newSessionStarted?: boolean;
}) {
  const enabled = config?.enabled ?? false;
  const links = config ? buildConsoleLinks(config) : null;

  return (
    <div className={`ns-bts-galileo-banner${enabled ? " connected" : " off"}`}>
      <div className="ns-bts-galileo-status">
        <span className="ns-bts-galileo-pill">{enabled ? "Splunk Agent Observability connected" : "Splunk Agent Observability off — set GALILEO_API_KEY"}</span>
        {enabled && config && (
          <span className="ns-bts-galileo-meta">
            {config.project} · {config.log_stream}
          </span>
        )}
      </div>
      <div className="ns-bts-galileo-actions">
        {enabled && links && (
          <>
            <a href={links.logStream} target="_blank" rel="noopener noreferrer" className="ns-bts-galileo-link">
              Open Console
            </a>
            <a href={links.agentControl} target="_blank" rel="noopener noreferrer" className="ns-bts-galileo-link">
              Agent Control
            </a>
          </>
        )}
        <button type="button" className="ns-bts-galileo-copy" onClick={onNewSession}>
          {newSessionStarted ? "New session!" : "New session"}
        </button>
        <button type="button" className="ns-bts-galileo-copy" onClick={onCopySession} disabled={!sessionId}>
          {copied ? "Copied!" : "Copy session ID"}
        </button>
      </div>
    </div>
  );
}
