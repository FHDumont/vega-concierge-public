"use client";
// Login/registro compartilhado (F-008) — conta e gate de checkout.
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

const TEST_ACCOUNTS: { label: string; email: string; password: string }[] = [
  { label: "Demo", email: "demo@vega.test", password: "demo1234" },
  { label: "Owner", email: "fernando@fernando.com.br", password: "owner1234" },
];

export default function AuthForms({
  returnTo,
  heroTitle = "Shopping, made effortless.",
  heroBody = "Sign in to track orders, unlock member tiers, and let our AI concierge curate picks for you.",
  formTitle,
  formSub,
}: {
  returnTo?: string | null;
  heroTitle?: string;
  heroBody?: string;
  formTitle?: string;
  formSub?: string;
}) {
  const { login, register } = useAuth();
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const valid =
    /.+@.+\..+/.test(email) &&
    password.length >= 4 &&
    (mode === "login" || name.trim() !== "");

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(name, email, password);
      if (returnTo?.startsWith("/")) {
        router.replace(returnTo);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  function fill(acctEmail: string, acctPassword: string) {
    setMode("login");
    setEmail(acctEmail);
    setPassword(acctPassword);
    setError(null);
  }

  const heading = formTitle ?? (mode === "login" ? "Welcome back" : "Create your account");
  const sub =
    formSub ??
    (returnTo === "/checkout"
      ? "Sign in to continue to checkout."
      : mode === "login"
        ? "Sign in to your Vega account."
        : "Join Vega — it only takes a moment.");

  return (
    <div className="ns-auth">
      <div className="ns-auth-card">
        <div className="ns-auth-hero">
          <div className="brand">
            <svg className="dot" viewBox="0 0 24 24" aria-hidden role="img" style={{ background: "transparent" }}>
              <path d="M12 1.5l2.6 7.9 7.9 2.6-7.9 2.6L12 22.5l-2.6-7.9L1.5 12l7.9-2.6z" fill="var(--accent)" />
            </svg>
            Vega
          </div>
          <div>
            <h2>{heroTitle}</h2>
            <p>{heroBody}</p>
            <div className="ns-auth-points">
              <div className="ns-auth-point">
                <span className="ic" aria-hidden>✦</span> AI concierge picks in seconds
              </div>
              <div className="ns-auth-point">
                <span className="ic" aria-hidden>🏆</span> Gold &amp; Platinum member tiers
              </div>
              <div className="ns-auth-point">
                <span className="ic" aria-hidden>📦</span> Full order history &amp; tracking
              </div>
            </div>
          </div>
        </div>

        <div className="ns-auth-form">
          <h1>{heading}</h1>
          <p className="ns-auth-sub">{sub}</p>

          <div className="ns-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={mode === "login"}
              className={`ns-tab${mode === "login" ? " on" : ""}`}
              onClick={() => setMode("login")}
            >
              Sign in
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === "register"}
              className={`ns-tab${mode === "register" ? " on" : ""}`}
              onClick={() => setMode("register")}
            >
              Register
            </button>
          </div>

          {error && (
            <div className="ns-alert error" style={{ marginBottom: 16 }} role="alert">
              {error}
            </div>
          )}

          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (valid && !busy) submit();
            }}
          >
            {mode === "register" && (
              <div className="ns-field">
                <label className="ns-label">Full name</label>
                <input
                  className="ns-input"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Jane Doe"
                  autoComplete="name"
                />
              </div>
            )}
            <div className="ns-field">
              <label className="ns-label">Email</label>
              <input
                className="ns-input"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="jane@example.com"
                autoComplete="email"
              />
            </div>
            <div className="ns-field">
              <label className="ns-label">Password</label>
              <input
                className="ns-input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••"
                autoComplete={mode === "login" ? "current-password" : "new-password"}
              />
            </div>

            <button type="submit" className="ns-btn-primary block" disabled={!valid || busy}>
              {busy && <span className="ns-spinner" aria-hidden />}
              {mode === "login" ? "Sign in" : "Create account"}
            </button>
          </form>

          <div className="ns-demo-hint">
            <b>Test accounts</b> — auto-fill the sign-in form:
            {TEST_ACCOUNTS.map((a) => (
              <div className="ns-demo-row" key={a.email}>
                {a.label === "Owner" ? (
                  <>
                    <span>
                      <b>{a.label}</b>
                    </span>
                    <button type="button" className="ns-link" onClick={() => fill(a.email, a.password)}>
                      Fill owner
                    </button>
                  </>
                ) : (
                  <>
                    <span>
                      <b>{a.label}</b> · <code>{a.email}</code> / <code>{a.password}</code>
                    </span>
                    <button type="button" className="ns-link" onClick={() => fill(a.email, a.password)}>
                      Fill {a.label.toLowerCase()}
                    </button>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
