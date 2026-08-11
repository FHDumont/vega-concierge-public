"use client";
// Feature-flag route guard (F-033): blocks PARTICIPANT access to a surface
// when the flag is OFF — hiding the menu link isn't enough, the route must also block. The
// OWNER is never blocked (ADR-021: never self-locks out of administration). While auth/flags
// haven't resolved yet, it shows a neutral placeholder (no flash of forbidden content). Blocked →
// redirects to the Store.
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useFlags } from "@/lib/flags";
import { FeatureFlags } from "@/lib/api";

export default function FlagGuard({ flag, children }: { flag: keyof FeatureFlags; children: React.ReactNode }) {
  const { user, ready: authReady } = useAuth();
  const { flags, ready: flagsReady } = useFlags();
  const router = useRouter();

  const isOwner = user?.role === "OWNER";
  const blocked = authReady && flagsReady && !isOwner && !flags[flag];

  useEffect(() => {
    if (blocked) router.replace("/");
  }, [blocked, router]);

  if (!authReady || !flagsReady) return <div className="ns-adm-empty" aria-busy="true">Loading…</div>;
  if (blocked) return null; // redirecting
  return <>{children}</>;
}
