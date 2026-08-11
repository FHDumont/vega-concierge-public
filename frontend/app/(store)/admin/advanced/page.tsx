"use client";
// Advanced problem toggles — migrated from Behind the Scenes (F-NAV-1). Owner-only.
import { useEffect, useState } from "react";
import {
  getProblems,
  setProblems,
  getGalileoConfig,
  getShopperSessionId,
  configureShopperSession,
  resetShopperSession,
  Problems,
  GalileoConfig,
} from "@/lib/api";
import ProblemPanel from "@/components/ProblemPanel";
import GalileoStatusBanner from "@/components/GalileoStatusBanner";
import { useAuth } from "@/lib/auth";

export default function AdvancedPage() {
  const { user, ready } = useAuth();

  if (!ready) {
    return (
      <div className="ns-adm-wrap">
        <div className="ns-center"><span className="ns-spinner" aria-hidden /></div>
      </div>
    );
  }

  if (!user || user.role !== "OWNER") {
    return (
      <div className="ns-adm-wrap">
        <div className="ns-adm-top">
          <div>
            <h1>Advanced</h1>
            <p className="sub">Owner only.</p>
          </div>
        </div>
        <div className="ns-adm-card">
          <p className="ns-adm-empty">You don’t have access to advanced workshop settings.</p>
        </div>
      </div>
    );
  }

  return <AdvancedContent />;
}

function AdvancedContent() {
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

  async function toggle(key: string) {
    setP(await setProblems({ ...problems, [key]: !problems[key] }));
  }

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
    <div className="ns-adm-wrap">
      <div className="ns-adm-top">
        <div>
          <h1>Advanced</h1>
          <p className="sub">Fine-grained problem toggles and presets for workshop scenarios.</p>
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

      <ProblemPanel
        problems={problems}
        onToggle={toggle}
        onPresetApplied={applyFlags}
        {...shared}
      />
    </div>
  );
}
