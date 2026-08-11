"use client";
// Portal shell (account + admin) — AppNav sidebar when logged in. Stable structure so it doesn't
// remount children on login (preserves the ?return= redirect post-auth).
// Protected routes without a session (e.g. token expired + refresh) → store home.
import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import AppNav from "@/components/AppNav";

function portalRequiresAuth(pathname: string): boolean {
  if (pathname === "/account") return false;
  if (pathname.startsWith("/account/")) return true;
  if (pathname.startsWith("/admin")) return true;
  return false;
}

export default function PortalShell({ children }: { children: React.ReactNode }) {
  const { user, ready } = useAuth();
  const pathname = usePathname() || "/";
  const router = useRouter();
  const needsAuth = portalRequiresAuth(pathname);

  useEffect(() => {
    if (!ready || user || !needsAuth) return;
    router.replace("/");
  }, [ready, user, needsAuth, router]);

  if (!ready || (!user && needsAuth)) {
    return (
      <div className="ns-account-page">
        <div className="ns-center">
          <span className="ns-spinner" aria-hidden />
        </div>
      </div>
    );
  }

  return (
    <div className={`ns-adm-shell ns-portal${user ? "" : " guest"}`}>
      {user && <AppNav />}
      <section className={user ? "ns-adm-content" : "ns-portal-guest"}>{children}</section>
    </div>
  );
}
