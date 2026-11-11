import { Link } from "@/lib/nav";
import { useParams, useRouter, useSearchParams } from "@/lib/nav";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { AxClaimDialog, axClaimTypes } from "@/components/shop/AxClaimDialog";
import { ClaimsLoadError, MyClaimCard, useMyClaims } from "@/components/shop/MyClaimCard";
import { ErrorNote, NotReady, RequireLogin } from "@/components/shop/bits";
import { Cover } from "@/components/shop/Cover";
import { TossWidgetBox } from "@/components/shop/TossWidgetBox";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateStr, hueOf, shortDateTime, won } from "@/lib/format";
import { ORDER_STATUS_KO, ORDER_STATUS_TONE, PAYMENT_KO } from "@/lib/labels";
import { failureGuide, lastFailedPayment, supportHref } from "@/lib/payment";
import { failHref, isInputError, TossRequestError, usePayPath, useTossWidget } from "@/lib/toss";
import type { Order } from "@/lib/types";

export default function OrderPage() {
  return <Suspense><Detail /></Suspense>;
}

function Detail() {
  const { id } = useParams<{ id: string }>();
  const search = useSearchParams();
  const placed = search.get("placed") === "1";
  const payHint = search.get("payhint") === "1"; // 결제 화면에서 위젯 입력이 모자라 넘어온 경우(문구는 고정)
  const { user, ready } = useAuth();
  const [o, setO] = useState<Order | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [claimOpen, setClaimOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [method, setMethod] = useState("card");
  const inflight = useRef(false); // 결제 요청 중 플래그: state 반영 전의 연속 클릭도 막는다
  const router = useRouter();
  const path = usePayPath();
  const { claims, failed: claimsFailed, reload: reloadClaims } = useMyClaims(user?.id, id);

  const load = useCallback(async () => {
    try { setO(await api.order(id)); setError(null); } catch (e) { setError((e as Error).message); }
  }, [id]);
  useEffect(() => { if (user) load(); }, [user, load]);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try { await fn(); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  // 결제는 한 번에 하나만 보낸다. 응답이 오면 화면 값을 믿지 않고 서버의 주문을 다시 읽는다.
  async function pay() {
    if (!o || inflight.current) return;
    inflight.current = true;
    setBusy(true);
    setError(null);
    let note: string | null = null;
    try {
      await api.pay(o.order_id, method);
    } catch (e) {
      // 이미 결제됐거나 취소된 주문이다(다른 탭·연속 클릭). 다시 보내지 않고 상태를 안내한다.
      note = e instanceof ApiError && e.code === "ORDER_NOT_PAYABLE" ? "이미 처리된 주문입니다. 최신 상태를 불러왔습니다." : (e as Error).message;
    } finally {
      await load(); // 서버의 주문을 다시 읽는다(load가 오류를 비우므로 안내는 그 뒤에 띄운다)
      if (note) setError(note);
      inflight.current = false;
      setBusy(false);
    }
  }

  // 토스: prepare(서버 금액·새 시도) → 결제창. 결과는 /payments/success|fail에서 승인·안내한다.
  async function payToss() {
    if (!o || inflight.current) return;
    const blocked = await widget.check();
    if (blocked) { setError(blocked); return; }
    inflight.current = true;
    setBusy(true);
    setError(null);
    try {
      await widget.pay(await api.tossPrepare(o.order_id));
    } catch (e) {
      if (e instanceof TossRequestError && !isInputError(e.code)) { router.push(failHref(e)); return; }
      const note = e instanceof TossRequestError ? e.message
        : e instanceof ApiError && e.code === "ORDER_NOT_PAYABLE" ? "이미 처리된 주문입니다. 최신 상태를 불러왔습니다." : (e as Error).message;
      await load();
      setError(note);
      inflight.current = false;
      setBusy(false);
    }
  }

  // 남의 주문은 보여 주지 않는다(shop-api는 주문 조회에서 구매자를 대조하지 않으므로 화면에서도 막는다).
  const mine = !o || !user || o.buyer_id === user.id;
  const payable = o && (o.status === "pending_payment" || o.status === "payment_failed");
  const failed = o && o.status === "payment_failed" ? lastFailedPayment(o) : null;
  const guide = o && o.status === "payment_failed" ? failureGuide(failed?.failure_code) : null;
  const toss = path === "toss";
  const widget = useTossWidget(toss && !!payable && mine, payable ? o.total_amount : null);

  return (
    <RequireLogin user={user} ready={ready}>
      <Link href="/orders" className="text-xs text-ink-500 hover:underline">‹ 주문 목록</Link>
      <ErrorNote error={error} />
      {o && !mine && <ErrorNote error="다른 구매자의 주문입니다." />}
      {o && mine && (
        <div className="mt-3 space-y-6">
          {placed && <p role="status" className="rounded-lg bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800">주문이 접수되었습니다. 결제 상태를 확인해 보세요.</p>}
          {payHint && payable && <p role="alert" className="rounded-lg bg-amber-50 px-3 py-2 text-sm font-medium text-amber-800">아직 결제하지 않았어요. 아래에서 결제 정보를 고른 뒤 다시 결제해 주세요.</p>}
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="font-mono text-lg font-bold">{o.order_id}</h1>
            <span className={`chip ${ORDER_STATUS_TONE[o.status] ?? "bg-ink-100 text-ink-700"}`}>{ORDER_STATUS_KO[o.status] ?? o.status}</span>
            <span className="text-xs text-ink-500">{shortDateTime(o.created_at)} 주문{o.promised_delivery_at && ` · ${dateStr(o.promised_delivery_at)} 도착 예정`}{o.delivered_at && ` · ${dateStr(o.delivered_at)} 도착`}</span>
          </div>

          <section className="rounded-xl border border-ink-100 bg-surface p-4">
            <h2 className="mb-2 text-sm font-bold">주문 상품</h2>
            <ul className="divide-y divide-ink-100">
              {o.items.map((i) => (
                <li key={i.order_item_id} className="flex items-center gap-3 py-2 text-sm">
                  <div className="w-10 shrink-0"><Cover title={i.title} hue={hueOf(i.product_id)} category="book" productId={i.product_id} compact /></div>
                  <Link href={`/products/${i.product_id}`} className="min-w-0 flex-1 truncate hover:underline">{i.title} × {i.qty}</Link>
                  <span className="tabular-nums text-ink-500">{won(i.paid_amount)}</span>
                </li>
              ))}
            </ul>
            <dl className="mt-3 space-y-1 border-t border-ink-100 pt-3 text-sm">
              <div className="flex justify-between"><dt className="text-ink-500">배송비</dt><dd className="tabular-nums">{o.shipping_fee ? won(o.shipping_fee) : "무료"}</dd></div>
              <div className="flex justify-between"><dt className="text-ink-500">적립금 사용</dt><dd className="tabular-nums">−{won(o.points_used)}</dd></div>
              <div className="flex justify-between text-base font-bold"><dt>결제 금액</dt><dd className="tabular-nums">{won(o.total_amount)}</dd></div>
              {o.refunded_amount > 0 && <div className="flex justify-between text-red-700"><dt>환불</dt><dd className="tabular-nums">{won(o.refunded_amount)}</dd></div>}
            </dl>
          </section>

          {o.payments.length > 0 && (
            <section className="rounded-xl border border-ink-100 bg-surface p-4">
              <h2 className="mb-2 text-sm font-bold">결제 내역</h2>
              <ul className="space-y-1 text-sm">
                {o.payments.map((p) => (
                  <li key={p.payment_id} className="flex flex-wrap justify-between gap-2">
                    <span>{PAYMENT_KO[p.method_code] ?? p.method_code} · <span className={p.status === "succeeded" ? "text-emerald-700" : "text-red-700"}>{p.status === "succeeded" ? "성공" : `실패${p.failure_code ? ` (${p.failure_code})` : ""}`}</span></span>
                    <span className="tabular-nums text-ink-500">{won(p.amount)} · {shortDateTime(p.created_at)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {guide && (
            <section role="alert" className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-900">
              <p className="font-bold">{guide.title}</p>
              <p className="mt-1">{guide.hint}</p>
              <p className="mt-3 text-xs text-red-800">
                결제가 계속 안 되면 <Link href={supportHref(o.order_id)} className="font-semibold underline">이 주문으로 문의하기</Link>
              </p>
            </section>
          )}

          {payable && toss && <TossWidgetBox widget={widget} />}
          {payable && path === "fake" && (
            <div className="flex flex-wrap items-center gap-2" aria-label="결제 수단">
              {[["card", "카드"], ["transfer", "계좌이체"], ["easy_pay", "간편결제"]].map(([k, l]) => (
                <button key={k} disabled={busy} onClick={() => setMethod(k)} aria-pressed={method === k}
                  className={`chip py-2 text-sm ${method === k ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>{l}</button>
              ))}
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            {payable && !toss && <button className="btn-primary" disabled={busy || path === "loading"} onClick={pay}>{busy ? "처리 중…" : o.status === "payment_failed" ? "다른 수단으로 다시 결제하기" : "결제하기"}</button>}
            {payable && toss && (
              <button className="btn-primary" disabled={busy || widget.status !== "ready"} onClick={payToss}>
                {busy ? "결제창 여는 중…" : `${won(o.total_amount)} ${o.status === "payment_failed" ? "다시 결제하기" : "결제하기"}`}
              </button>
            )}
            {payable && <button className="btn-ghost" disabled={busy} onClick={() => act(() => api.cancel(o.order_id))}>주문 취소</button>}
            {axClaimTypes(o).length > 0 && <button className="btn-ghost" onClick={() => setClaimOpen(true)}>{o.delivered_at ? "반품·교환 신청" : "취소 신청"}</button>}
          </div>

          <ClaimsLoadError failed={claimsFailed} onRetry={() => { void reloadClaims(); }} />
          {claims.map((c) => <MyClaimCard key={c.id} c={c} />)}
          {/* TODO(ax-backend#385): 신청 철회·사진 추가, 후기 작성 API가 생기면 연결한다 */}
          <NotReady feature="신청 철회, 후기 작성" />
        </div>
      )}
      {o && claimOpen && <AxClaimDialog order={o} onClose={() => setClaimOpen(false)} onDone={() => { void reloadClaims(); load(); }} />}
    </RequireLogin>
  );
}
