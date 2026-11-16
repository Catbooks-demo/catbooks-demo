import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "@/lib/nav";
import { Empty, ErrorNote, PageTitle, RequireLogin } from "@/components/shop/bits";
import { IdeaBox } from "@/components/shop/IdeaBox";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { codePoints, dateStr, won } from "@/lib/format";
import { ORDER_STATUS_KO, STATUS_KO } from "@/lib/labels";
import type { Inquiry, InquiryImage, Order } from "@/lib/types";

// 서버 한도(shop-api: 본문 2000자, 제목 100자)와 같다. 서버 검증이 최종이고 화면은 먼저 막기만 한다.
const MAX_BODY = 2000;
const MAX_SUBJECT = 100;
// 사진 한도도 서버(inquiries.py)와 같다: 4장, 한 장 base64 6,000,000자, 합계 12,000,000자.
const MAX_IMAGES = 4;
const MAX_IMAGE_BASE64 = 6_000_000;
const MAX_IMAGES_TOTAL = 12_000_000;
const IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
const b64Len = (bytes: number) => 4 * Math.ceil(bytes / 3);

interface Photo { key: number; image: InquiryImage; url: string }

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => {
      const v = String(r.result ?? "");
      resolve(v.slice(v.indexOf(",") + 1)); // data: 머리말은 뗀다
    };
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

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
    case "TOO_MANY_IMAGES": return `사진은 최대 ${MAX_IMAGES}장까지 첨부할 수 있어요.`;
    case "INVALID_IMAGE": return "사진 형식이 올바르지 않아요. 다른 사진으로 다시 첨부해 주세요.";
    case "IMAGE_TOO_LARGE": return "사진 한 장의 크기가 너무 커요. 더 작은 사진으로 첨부해 주세요.";
    case "IMAGES_TOO_LARGE": return "사진 전체 크기가 너무 커요. 사진 수를 줄이거나 더 작은 사진으로 첨부해 주세요.";
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
  const buyer = user?.buyer_id;
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
  const [photos, setPhotos] = useState<Photo[]>([]);
  const photoKey = useRef(0);
  const inflight = useRef(false);
  const photosRef = useRef<Photo[]>([]);
  photosRef.current = photos;
  useEffect(() => () => photosRef.current.forEach((p) => URL.revokeObjectURL(p.url)), []);

  async function addPhotos(list: FileList | null) {
    if (!list || list.length === 0) return;
    setFormError(null);
    const files = Array.from(list);
    let used = photos.reduce((n, p) => n + p.image.data_base64.length, 0);
    let count = photos.length;
    const added: Photo[] = [];
    let problem: string | null = null;
    for (const f of files) {
      if (count >= MAX_IMAGES) { problem = `사진은 최대 ${MAX_IMAGES}장까지 첨부할 수 있어요.`; break; }
      if (!(IMAGE_TYPES as readonly string[]).includes(f.type)) { problem = "사진은 JPG, PNG, WEBP 형식만 첨부할 수 있어요."; continue; }
      const est = b64Len(f.size);
      if (est > MAX_IMAGE_BASE64) { problem = "사진 한 장의 크기가 너무 커요. 약 4.5MB 이하로 첨부해 주세요."; continue; }
      if (used + est > MAX_IMAGES_TOTAL) { problem = "사진 전체 크기가 너무 커요. 사진 수를 줄여 주세요."; continue; }
      try {
        const data = await readBase64(f);
        used += data.length;
        count += 1;
        added.push({ key: ++photoKey.current, url: URL.createObjectURL(f), image: { ref: f.name.trim().slice(0, 200) || "photo", media_type: f.type as InquiryImage["media_type"], data_base64: data } });
      } catch { problem = "사진을 읽지 못했어요. 다른 사진으로 다시 시도해 주세요."; }
    }
    if (added.length) setPhotos((cur) => [...cur, ...added]);
    if (problem) setFormError(problem);
  }

  function removePhoto(key: number) {
    setPhotos((cur) => {
      cur.filter((p) => p.key === key).forEach((p) => URL.revokeObjectURL(p.url));
      return cur.filter((p) => p.key !== key);
    });
  }

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
  const subjectLen = codePoints(subject.trim());
  const bodyLen = codePoints(body.trim());
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
        ...(photos.length ? { images: photos.map((p) => p.image) } : {}),
      });
      setReceipt(r);
      setOrderId("");
      setSubject("");
      setBody("");
      photos.forEach((p) => URL.revokeObjectURL(p.url));
      setPhotos([]);
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
            문의할 주문이 없습니다. 1:1 문의는 주문을 선택해서 보내므로, <Link href="/my/orders" className="font-semibold text-brand-600 underline">주문·배송</Link>에서 주문을 확인해 주세요.
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
                <p><Link href={`/my/orders/${encodeURIComponent(picked.order_id)}`} className="font-mono font-semibold underline">{picked.order_id}</Link> · {ORDER_STATUS_KO[picked.status] ?? picked.status} · {dateStr(picked.created_at)} 주문 · 결제 {won(picked.total_amount)}</p>
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
            <div className="text-sm">
              <span className="mb-1 flex justify-between font-medium"><span>사진 첨부 <span className="font-normal text-ink-500">(선택)</span></span><span className="text-xs tabular-nums text-ink-500">{photos.length}/{MAX_IMAGES}</span></span>
              <input type="file" accept={IMAGE_TYPES.join(",")} multiple className="field" disabled={sending || photos.length >= MAX_IMAGES} onChange={(e) => { void addPhotos(e.target.files); e.target.value = ""; }} />
              <p className="mt-1 text-xs text-ink-500">영수증 등 사진을 최대 {MAX_IMAGES}장까지 첨부할 수 있어요. JPG·PNG·WEBP, 한 장에 약 4.5MB 이하예요.</p>
              {photos.length > 0 && (
                <ul className="mt-2 flex flex-wrap gap-2">
                  {photos.map((p) => (
                    <li key={p.key} className="relative">
                      <img src={p.url} alt={p.image.ref} className="h-20 w-20 rounded-lg border border-ink-100 object-cover" />
                      <button type="button" className="absolute -right-1.5 -top-1.5 h-6 w-6 rounded-full bg-ink-700 text-xs text-white" aria-label={`${p.image.ref} 사진 빼기`} onClick={() => removePhoto(p.key)} disabled={sending}>×</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <p className="text-xs text-ink-500">주민등록번호·카드번호·계좌번호 등 민감한 개인정보는 입력하지 마세요.</p>
            <button type="submit" className="btn-primary" disabled={!canSend}>{sending ? "접수 중…" : "문의 보내기"}</button>
          </form>
        )}
        <p className="mt-4 text-sm text-ink-500">보낸 문의는 <Link href="/my/inquiries" className="font-semibold text-brand-600 underline">마이페이지 → 1:1 문의 내역</Link>에서 확인해요.</p>
        {buyer && <IdeaBox key={buyer} buyerId={buyer} />}
        <p className="mt-4 text-sm text-ink-500">주문과 관련한 신청은 <Link href="/my/orders" className="font-semibold text-brand-600 underline">주문·배송</Link>에서 하세요.</p>
      </RequireLogin>
    </div>
  );
}
