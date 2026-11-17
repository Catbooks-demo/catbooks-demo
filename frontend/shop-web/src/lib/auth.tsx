import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, onSessionCleared, storeToken, storedToken } from "./api";
import type { Me, SignupInput } from "./types";

interface AuthValue {
  user: Me | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  signup: (input: SignupInput) => Promise<void>;
  logout: () => Promise<void>;
  /** 시험용: `SHOP_DEMO_LOGIN=on`인 서버에서 옛 데모 구매자로 로그인한다. */
  demoLogin: (buyerId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

/** 로그인 토큰(localStorage)이 있으면 `GET /api/shop/me`로 본인을 다시 받는다. 무효한 토큰은 api.ts가 지운다. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [ready, setReady] = useState(() => !storedToken());

  const refresh = useCallback(async () => {
    if (!storedToken()) { setUser(null); return; }
    try {
      setUser(await api.me());
    } catch (e) {
      const status = (e as { status?: number }).status;
      if (status === 401) { storeToken(null); setUser(null); }
    }
  }, []);

  useEffect(() => onSessionCleared(() => setUser(null)), []);

  useEffect(() => { void refresh().finally(() => setReady(true)); }, [refresh]);

  const accept = useCallback((r: { token: string; user: Me }) => {
    storeToken(r.token);
    setUser(r.user);
  }, []);
  const login = useCallback(async (email: string, password: string) => accept(await api.login(email, password)), [accept]);
  const signup = useCallback(async (input: SignupInput) => accept(await api.signup(input)), [accept]);
  const demoLogin = useCallback(async (buyerId: string) => accept(await api.demoLogin(buyerId)), [accept]);
  const logout = useCallback(async () => {
    try { await api.logout(); } catch { /* 서버에 닿지 않아도 이 기기에서는 로그아웃한다 */ }
    storeToken(null);
    setUser(null);
  }, []);

  const value = useMemo(() => ({ user, ready, login, signup, logout, demoLogin, refresh }), [user, ready, login, signup, logout, demoLogin, refresh]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
