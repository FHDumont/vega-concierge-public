"use client";
// Guarda de rota por feature flag (F-033): bloqueia o acesso de PARTICIPANTES a uma superfície
// quando a flag está OFF — não basta esconder o link do menu, a rota também tem de barrar. O
// OWNER nunca é bloqueado (ADR-021: não se autobloqueia da administração). Enquanto auth/flags
// não resolveram, mostra um placeholder neutro (sem piscar conteúdo proibido). Bloqueado →
// redireciona p/ a Loja.
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
  if (blocked) return null; // redirecionando
  return <>{children}</>;
}
