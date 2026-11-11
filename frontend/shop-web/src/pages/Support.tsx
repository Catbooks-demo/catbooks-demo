import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "@/lib/nav";
import { Empty, ErrorNote, NotReady, PageTitle, RequireLogin } from "@/components/shop/bits";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateStr, won } from "@/lib/format";
import { ORDER_STATUS_KO, STATUS_KO } from "@/lib/labels";
import type { Inquiry, Order } from "@/lib/types";

// 서버 한도(shop-api: 본문 2000자, 제목 100자)와 같다. 서버 검증이 최종이고 화면은 먼저 막기만 한다.
const MAX_BODY = 2000;
const MAX_SUBJECT = 100;

// 서버는 공백을 다듬은 뒤 코드포인트로 센다. 이모지 등이 UTF-16 길이와 어긋나지 않게 맞춘다.
const count = (s: string) => [...s].length;

/** 오류 코드별 안내. 원인을 짐작해 적지 않고, 코드가 알려 주는 사실만 말한다. */
function inquiryErrorText(e: unknown): string {
  const generic = "문의를 접수하지 못했습니다. 잠시 후 다시 시도해 주세요.";
  if (!(e instanceof ApiError)) return generic;
  switch (e.code) {
    case "RATE_LIMITED": return "문의를 짧은 시간에 너무 많이 보냈습니다. 잠시 후 다시 보내 주세요.";
    case "ORDER_NOT_FOUND": return "선택한 주문을 찾을 수 없습니다. 주문 목록에서 주문을 다시 확인해 주세요.";
    case "EMPTY_BODY": return "문의 내용을 입력해 주세요.";
    case "BODY_TOO_LONG": return `문의 내용은 ${MAX_BODY.toLocaleString("ko-KR")}자 이하로 입력해 주세요.`;
    case "SUBJECT_TOO_LONG": return `제목은 ${MAX_SUBJECT}자 이하로 입력해 주세요.`;
    case "INVALID_BUYER_ID": return "구매자 정보가 올바르지 않습니다. 구매자를 다시 선택해 주세요.";
    case "INVALID_ORDER_ID": return "주문 번호가 올바르지 않습니다. 주문을 다시 선택해 주세요.";
    default:
      if (e.status === 0) return "판매 서버에 연결할 수 없어 문의를 접수하지 못했습니다. 잠시 후 다시 시도해 주세요.";
      if (e.status === 502 || e.status === 504 || e.status === 403) return "고객지원 쪽에서 문의를 받지 못했습니다. 잠시 후 다시 시도해 주세요.";
      return generic;
  }
}

const orderLabel = (o: Order) => `${o.order_id} · ${ORDER_STATUS_KO[o.status] ?? o.status} · ${won(o.total_amount)}`;

