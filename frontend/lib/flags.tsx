"use client";
// Feature flags de menu/superfícies (F-033) — estado global no cliente. Lê as flags EFETIVAS
// (`GET /api/flags`, público) p/ o front decidir o que mostrar no menu e quais rotas bloquear
// p/ os PARTICIPANTES. Em modo `remote` essas flags vêm do hub (propaga p/ as 150 VMs) — por
// isso há um poll leve: o owner liga/desliga no hub e as lojas refletem em segundos.
//
// O OWNER nunca é bloqueado (ADR-021): o gate é só de visibilidade do participante; o componente
// que consome (`useFlags`) combina com `useAuth` p/ deixar o owner passar. Aqui só servimos as
// flags efetivas + um `refresh` (a tela de toggles chama após editar).
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { FeatureFlags, getFlags } from "./api";

// Default OTIMISTA = tudo ON (standalone-first: nada some até o backend dizer). Evita esconder
// itens por um piscar antes do 1º fetch.
const ALL_ON: FeatureFlags = { behind_the_scenes: true, admin: true, simulator: true, inspector: true };

const POLL_MS = 15000; // propagação do hub → lojas (modo remote); barato (sem segredo, sem auth)

type FlagsContextValue = { flags: FeatureFlags; ready: boolean; refresh: () => Promise<void> };

const FlagsContext = createContext<FlagsContextValue | null>(null);

export function FlagsProvider({ children }: { children: React.ReactNode }) {
  const [flags, setFlags] = useState<FeatureFlags>(ALL_ON);
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setFlags(await getFlags());
    } catch {
      /* backend fora do ar — mantém o último valor (não esconde nada por falha de rede) */
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
