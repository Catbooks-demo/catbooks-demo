import { useEffect, useState } from "react";
import { Link } from "@/lib/nav";
import { AxClaimDialog, axClaimTypes } from "@/components/shop/AxClaimDialog";
import { AxClaimStatusBadge, AxClaimSummary } from "@/components/shop/AxClaimSummary";
import { Empty, ErrorNote, NotReady, PageTitle, RequireLogin } from "@/components/shop/bits";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateStr, shortDateTime, won } from "@/lib/format";
import { CLAIM_REASON_KO, CLAIM_TYPE_KO, ORDER_STATUS_KO, ORDER_STATUS_TONE } from "@/lib/labels";
import type { Claim, Order } from "@/lib/types";

export default function OrdersPage() {
  const { user, ready } = useAuth();
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [claims, setClaims] = useState<Record<string, Claim[]>>({});
  const [target, setTarget] = useState<Order | null>(null);
  const [error, setError] = useState<string | null>(null);
  const buyer = user?.id;

  useEffect(() => {
    if (!buyer) return;
    let live = true;
    api.orders(buyer)
      .then((o) => { if (live) { setOrders(o); setError(null); } })
      .catch((e: Error) => { if (live) { setOrders(null); setError(e.message); } });
    return () => { live = false; };
  }, [buyer]);

  function done(c: Claim) {
    setClaims((m) => ({ ...m, [c.order_id]: [c, ...(m[c.order_id] ?? [])] }));
    if (buyer) api.orders(buyer).then(setOrders).catch(() => undefined);
  }

  return (
    <RequireLogin user={user} ready={ready}>
      <PageTitle title="주문·배송" sub={`${buyer} 구매자의 주문입니다. 취소·반품·교환 신청은 고객지원 → 안전 → 재무로 이어집니다.`} right={<Link href="/support" className="btn-ghost text-sm">1:1 문의</Link>} />
      <ErrorNote error={error} />
      {orders && orders.length === 0 && <Empty>주문 내역이 없습니다. <Link href="/products" className="font-semibold text-brand-600 underline">쇼핑하러 가기</Link></Empty>}
      <ul className="space-y-4">
        {(orders ?? []).map((o) => (
          <li key={o.order_id} className="rounded-xl border border-ink-100 bg-surface p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/orders/${o.order_id}`} className="font-mono text-sm font-semibold hover:underline">{o.order_id}</Link>
              <span className={`chip ${ORDER_STATUS_TONE[o.status] ?? "bg-ink-100 text-ink-700"}`}>{ORDER_STATUS_KO[o.status] ?? o.status}</span>
              <span className="text-xs text-ink-500">{dateStr(o.created_at)} 주문{o.delivered_at && ` · ${dateStr(o.delivered_at)} 도착`}</span>
              {axClaimTypes(o).length > 0 && <button className="btn-ghost ml-auto py-1 text-xs" onClick={() => setTarget(o)}>{o.delivered_at ? "반품·교환 신청" : "취소 신청"}</button>}
            </div>
            <ul className="mt-3 space-y-1 text-sm">
              {o.items.map((i) => (
                <li key={i.order_item_id} className="flex justify-between gap-2"><span className="min-w-0 truncate">{i.title} × {i.qty}</span><span className="tabular-nums text-ink-500">{won(i.paid_amount)}</span></li>
              ))}
            </ul>
            <p className="mt-2 text-right text-xs text-ink-500">
              배송비 {won(o.shipping_fee)} · 적립금 −{won(o.points_used)} · 결제 <b className="text-ink-900">{won(o.total_amount)}</b>
              {o.refunded_amount > 0 && <span className="ml-1 text-red-700">· 환불 {won(o.refunded_amount)}</span>}
            </p>
            {(claims[o.order_id] ?? []).map((c) => (
              <div key={c.id} className="mt-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <AxClaimStatusBadge status={c.status} />
                  <span className="text-sm font-semibold">{CLAIM_TYPE_KO[c.type]} · {CLAIM_REASON_KO[c.reason]}</span>
                  <span className="text-xs text-ink-500">#{c.id} · {shortDateTime(c.created_at)}</span>
                </div>
                <AxClaimSummary c={c} />
              </div>
            ))}
          </li>
        ))}
      </ul>
      {/* TODO(ax-backend#385): 신청 내역 조회(api.claims)가 생기면 새로고침 후에도 보이게 연결한다 */}
      <div className="mt-6"><NotReady feature="신청 내역 조회·철회·사진 추가">{Object.values(claims).some((l) => l.length > 0) && <p className="mt-2 text-[11px]">방금 낸 신청만 표시됩니다. 새로고침하면 사라집니다.</p>}</NotReady></div>
      {target && <AxClaimDialog order={target} onClose={() => setTarget(null)} onDone={done} />}
    </RequireLogin>
  );
}
