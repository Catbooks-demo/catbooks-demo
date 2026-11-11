import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "@/lib/nav";
import { api, ApiError } from "@/lib/api";
import { won } from "@/lib/format";
import { PAYMENT_KO } from "@/lib/labels";
import { failureGuide, lastFailedPayment, supportHref } from "@/lib/payment";
import { orderIdOfToss } from "@/lib/toss";
import type { Order } from "@/lib/types";

type State =
  | { kind: "confirming" }
  | { kind: "paid"; order: Order }
  | { kind: "failed"; orderId: string | null; code: string | null };

/**
 * 토스 결제위젯 성공 리다이렉트(`?paymentKey&orderId&amount`). 여기서 shop-api에 승인을 요청한다.
 * 새로고침해도 서버가 처음 결과를 그대로 돌려주므로 두 번 승인되지 않는다.
 */
export default function PaymentSuccessPage() {
  const q = useSearchParams();
  const paymentKey = q.get("paymentKey");
  const tossOrderId = q.get("orderId");
  const amountRaw = q.get("amount");
  const amount = amountRaw ? Number(amountRaw) : NaN;
  const [state, setState] = useState<State>({ kind: "confirming" });
  const started = useRef(false); // StrictMode의 두 번 실행에도 승인 요청은 한 번

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const orderId = orderIdOfToss(tossOrderId);
    if (!paymentKey || !tossOrderId || !Number.isFinite(amount)) {
      setState({ kind: "failed", orderId, code: null });
      return;
    }
    api.tossConfirm({ toss_order_id: tossOrderId, payment_key: paymentKey, amount })
      .then((order) => setState(order.status === "paid"
        ? { kind: "paid", order }
        : { kind: "failed", orderId: order.order_id, code: lastFailedPayment(order)?.failure_code ?? null }))
      .catch((e) => setState({ kind: "failed", orderId, code: e instanceof ApiError ? e.code ?? null : null }));
  }, [paymentKey, tossOrderId, amount]);

  if (state.kind === "confirming") {
    return (
      <div className="mx-auto max-w-md py-16 text-center" role="status">
        <p className="text-lg font-bold">결제를 확인하고 있어요…</p>
        <p className="mt-2 text-sm text-ink-500">창을 닫지 말고 잠시만 기다려 주세요.</p>
      </div>
    );
  }

  if (state.kind === "failed") {
    const guide = failureGuide(state.code);
    return (
      <div className="mx-auto max-w-md py-10">
        <section role="alert" className="rounded-xl border border-red-100 bg-red-50 p-5 text-sm text-red-800">
          <p className="text-base font-bold">{guide.title}</p>
          <p className="mt-1">{guide.hint}</p>
          {state.orderId && <p className="mt-3 font-mono text-xs text-red-700">주문 번호 {state.orderId}</p>}
        </section>
        <div className="mt-5 flex flex-wrap gap-2">
          <Link href={state.orderId ? `/orders/${state.orderId}` : "/orders"} className="btn-primary">다시 결제하기</Link>
          {state.orderId && <Link href={supportHref(state.orderId)} className="btn-ghost">문의하기</Link>}
        </div>
      </div>
    );
  }

  const o = state.order;
  const paid = [...o.payments].reverse().find((p) => p.status === "succeeded");
  return (
    <div className="mx-auto max-w-md py-10">
      <section role="status" className="rounded-xl border border-ink-100 bg-surface p-6">
        <p className="text-3xl" aria-hidden>✓</p>
        <h1 className="mt-2 text-xl font-bold">결제가 완료됐어요</h1>
        <p className="mt-1 text-sm text-ink-500">주문이 접수되어 곧 준비를 시작해요.</p>
        <dl className="mt-5 space-y-2 border-t border-ink-100 pt-4 text-sm">
          <div className="flex justify-between gap-3"><dt className="text-ink-500">주문 번호</dt><dd className="font-mono">{o.order_id}</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-ink-500">결제 금액</dt><dd className="font-bold tabular-nums">{won(paid?.amount ?? o.total_amount)}</dd></div>
          <div className="flex justify-between gap-3"><dt className="text-ink-500">결제 수단</dt><dd>{paid ? PAYMENT_KO[paid.method_code] ?? paid.method_code : "-"}</dd></div>
        </dl>
      </section>
      <div className="mt-5 flex flex-wrap gap-2">
        <Link href={`/orders/${o.order_id}`} className="btn-primary">주문 상세 보기</Link>
        <Link href="/products" className="btn-ghost">쇼핑 계속하기</Link>
      </div>
    </div>
  );
}
