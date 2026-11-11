import { Link } from "@/lib/nav";
import { useRouter } from "@/lib/nav";
import { useEffect, useRef, useState } from "react";
import { Empty, ErrorNote, PageTitle, PriceSummary, RequireLogin, useCartPrice } from "@/components/shop/bits";
import { TossWidgetBox } from "@/components/shop/TossWidgetBox";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useCart, type CartLine } from "@/lib/cart";
import { won } from "@/lib/format";
import { failHref, isInputError, TossRequestError, usePayPath, useTossWidget } from "@/lib/toss";
import type { Order } from "@/lib/types";

export default function CheckoutPage() {
  const { user, ready } = useAuth();
  const cart = useCart();
  const router = useRouter();
  const [method, setMethod] = useState("card");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inflight = useRef(false); // 연속 클릭으로 주문이 두 번 만들어지지 않게 하는 플래그
  // 결제창으로 넘어가기 직전에 장바구니를 비워도 화면(위젯 포함)이 사라지지 않게, 그 순간의 상품을 붙잡아 둔다
  const [frozen, setFrozen] = useState<CartLine[] | null>(null);
  const lines = frozen ?? cart.lines;
  const { price, error: priceError } = useCartPrice(lines);
  const path = usePayPath();
  const toss = path === "toss";
  // 위젯 금액은 서버 장바구니 가격. 결제 직전에 prepare 응답 금액으로 다시 맞춘다
  const widget = useTossWidget(toss && lines.length > 0, price?.payable_amount ?? null);

  // 주문 생성(pending_payment) → 가짜 결제. 합계·배송비는 서버 응답이 정한다.
  async function submit() {
    if (!user || inflight.current) return;
    inflight.current = true;
    setBusy(true);
    setError(null);
    try {
      const order = await api.placeOrder({ buyer_id: user.id, items: cart.lines.map((l) => ({ product_id: l.id, qty: l.qty })) });
      cart.clear();
      // 결제가 실패해도 주문은 만들어졌다: 주문 상세에서 상태를 보고 다시 결제한다.
      await api.pay(order.order_id, method).catch(() => undefined);
      router.push(`/orders/${order.order_id}?placed=1`);
    } catch (e) {
      setError((e as Error).message);
      inflight.current = false;
      setBusy(false);
    }
  }

  // 토스: 주문 생성 → prepare(서버 금액) → 결제창. 장바구니는 결제창으로 넘어가기 직전에 비운다.
  async function submitToss() {
    if (!user || inflight.current) return;
    const blocked = await widget.check(); // 주문을 만들기 전에 막는다
    if (blocked) { setError(blocked); return; }
    inflight.current = true;
    setBusy(true);
    setError(null);
    let order: Order | null = null;
    try {
      order = await api.placeOrder({ buyer_id: user.id, items: cart.lines.map((l) => ({ product_id: l.id, qty: l.qty })) });
      const prepared = await api.tossPrepare(order.order_id);
      await widget.pay(prepared, () => {
        setFrozen(cart.lines);
        cart.clear();
        try { localStorage.setItem("ax.shop.cart", "[]"); } catch { /* 저장소가 막혀도 결제는 진행 */ } // 저장 효과보다 먼저 페이지가 넘어가도 비워지게
      });
      // 리다이렉트 방식이라 보통 여기까지 오지 않는다
    } catch (e) {
      if (e instanceof TossRequestError && order && isInputError(e.code)) {
        // 결제창이 열리기 전 입력 오류: 주문은 만들어졌으니 주문 상세에서 다시 골라 결제한다
        router.push(`/orders/${order.order_id}?placed=1&payhint=1`);
        return;
      }
      if (e instanceof TossRequestError) { router.push(failHref(e)); return; }
      if (order) {
        // 주문은 만들어졌다: 같은 상품으로 두 번 주문하지 않게 장바구니를 비우고 주문 상세에서 다시 결제한다
        cart.clear();
        router.push(`/orders/${order.order_id}?placed=1`);
        return;
      }
      setError((e as Error).message);
      inflight.current = false;
      setBusy(false);
    }
  }

  // 결제창에서 뒤로 가기로 돌아오면(bfcache) 버튼이 '여는 중'에 멈추지 않게 푼다
  useEffect(() => {
    const onShow = (e: PageTransitionEvent) => { if (e.persisted) { inflight.current = false; setBusy(false); } };
    window.addEventListener("pageshow", onShow);
    return () => window.removeEventListener("pageshow", onShow);
  }, []);

  return (
    <RequireLogin user={user} ready={ready}>
      <PageTitle title="주문·결제" />
      {!lines.length ? <Empty>주문할 상품이 없습니다. <Link href="/products" className="font-semibold text-brand-600 underline">상품 보러 가기</Link></Empty> : (
        <div className="grid gap-8 lg:grid-cols-[1fr_340px]">
          <div className="space-y-6">
            <section className="rounded-xl border border-ink-100 bg-surface p-5">
              <h2 className="mb-3 text-sm font-bold">구매자</h2>
              <p className="text-sm font-medium">{user?.name ?? user?.id}</p>
              {user?.name && <p className="font-mono text-xs text-ink-500">{user.id}</p>}
              {/* TODO(ax-backend#385): 받는 분·주소 입력은 shop-api 주문에 해당 필드가 생기면 추가한다 */}
            </section>
            <section className="rounded-xl border border-ink-100 bg-surface p-5">
              <h2 className="mb-3 text-sm font-bold">주문 상품</h2>
              <ul className="divide-y divide-ink-100 text-sm">
                {lines.map((l) => <li key={l.id} className="flex justify-between gap-3 py-2"><span className="min-w-0 truncate">{l.title} × {l.qty}</span><span className="tabular-nums">{won(l.price * l.qty)}</span></li>)}
              </ul>
            </section>
            {toss ? <TossWidgetBox widget={widget} /> : path === "fake" && (
            <section className="rounded-xl border border-ink-100 bg-surface p-5">
              <h2 className="mb-3 text-sm font-bold">결제 수단 (데모)</h2>
              <div className="flex flex-wrap gap-2">
                {[["card", "카드"], ["transfer", "계좌이체"], ["easy_pay", "간편결제"]].map(([k, l]) => (
                  <button key={k} onClick={() => setMethod(k)} aria-pressed={method === k}
                    className={`chip py-2 text-sm ${method === k ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>{l}</button>
                ))}
              </div>
              {/* TODO(ax-backend#385): 적립금 보유액 조회가 생기면 points_used 입력을 붙인다 */}
              <p className="mt-4 text-xs text-ink-500">적립금 사용은 준비 중입니다.</p>
            </section>
            )}
          </div>
          <aside className="h-fit rounded-xl border border-ink-100 bg-surface p-5 text-sm">
            <PriceSummary price={price} error={priceError} />
            <ErrorNote error={error} />
            {toss ? (
              <button className="btn-primary mt-4 w-full py-3" disabled={busy || !price || widget.status !== "ready"} onClick={submitToss}>
                {busy ? "결제창 여는 중…" : price ? `${won(price.payable_amount)} 결제하기` : "결제하기"}
              </button>
            ) : (
              <>
                <button className="btn-primary mt-4 w-full py-3" disabled={busy || path === "loading"} onClick={submit}>{busy ? "결제 중…" : "주문하고 결제하기"}</button>
                <p className="mt-2 text-[11px] text-ink-500">데모라 실제 결제는 일어나지 않습니다.</p>
              </>
            )}
          </aside>
        </div>
      )}
    </RequireLogin>
  );
}
