"use client";
// Estado de sessão do cliente (F-008): usuário logado + token. O token persiste em
// localStorage e é injetado no client de API (setAuthToken) para as chamadas autenticadas
// (pedidos/histórico/me). Auth de DEMO — ver ADR-011 / DT-010.
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import {
  AuthResult,
  User,
  getMe,
  login as apiLogin,
  logout as apiLogout,
  register as apiRegister,
  setAuthToken,
  updateAddress as apiUpdateAddress,
} from "./api";

type AuthContextValue = {
  user: User | null;
  ready: boolean; // sessão já resolvida (evita flicker antes de checar o token salvo)
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  saveAddress: (address: string) => Promise<void>; // salva/edita o endereço do perfil (F-011)
};

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = "vega.token";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  // Restaura a sessão a partir do token salvo (revalida no backend; descarta se expirou).
  useEffect(() => {
    let token: string | null = null;
    try {
      token = localStorage.getItem(TOKEN_KEY);
    } catch {
      /* storage indisponível */
    }
    if (!token) {
      setReady(true);
      return;
    }
    setAuthToken(token);
    getMe()
      .then((u) => {
        if (u) setUser(u);
        else clearToken(); // token inválido (ex.: backend reiniciou — sessões em memória)
      })
      .catch(() => {
        /* backend fora do ar — mantém deslogado, sem quebrar a loja */
      })
      .finally(() => setReady(true));
  }, []);

  function persist(result: AuthResult) {
    setAuthToken(result.token);
    try {
      localStorage.setItem(TOKEN_KEY, result.token);
    } catch {
      /* storage indisponível — sessão vale só nesta aba */
    }
    setUser(result.user);
  }

  function clearToken() {
    setAuthToken(null);
    try {
      localStorage.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
    setUser(null);
  }

  async function login(email: string, password: string) {
    persist(await apiLogin(email, password));
  }
  async function register(name: string, email: string, password: string) {
    persist(await apiRegister(name, email, password));
  }
  async function logout() {
    await apiLogout().catch(() => {});
    clearToken();
  }
  // Recarrega o usuário (ex.: após um pedido, p/ refletir gasto/tier atualizados).
  async function refresh() {
    const u = await getMe().catch(() => null);
    if (u) setUser(u);
    else clearToken();
  }
  // Salva o endereço no perfil e reflete o usuário atualizado no estado (F-011).
  async function saveAddress(address: string) {
    setUser(await apiUpdateAddress(address));
  }

  const value = useMemo<AuthContextValue>(
    () => ({ user, ready, login, register, logout, refresh, saveAddress }),
    [user, ready],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
