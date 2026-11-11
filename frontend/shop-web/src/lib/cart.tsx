import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Product } from "./types";

export interface CartLine {
  id: string; // product_id
  title: string;
  category: string; // category_code
  price: number; // 담을 때의 단가(표시용). 실제 금액은 주문 생성 시 서버가 정한다
  stock: number;
  qty: number;
}

interface CartValue {
  lines: CartLine[];
  count: number;
  add: (p: Product, qty?: number) => void;
  setQty: (id: string, qty: number) => void;
  remove: (id: string) => void;
  clear: () => void;
}

const KEY = "ax.shop.cart"; // 예전 catbooks 장바구니(숫자 ID)와 섞이지 않게 키를 바꿨다
const CartContext = createContext<CartValue | null>(null);

export function CartProvider({ children }: { children: ReactNode }) {
  const [lines, setLines] = useState<CartLine[]>([]);
  const [loaded, setLoaded] = useState(false);

  // Read storage after mount only, so server rendering never touches it.
  useEffect(() => {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw) setLines(JSON.parse(raw));
    } catch {
      /* corrupt or blocked storage: start with an empty cart */
    }
    setLoaded(true);
  }, []);

  useEffect(() => {
    if (!loaded) return;
    try {
      localStorage.setItem(KEY, JSON.stringify(lines));
    } catch {
      /* cart still works for this session */
    }
  }, [lines, loaded]);

  const add = useCallback((p: Product, qty = 1) => {
    setLines((cur) => {
      const have = cur.find((l) => l.id === p.product_id);
      const next = Math.min(20, Math.max(1, (have?.qty ?? 0) + qty));
      if (have) return cur.map((l) => (l.id === p.product_id ? { ...l, qty: next } : l));
      return [...cur, { id: p.product_id, title: p.name, category: p.category_code, price: p.price, stock: p.stock, qty: next }];
    });
  }, []);
  const setQty = useCallback((id: string, qty: number) => setLines((cur) => cur.map((l) => (l.id === id ? { ...l, qty: Math.min(20, Math.max(1, qty)) } : l))), []);
  const remove = useCallback((id: string) => setLines((cur) => cur.filter((l) => l.id !== id)), []);
  const clear = useCallback(() => setLines([]), []);

  const value = useMemo(
    () => ({ lines, count: lines.reduce((n, l) => n + l.qty, 0), add, setQty, remove, clear }),
    [lines, add, setQty, remove, clear],
  );
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartValue {
  const ctx = useContext(CartContext);
  if (!ctx) throw new Error("useCart must be used inside <CartProvider>");
  return ctx;
}