export default function SupportPage() {
  const { user, ready } = useAuth();
  const buyer = user?.id;
  // 결제 실패 화면 등에서 넘어온 주문 번호. 내 주문 목록에 있을 때만 미리 선택한다.
  const wanted = useSearchParams().get("order_id");

  const [orders, setOrders] = useState<Order[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [orderId, setOrderId] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [sending, setSending] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Inquiry | null>(null);
  const inflight = useRef(false);

  useEffect(() => {
    if (!buyer) return;
    let live = true;
    setOrders(null);
    setLoadError(null);
    api.orders(buyer)
      .then((o) => {
        if (!live) return;
        setOrders(o);
        setOrderId((cur) => cur || (wanted && o.some((x) => x.order_id === wanted) ? wanted : ""));
      })
      .catch((e: Error) => { if (live) setLoadError(e.message); });
    return () => { live = false; };
  }, [buyer, wanted]);

  const picked = orders?.find((o) => o.order_id === orderId) ?? null;
  const subjectLen = count(subject.trim());
  const bodyLen = count(body.trim());
  const subjectOver = subjectLen > MAX_SUBJECT;
  const bodyOver = bodyLen > MAX_BODY;
  const canSend = !!picked && bodyLen > 0 && !bodyOver && !subjectOver && !sending;

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    if (inflight.current || !buyer || !picked) return;
    if (bodyLen === 0) { setFormError("문의 내용을 입력해 주세요."); return; }
    if (bodyOver || subjectOver) return;
    inflight.current = true;
    setSending(true);
    setFormError(null);
    setReceipt(null);
    try {
      const trimmedSubject = subject.trim();
      const r = await api.submitInquiry({
        buyer_id: buyer,
        order_id: picked.order_id,
        ...(trimmedSubject ? { subject: trimmedSubject } : {}),
        body: body.trim(),
      });
      setReceipt(r);
      setOrderId("");
      setSubject("");
      setBody("");
    } catch (e) {
      // 문의 원문은 콘솔·저장소에 남기지 않는다. 오류 코드로 만든 안내만 보여 준다.
      setFormError(inquiryErrorText(e));
    } finally {
      inflight.current = false;
      setSending(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl">
      <PageTitle title="고객센터" sub="취소·반품·교환은 주문 상세에서 바로 신청할 수 있습니다." />
      <RequireLogin user={user} ready={ready}>
        <ErrorNote error={loadError} />
        {!loadError && orders === null && <p className="py-6 text-center text-sm text-ink-500">주문을 불러오는 중…</p>}
        {orders && orders.length === 0 && (
          <Empty>
            문의할 주문이 없습니다. 1:1 문의는 주문을 선택해서 보내므로, <Link href="/orders" className="font-semibold text-brand-600 underline">주문·배송</Link>에서 주문을 확인해 주세요.
          </Empty>
        )}
        {orders && orders.length > 0 && (
          <form onSubmit={submit} className="space-y-4 rounded-xl border border-ink-100 bg-surface p-4" aria-busy={sending}>
            <h2 className="font-semibold">1:1 문의</h2>
            {receipt && (
              <div role="status" className="rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800">
                <p className="font-semibold">문의가 접수되었습니다.</p>
                <p className="mt-1">문의 번호 <span className="font-mono font-semibold">{String(receipt.id)}</span> · 상태 {STATUS_KO[receipt.status] ?? receipt.status} · 주문 <span className="font-mono">{receipt.order_id}</span></p>
              </div>
            )}
            <ErrorNote error={formError} />
            <label className="block text-sm">
              <span className="mb-1 block font-medium">문의할 주문 <span className="text-red-700">(필수)</span></span>
              <select className="field" value={orderId} required onChange={(e) => { setOrderId(e.target.value); setFormError(null); }} disabled={sending}>
                <option value="">주문을 선택해 주세요</option>
                {orders.map((o) => <option key={o.order_id} value={o.order_id}>{orderLabel(o)}</option>)}
              </select>
            </label>
            {picked && (
              <div className="rounded-lg bg-ink-100 px-3 py-2 text-xs text-ink-700">
                <p><Link href={`/orders/${encodeURIComponent(picked.order_id)}`} className="font-mono font-semibold underline">{picked.order_id}</Link> · {ORDER_STATUS_KO[picked.status] ?? picked.status} · {dateStr(picked.created_at)} 주문 · 결제 {won(picked.total_amount)}</p>
                <p className="mt-1 truncate">{picked.items.map((i) => `${i.title} × ${i.qty}`).join(", ")}</p>
              </div>
            )}
            <label className="block text-sm">
              <span className="mb-1 flex justify-between font-medium"><span>제목 <span className="font-normal text-ink-500">(선택)</span></span><span className={`text-xs tabular-nums ${subjectOver ? "text-red-700" : "text-ink-500"}`}>{subjectLen}/{MAX_SUBJECT}</span></span>
              <input className="field" value={subject} onChange={(e) => setSubject(e.target.value)} disabled={sending} autoComplete="off" />
              {subjectOver && <span className="mt-1 block text-xs text-red-700">제목은 {MAX_SUBJECT}자 이하로 입력해 주세요.</span>}
            </label>
            <label className="block text-sm">
              <span className="mb-1 flex justify-between font-medium"><span>문의 내용 <span className="text-red-700">(필수)</span></span><span className={`text-xs tabular-nums ${bodyOver ? "text-red-700" : "text-ink-500"}`}>{bodyLen.toLocaleString("ko-KR")}/{MAX_BODY.toLocaleString("ko-KR")}</span></span>
              <textarea className="field h-36 resize-y" value={body} onChange={(e) => setBody(e.target.value)} disabled={sending} />
              {bodyOver && <span className="mt-1 block text-xs text-red-700">문의 내용은 {MAX_BODY.toLocaleString("ko-KR")}자 이하로 입력해 주세요.</span>}
            </label>
            <p className="text-xs text-ink-500">주민등록번호·카드번호·계좌번호 등 민감한 개인정보는 입력하지 마세요.</p>
            <button type="submit" className="btn-primary" disabled={!canSend}>{sending ? "접수 중…" : "문의 보내기"}</button>
          </form>
        )}
        <div className="mt-4"><NotReady feature="문의 내역 조회" /></div>
        <p className="mt-4 text-sm text-ink-500">주문과 관련한 신청은 <Link href="/orders" className="font-semibold text-brand-600 underline">주문·배송</Link>에서 하세요.</p>
      </RequireLogin>
    </div>
  );
}
