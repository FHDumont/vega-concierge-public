"use client";
// Conta do cliente (F-008): deslogado → login/registro; logado → perfil (nome, e-mail,
// tier + progresso) + logout. Purchase history em /account/purchases (F-NAV-1).
// Design custom dirigido por paletas (ADR-012/013). Auth de DEMO — ADR-011 / DT-010.
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { TIER_GOLD_AT, TIER_PLATINUM_AT, formatMoney, nextTierHint } from "@/lib/shop";
import { useAuth } from "@/lib/auth";
import TierBadge from "@/components/TierBadge";
import AccountInsights from "@/components/AccountInsights";
import AuthForms from "@/components/AuthForms";

function readReturnTo(): string | null {
  if (typeof window === "undefined") return null;
  const path = new URLSearchParams(window.location.search).get("return");
  if (path?.startsWith("/") && path !== "/account") return path;
  return null;
}

function tierProgress(tier: string, spend: number): number {
  if (tier === "PLATINUM") return 100;
  const target = tier === "GOLD" ? TIER_PLATINUM_AT : TIER_GOLD_AT;
  return Math.min(100, Math.round((spend / target) * 100));
}

function AddressCard() {
  const { user, saveAddress } = useAuth();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(user?.address ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);

  if (!user) return null;
  const hasAddress = user.address.trim() !== "";

  function start() {
    setValue(user!.address);
    setError(false);
    setEditing(true);
  }
  async function save() {
    setBusy(true);
    setError(false);
    try {
      await saveAddress(value);
      setEditing(false);
    } catch {
      setError(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ns-panelcard">
      <h2 className="ns-card-title">Shipping address</h2>
      {error && (
        <div className="ns-alert error" style={{ marginBottom: 14 }} role="alert">
          We couldn’t save your address. Please try again.
        </div>
      )}
      {editing ? (
        <>
          <div className="ns-field">
            <label className="ns-label">Address</label>
            <input
              className="ns-input"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="123 Main St, City"
              autoComplete="street-address"
            />
          </div>
          <div className="ns-btn-row" style={{ marginTop: 4 }}>
            <button type="button" className="ns-btn-ghost block" disabled={busy} onClick={() => setEditing(false)}>
              Cancel
            </button>
            <button type="button" className="ns-btn-primary block" disabled={busy || value.trim() === ""} onClick={save}>
              {busy && <span className="ns-spinner" aria-hidden />}
              Save address
            </button>
          </div>
        </>
      ) : (
        <div className="ns-address-row">
          <p className={hasAddress ? undefined : "ns-muted"} style={{ margin: 0 }}>
            {hasAddress ? user.address : "No saved address yet. Add one to speed up checkout."}
          </p>
          <button type="button" className="ns-btn-ghost" onClick={start}>
            {hasAddress ? "Edit" : "Add address"}
          </button>
        </div>
      )}
    </section>
  );
}

function Profile() {
  const { user, logout, refresh } = useAuth();

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    function onVisible() {
      if (document.visibilityState === "visible") refresh();
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!user) return null;
  const hint = nextTierHint(user.tier, user.spend);
  const initial = user.name.trim().charAt(0).toUpperCase() || "?";

  return (
    <div className="ns-account-page">
      <h1>Account</h1>

      <section className="ns-panelcard">
        <div className="ns-profile">
          <div className="who">
            <div className="ns-avatar" aria-hidden>
              {initial}
            </div>
            <div>
              <div className="nm">{user.name}</div>
              <div className="em">{user.email}</div>
            </div>
          </div>
          <div className="right">
            <TierBadge tier={user.tier} />
            <span className="ns-muted" style={{ fontSize: 13 }}>
              Total spent: {formatMoney(user.spend)}
            </span>
          </div>
        </div>

        {hint && (
          <div className="ns-progress">
            <div className="ns-muted" style={{ fontSize: 13 }}>
              {hint}
            </div>
            <div className="track">
              <div className="fill" style={{ width: `${tierProgress(user.tier, user.spend)}%` }} />
            </div>
          </div>
        )}

        <div style={{ marginTop: 18 }}>
          <button type="button" className="ns-btn-ghost" onClick={logout}>
            Sign out
          </button>
        </div>
      </section>

      <AccountInsights />

      <AddressCard />
    </div>
  );
}

export default function AccountPage() {
  const { user, ready } = useAuth();
  const router = useRouter();
  const [returnTo, setReturnTo] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);

  useEffect(() => {
    setReturnTo(readReturnTo());
  }, []);

  // Já logado com ?return= (ex. refresh) — ou fallback se o submit não redirecionou.
  useEffect(() => {
    if (!ready || !user) return;
    const dest = readReturnTo();
    if (!dest) return;
    setRedirecting(true);
    router.replace(dest);
  }, [ready, user, router]);

  if (!ready || redirecting) {
    return (
      <div className="ns-account-page">
        <div className="ns-center">
          <span className="ns-spinner" aria-hidden />
        </div>
      </div>
    );
  }
  return user ? <Profile /> : <AuthForms returnTo={returnTo} />;
}
