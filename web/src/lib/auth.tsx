import { createContext, useContext, useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { api, getToken, setToken } from "./api";
import type { Me } from "./types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (firm_name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthCtx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

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

  const login = useCallback(
    async (email: string, password: string) => {
      const r = await api<{ access_token: string }>("/auth/login", {
        method: "POST",
        auth: false,
        body: { email, password },
      });
      setToken(r.access_token);
      setLoading(true);
      await loadMe();
    },
    [loadMe],
  );

  const register = useCallback(
    async (firm_name: string, email: string, password: string) => {
      const r = await api<{ access_token: string }>("/auth/register", {
        method: "POST",
        auth: false,
        body: { firm_name, email, password },
      });
      setToken(r.access_token);
      setLoading(true);
      await loadMe();
    },
    [loadMe],
  );

  const logout = useCallback(() => {
    setToken(null);
    setMe(null);
  }, []);

  const value = useMemo(
    () => ({ me, loading, login, register, logout }),
    [me, loading, login, register, logout],
  );
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const ctx = useContext(AuthCtx);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
