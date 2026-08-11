"use client";
// Menu/surfaces feature flags (F-033) — global client-side state. Reads the EFFECTIVE flags
// (`GET /api/flags`, public) so the frontend can decide what to show in the menu and which
// routes to block for PARTICIPANTS. In `remote` mode these flags come from the hub (propagates
// to the 150 VMs) — hence the light poll: the owner toggles on the hub and the stores reflect it in seconds.
//
// The OWNER is never blocked (ADR-021): the gate is only about participant visibility; the
// consuming component (`useFlags`) combines with `useAuth` to let the owner through. Here we
// only serve the effective flags plus a `refresh` (the toggles screen calls it after editing).
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { FeatureFlags, getFlags } from "./api";

// OPTIMISTIC default = everything ON (standalone-first: nothing disappears until the backend says so).
// Avoids hiding items for a flash before the 1st fetch.
const ALL_ON: FeatureFlags = { behind_the_scenes: true, admin: true, simulator: true, inspector: true };

const POLL_MS = 15000; // hub → stores propagation (remote mode); cheap (no secret, no auth)

type FlagsContextValue = { flags: FeatureFlags; ready: boolean; refresh: () => Promise<void> };

const FlagsContext = createContext<FlagsContextValue | null>(null);

export function FlagsProvider({ children }: { children: React.ReactNode }) {
  const [flags, setFlags] = useState<FeatureFlags>(ALL_ON);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setFlags(await getFlags());
    } catch {
      /* backend unreachable — keeps the last value (doesn't hide anything on network failure) */
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, POLL_MS);
    return () => clearInterval(id);
  }, [refresh]);

  const value = useMemo<FlagsContextValue>(() => ({ flags, ready, refresh }), [flags, ready, refresh]);
  return <FlagsContext.Provider value={value}>{children}</FlagsContext.Provider>;
}

export function useFlags(): FlagsContextValue {
  const ctx = useContext(FlagsContext);
  if (!ctx) throw new Error("useFlags must be used within FlagsProvider");
  return ctx;
}
