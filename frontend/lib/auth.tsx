"use client";
// Client-side session state (F-008): logged-in user + token. The token persists in
// localStorage and is injected into the API client (setAuthToken) for authenticated
// calls (orders/history/me). DEMO auth — see ADR-011 / DT-010.
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
  ready: boolean; // session already resolved (avoids flicker before checking the saved token)
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
  saveAddress: (address: string) => Promise<void>; // saves/edits the profile address (F-011)
};

const AuthContext = createContext<AuthContextValue | null>(null);

const TOKEN_KEY = "vega.token";

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  // Restores the session from the saved token (revalidates against the backend; discards if expired).
  useEffect(() => {
    let token: string | null = null;
    try {
      token = localStorage.getItem(TOKEN_KEY);
    } catch {
      /* storage unavailable */
    }
    if (!token) {
      setReady(true);
      return;
    }
    setAuthToken(token);
    getMe()
      .then((u) => {
        if (u) setUser(u);
        else clearToken(); // invalid token (e.g. backend restarted — in-memory sessions)
      })
      .catch(() => {
        /* backend unreachable — stays logged out, without breaking the shop */
      })
      .finally(() => setReady(true));
  }, []);

  function persist(result: AuthResult) {
    setAuthToken(result.token);
    try {
      localStorage.setItem(TOKEN_KEY, result.token);
    } catch {
      /* storage unavailable — session only valid in this tab */
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
  // Reloads the user (e.g. after an order, to reflect updated spend/tier).
  async function refresh() {
    const u = await getMe().catch(() => null);
    if (u) setUser(u);
    else clearToken();
  }
  // Saves the address to the profile and reflects the updated user in state (F-011).
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
