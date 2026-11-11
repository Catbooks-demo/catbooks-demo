import { Link } from "@/lib/nav";
import { useEffect, useState, type ReactNode } from "react";
import { hueOf, won } from "@/lib/format";
import { useCategories } from "@/lib/categories";
import { api } from "@/lib/api";
import type { CartLine } from "@/lib/cart";
import type { CartPrice, Product } from "@/lib/types";
import { Cover } from "./Cover";

export function Price({ amount, big = false }: { amount: number; big?: boolean }) {
  return <span className={`font-bold tabular-nums ${big ? "text-2xl" : "text-base"}`}>{won(amount)}</span>;
}

export function ProductCard({ p }: { p: Product }) {
  const soldOut = p.stock <= 0;
  const { label } = useCategories();
  return (
    <Link href={`/products/${p.product_id}`} className="group flex w-full flex-col gap-2">
      <div className="relative rounded-md bg-surface-2 p-4 transition group-hover:bg-ink-100">
        <Cover title={p.name} hue={hueOf(p.product_id)} category={p.category_code} productId={p.product_id} className="mx-auto max-w-[170px]" />
        {soldOut && <span className="absolute left-2 top-2 chip bg-ink-900 text-surface">일시품절</span>}
      </div>
      <div className="min-w-0">
        <h3 className="line-clamp-2 break-keep text-sm font-semibold leading-snug group-hover:underline">{p.name}</h3>
        <p className="mt-0.5 truncate text-xs text-ink-500">{label(p.category_code)}</p>
        <div className="mt-1.5"><Price amount={p.price} /></div>
      </div>
    </Link>
  );
}

export function Modal({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose} role="presentation">
      <div role="dialog" aria-modal="true" aria-label={title}
        className={`max-h-[92vh] w-full overflow-y-auto rounded-2xl bg-surface p-5 shadow-xl ${wide ? "max-w-3xl" : "max-w-lg"}`} onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="btn-quiet px-2 py-1" onClick={onClose} aria-label="닫기">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function PageTitle({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end gap-3">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
        {sub && <p className="mt-1 text-sm text-ink-500">{sub}</p>}
      </div>
      {right && <div className="ml-auto">{right}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-ink-300 px-4 py-12 text-center text-sm text-ink-500">{children}</div>;
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return <p role="alert" className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm font-medium text-red-800">{error}</p>;
}

export function RequireLogin({ children, user, ready }: { children: ReactNode; user: unknown; ready: boolean }) {
  if (!ready) return <div className="py-20 text-center text-sm text-ink-500">불러오는 중…</div>;
  if (!user) {
    return (
      <Empty>
        구매자를 먼저 선택해 주세요. <Link href="/login" className="font-semibold text-brand-600 underline">구매자 선택</Link>
      </Empty>
    );
  }
  return <>{children}</>;
}

/** shop-api에 아직 없는 기능을 오류 대신 안내로 막는다. TODO(ax-backend#385)로 연결할 자리. */
export function NotReady({ feature, children }: { feature: string; children?: ReactNode }) {
  return (
    <div role="note" className="rounded-xl border border-dashed border-ink-300 bg-surface-2 px-4 py-6 text-center text-sm text-ink-500">
      <p className="font-semibold text-ink-700">{feature} — 준비 중입니다</p>
      <p className="mt-1 text-xs">판매 서버(shop-api)에 아직 없는 기능이라 열리면 이 자리에 연결됩니다.</p>
      {children}
    </div>
  );
}

/** 장바구니 금액 미리보기(`POST /api/shop/cart/price`). 담긴 품목·수량이 바뀌면 서버에 다시 묻는다. */
export function useCartPrice(lines: CartLine[]) {
  const [price, setPrice] = useState<CartPrice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = lines.map((l) => `${l.id}:${l.qty}`).join(",");
  useEffect(() => {
    if (!lines.length) { setPrice(null); setError(null); return; }
    let live = true;
    setError(null);
    api.cartPrice(lines.map((l) => ({ product_id: l.id, qty: l.qty })))
      .then((p) => live && setPrice(p))
      .catch((e: Error) => { if (live) { setPrice(null); setError(e.message); } });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  return { price, error };
}

/** 서버가 계산한 금액을 그대로 보여 준다. 쿠폰 할인은 값이 없으면(null) 0원·빈칸이 아니라 "확인 필요"로 그린다. */
export function PriceSummary({ price, error }: { price: CartPrice | null; error: string | null }) {
  if (error) return <ErrorNote error={error} />;
  if (!price) return <p className="text-xs text-ink-500">금액을 계산하는 중…</p>;
  return (
    <dl className="space-y-2">
      <div className="flex justify-between"><dt className="text-ink-500">상품 금액</dt><dd className="tabular-nums">{won(price.subtotal)}</dd></div>
      <div className="flex justify-between"><dt className="text-ink-500">배송비</dt><dd className="tabular-nums">{price.shipping_fee === 0 ? "무료" : won(price.shipping_fee)}</dd></div>
      <div className="flex justify-between gap-3">
        <dt className="text-ink-500">쿠폰 할인</dt>
        <dd className="text-right">
          {price.coupon_discount === null
            ? <><span className="font-semibold text-amber-700">확인 필요</span>{price.coupon_unknown_reason && <span className="mt-0.5 block text-[11px] text-ink-500">{price.coupon_unknown_reason}</span>}</>
            : <span className="tabular-nums">{won(price.coupon_discount)}</span>}
        </dd>
      </div>
      {price.points_used > 0 && <div className="flex justify-between"><dt className="text-ink-500">적립금 사용</dt><dd className="tabular-nums">{won(price.points_used)}</dd></div>}
      <div className="flex justify-between border-t border-ink-100 pt-2 font-bold"><dt>결제 예정 금액</dt><dd className="tabular-nums">{won(price.payable_amount)}</dd></div>
      <p className="text-[11px] text-ink-500">{won(price.free_shipping_from)} 이상 구매하면 배송비가 무료입니다. 최종 금액은 주문 상세에서 확인할 수 있어요.</p>
    </dl>
  );
}
