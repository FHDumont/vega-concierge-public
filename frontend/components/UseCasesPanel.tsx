"use client";
// Workshop use cases — painel UC + Splunk Agent Observability (F-NAV-1, movido p/ /use-cases no header).
import { useEffect, useState } from "react";
import {
  getProblems,
  getGalileoConfig,
  getShopperSessionId,
  configureShopperSession,
  resetShopperSession,
  GalileoConfig,
} from "@/lib/api";
import GalileoStatusBanner from "@/components/GalileoStatusBanner";
import WorkshopGuide from "@/components/WorkshopGuide";
import { useWorkshopProblems } from "@/lib/workshop-problems";

export default function UseCasesPanel() {
  const { problems, setProblems } = useWorkshopProblems();
  const [galileo, setGalileo] = useState<GalileoConfig | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [newSessionStarted, setNewSessionStarted] = useState(false);

  useEffect(() => {
    getGalileoConfig()
      .then((g) => {
        setGalileo(g);
        configureShopperSession(g.session_idle_minutes);
      })
      .catch(() => {});
    setSessionId(getShopperSessionId());
  }, []);

  function applyFlags(flags: Parameters<typeof setProblems>[0]) {
    setProblems(flags);
  }

  async function copySessionId(id?: string | null) {
    const session = id ?? getShopperSessionId();
    if (!session) return;
    try {
      await navigator.clipboard.writeText(session);
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

  function resetSessionSilent() {
    const id = resetShopperSession();
    setSessionId(id);
    void copySessionId(id);
  }

  return (
    <div className="ns-adm-wrap ns-use-cases-page">
      <div className="ns-adm-top">
        <div>
          <h1>Use cases</h1>
          <p className="sub">
            Turn ON a workshop scenario, run it in the store using the button or chips on each card, then open Splunk
            Agent Observability Console for traces and evaluators.
          </p>
        </div>
      </div>

      <GalileoStatusBanner
        config={galileo}
        sessionId={sessionId}
        onCopySession={() => copySessionId()}
        copied={copied}
        onNewSession={startNewSession}
        newSessionStarted={newSessionStarted}
      />

      <WorkshopGuide
        problems={problems}
        onPresetApplied={applyFlags}
        onSessionReset={resetSessionSilent}
      />
    </div>
  );
}
