"use client";
// Shared state for workshop toggles/scenarios — header + /use-cases.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { getProblems, type Problems } from "@/lib/api";
import { WORKSHOP_UCS } from "@/lib/galileo-workshop";

type WorkshopProblemsContextValue = {
  problems: Problems;
  setProblems: (p: Problems) => void;
  refreshProblems: () => Promise<void>;
};

const WorkshopProblemsContext = createContext<WorkshopProblemsContextValue | null>(null);

export function WorkshopProblemsProvider({ children }: { children: ReactNode }) {
  const [problems, setProblems] = useState<Problems>({});

  const refreshProblems = useCallback(async () => {
    try {
      setProblems(await getProblems());
    } catch {
      /* offline / blocked */
    }
  }, []);

  useEffect(() => {
    refreshProblems();
  }, [refreshProblems]);

  useEffect(() => {
    function onVisible() {
      if (document.visibilityState === "visible") refreshProblems();
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refreshProblems]);

  return (
    <WorkshopProblemsContext.Provider value={{ problems, setProblems, refreshProblems }}>
      {children}
    </WorkshopProblemsContext.Provider>
  );
}

export function useWorkshopProblems(): WorkshopProblemsContextValue {
  const ctx = useContext(WorkshopProblemsContext);
  if (!ctx) {
    throw new Error("useWorkshopProblems must be used within WorkshopProblemsProvider");
  }
  return ctx;
}

/** Short label for the menu when a UC preset is active (e.g. "UC-4 ON"). */
export function activeScenarioMenuLabel(activeScenario?: string): string | null {
  if (!activeScenario) return null;
  const uc = WORKSHOP_UCS.find((u) => u.presetId === activeScenario);
  const id = uc ? uc.id.toUpperCase() : activeScenario.toUpperCase();
  return `${id} ON`;
}
