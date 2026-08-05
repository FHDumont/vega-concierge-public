"use client";
// Workshop use cases — painel UC + Splunk Agent Observability (F-NAV-1, movido p/ /use-cases no header).
import { useEffect, useState } from "react";
import {
  getProblems,
  getGalileoConfig,
  getShopperSessionId,
  configureShopperSession,
  resetShopperSession,
  Problems,
  GalileoConfig,
} from "@/lib/api";
import GalileoStatusBanner from "@/components/GalileoStatusBanner";
import WorkshopGuide from "@/components/WorkshopGuide";

export default function UseCasesPanel() {
  const [problems, setP] = useState<Problems>({});
  const [galileo, setGalileo] = useState<GalileoConfig | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [newSessionStarted, setNewSessionStarted] = useState(false);

  useEffect(() => {
    getProblems().then(setP).catch(() => {});
    getGalileoConfig()
      .then((g) => {
        setGalileo(g);
        configureShopperSession(g.session_idle_minutes);
      })
      .catch(() => {});
    setSessionId(getShopperSessionId());
  }, []);

  function applyFlags(flags: Problems) {
    setP(flags);
  }

  async function copySessionId() {
    const id = getShopperSessionId();
    if (!id) return;
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked */
    }
  }

  function startNewSession() {
    const id = resetShopperSession();
    setSessionId(id);
    setNewSessionStarted(true);
    setTimeout(() => setNewSessionStarted(false), 2000);
  }

  const shared = { galileo, sessionId };

  return (
    <div className="ns-adm-wrap ns-use-cases-page">
      <div className="ns-adm-top">
        <div>
          <h1>Use cases</h1>
          <p className="sub">
            Load a workshop scenario, simulate the real request, then open Splunk Agent Observability Console for traces and evaluators.
          </p>
        </div>
      </div>

      <GalileoStatusBanner
        config={galileo}
        sessionId={sessionId}
        onCopySession={copySessionId}
        copied={copied}
        onNewSession={startNewSession}
        newSessionStarted={newSessionStarted}
      />

      <WorkshopGuide problems={problems} onPresetApplied={applyFlags} {...shared} />
    </div>
  );
}
