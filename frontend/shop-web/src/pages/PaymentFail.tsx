import { Link, useSearchParams } from "@/lib/nav";
import { supportHref } from "@/lib/payment";
import { isUserCancel, orderIdOfToss } from "@/lib/toss";

/**
 * 토스 결제위젯 실패 리다이렉트(`?code&message&orderId`). 승인 전이라 돈은 빠져나가지 않았고
 * 주문은 결제 대기로 남아 있으니 주문 상세에서 다시 결제하게 한다. 토스 문구는 글자로만 그린다.
 */
export default function PaymentFailPage() {
  const q = useSearchParams();
  const code = q.get("code");
  const message = q.get("message");
  const orderId = orderIdOfToss(q.get("orderId"));
  const cancelled = isUserCancel(code);
  const detail = orderId ? `/orders/${orderId}` : "/orders";

  return (
    <div className="mx-auto max-w-md py-10">
      {cancelled ? (
        <section role="status" className="rounded-xl border border-ink-100 bg-surface p-6">
          <h1 className="text-xl font-bold">결제를 취소했어요</h1>
          <p className="mt-2 text-sm text-ink-500">결제창을 닫아서 결제하지 않았어요. 주문은 결제 대기로 남아 있으니 원할 때 다시 결제할 수 있어요.</p>
          {orderId && <p className="mt-3 font-mono text-xs text-ink-500">주문 번호 {orderId}</p>}
        </section>
      ) : (
        <section role="alert" className="rounded-xl border border-red-100 bg-red-50 p-5 text-sm text-red-800">
          <p className="text-base font-bold">결제하지 못했어요</p>
          {message && <p className="mt-1">{message}</p>}
          <p className="mt-2">결제 금액은 빠져나가지 않았어요. 주문은 결제 대기로 남아 있으니 주문 상세에서 다시 결제해 주세요.</p>
          <p className="mt-3 font-mono text-xs text-red-700">{orderId && `주문 번호 ${orderId} · `}{code ?? "UNKNOWN"}</p>
        </section>
      )}
      <div className="mt-5 flex flex-wrap gap-2">
        <Link href={detail} className="btn-primary">주문 상세로 가기</Link>
        {!cancelled && orderId && <Link href={supportHref(orderId)} className="btn-ghost">문의하기</Link>}
      </div>
    </div>
  );
}
