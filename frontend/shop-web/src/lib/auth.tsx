import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, storeBuyer, storedBuyer } from "./api";

/** 구매자 한 명(가명 ID). `name`은 shop-api가 주는 표시용 데모 이름이고, 아직 못 받았으면 null이다. 적립금은 shop-api에 없다. */
export interface Buyer { id: string; name: string | null }

interface AuthValue {
  user: Buyer | null;
  ready: boolean;
  login: (id: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthValue | null>(null);

/** 데모 구매자 선택. 비밀번호·토큰이 없어 ID만 sessionStorage에 두고 주문 요청 본문에 실어 보낸다. */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Buyer | null>(() => {
    const id = storedBuyer();
    return id ? { id, name: null } : null;
  });

  // 새로고침 뒤에는 저장된 ID로 이름을 다시 받는다. 서버에 없는 구매자면 선택을 푼다.
  useEffect(() => {
    const id = storedBuyer();
    if (!id) return;
    api.demoUser(id)
      .then((u) => setUser({ id: u.buyer_id, name: u.display_name }))
      .catch((e) => {
        if ((e as { status?: number }).status === 404) { storeBuyer(null); setUser(null); }
      });
  }, []);

  /** 서버에 구매자를 확인(`POST /api/shop/sessions`)한 뒤에만 선택으로 인정한다. 실패하면 던진다. */
  const login = useCallback(async (id: string) => {
    const { user: u } = await api.selectUser(id);
    storeBuyer(u.buyer_id);
    setUser({ id: u.buyer_id, name: u.display_name });
  }, []);
  const logout = useCallback(() => {
    storeBuyer(null);
    setUser(null);
  }, []);

  const value = useMemo(() => ({ user, ready: true, login, logout }), [user, login, logout]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
