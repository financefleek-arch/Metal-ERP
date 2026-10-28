import { createContext, useContext, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { SESSION_ENDED, api, getToken, setToken } from "./api";
import type { Me } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  /** Take a token an auth call handed back (sign-up, password reset or change) and load the user. */
  acceptToken: (token: string) => Promise<void>;
  /** Re-read /auth/me (after a password change, or when Setup changes). */
  refresh: () => Promise<void>;
  logout: () => void;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const qc = useQueryClient();

  const loadMe = useCallback(async () => {
    if (!getToken()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      setMe(await api<Me>("/auth/me"));
    } catch {
      setToken(null);
      setMe(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadMe();
  }, [loadMe]);

  const acceptToken = useCallback(
    async (token: string) => {
      setToken(token);
      setLoading(true);
      await loadMe();
    },
    [loadMe],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const r = await api<{ access_token: string }>("/auth/login", {
        method: "POST",
        auth: false,
        body: { email, password },
      });
      await acceptToken(r.access_token);
    },
    [acceptToken],
  );

  const refresh = useCallback(async () => {
    await loadMe();
  }, [loadMe]);

  const logout = useCallback(() => {
    setToken(null);
    setMe(null);
    qc.clear();
  }, [qc]);

  useEffect(() => {
    window.addEventListener(SESSION_ENDED, logout);
    return () => window.removeEventListener(SESSION_ENDED, logout);
  }, [logout]);

  const value = useMemo(
    () => ({ me, loading, login, acceptToken, refresh, logout }),
    [me, loading, login, acceptToken, refresh, logout],
  );
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
