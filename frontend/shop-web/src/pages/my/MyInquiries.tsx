import { useEffect, useState, type FormEvent } from "react";
import { Empty, ErrorNote, PageTitle } from "@/components/shop/bits";
import { api, ApiError } from "@/lib/api";
import { INQUIRY_STATUS_KO } from "@/lib/labels";
import { shortDateTime } from "@/lib/format";
import { Link } from "@/lib/nav";
import type { MyInquiry, MyInquiryDetail } from "@/lib/types";

const MAX_COMMENT = 300;

function rateErrorText(e: unknown): string {
  const generic = "평가를 남기지 못했어요. 잠시 후 다시 시도해 주세요.";
  if (!(e instanceof ApiError)) return generic;
  if (e.code === "RATE_LIMITED" || e.status === 429) return "평가를 짧은 시간에 너무 많이 보냈어요. 잠시 후 다시 시도해 주세요.";
  if (e.code === "NOT_FOUND" || e.status === 404) return "문의를 찾을 수 없어요.";
  if (e.code === "INVALID_STATE" || e.status === 409) return "이미 평가했거나 아직 평가할 수 없는 문의예요. 화면을 새로 고쳐 확인해 주세요.";
  if (e.status === 0) return "판매 서버에 연결할 수 없어요. 잠시 후 다시 시도해 주세요.";
  return generic;
}

function RateForm({ id, onDone }: { id: number; onDone: (rating: number) => void }) {
  const [rating, setRating] = useState(0);
  const [resolved, setResolved] = useState<boolean | null>(null);
  const [comment, setComment] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    if (sending || rating < 1 || resolved === null) return;
    setSending(true);
    setError(null);
    try {
      const c = comment.trim();
      const r = await api.rateInquiry(id, { rating, resolved, ...(c ? { comment: c } : {}) });
      onDone(r.rating);
    } catch (e) {
      setError(rateErrorText(e));
      setSending(false);
    }
  }

  return (
    <form onSubmit={submit} className="mt-3 space-y-3 rounded-lg bg-ink-100 p-3" aria-busy={sending}>
      <p className="font-semibold">답변은 어땠나요?</p>
      <ErrorNote error={error} />
      <div role="radiogroup" aria-label="만족도" className="flex gap-1">
        {[1, 2, 3, 4, 5].map((n) => (
          <button key={n} type="button" role="radio" aria-checked={rating === n} aria-label={`${n}점`} disabled={sending} onClick={() => setRating(n)} className={`h-9 w-9 rounded-lg border text-sm font-semibold ${rating === n ? "border-brand-600 bg-brand-600 text-white" : "border-ink-100 bg-surface"}`}>{n}</button>
        ))}
      </div>
      <fieldset className="flex gap-4" disabled={sending}>
        <legend className="mb-1 text-xs text-ink-500">문제가 해결됐나요?</legend>
        <label className="flex items-center gap-1"><input type="radio" name={`resolved-${id}`} checked={resolved === true} onChange={() => setResolved(true)} />해결됐어요</label>
        <label className="flex items-center gap-1"><input type="radio" name={`resolved-${id}`} checked={resolved === false} onChange={() => setResolved(false)} />해결되지 않았어요</label>
      </fieldset>
      <label className="block">
        <span className="mb-1 flex justify-between text-xs text-ink-500"><span>한마디 (선택)</span><span className="tabular-nums">{comment.length}/{MAX_COMMENT}</span></span>
        <textarea className="field h-20 resize-y" value={comment} maxLength={MAX_COMMENT} onChange={(e) => setComment(e.target.value)} disabled={sending} />
      </label>
      <button type="submit" className="btn-primary" disabled={sending || rating < 1 || resolved === null}>{sending ? "보내는 중…" : "평가 남기기"}</button>
    </form>
  );
}

function Detail({ id }: { id: number }) {
  const [d, setD] = useState<MyInquiryDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rated, setRated] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    api.myInquiry(id).then((v) => { if (live) setD(v); }).catch(() => { if (live) setError("문의 내용을 불러오지 못했어요. 잠시 후 다시 확인해 주세요."); });
    return () => { live = false; };
  }, [id]);

  if (error) return <div className="mt-3"><ErrorNote error={error} /></div>;
  if (!d) return <p className="mt-3 text-xs text-ink-500">불러오는 중…</p>;
  return (
    <div className="mt-3 border-t border-ink-100 pt-3">
      {d.messages && d.messages.length > 0 ? (
        <ul className="space-y-2">
          {d.messages.map((m, i) => (
            <li key={m.id ?? i} className={`rounded-lg px-3 py-2 ${m.role === "customer" ? "bg-ink-100" : "bg-emerald-50"}`}>
              <p className="text-xs text-ink-500">{m.role === "customer" ? "내가 보낸 문의" : "고객지원 답변"}{m.created_at ? ` · ${shortDateTime(m.created_at)}` : ""}</p>
              <p className="mt-1 whitespace-pre-wrap text-ink-700">{m.text}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-ink-500">{d.notice ?? "아직 보여 드릴 대화가 없어요."}</p>
      )}
      {rated !== null || d.rated ? (
        <p role="status" className="mt-3 rounded-lg bg-emerald-50 px-3 py-2 text-emerald-800">{rated !== null ? `평가해 주셔서 고마워요. (${rated}점)` : "평가를 남긴 문의예요. 고마워요."}</p>
      ) : d.can_rate ? (
        <RateForm id={id} onDone={setRated} />
      ) : null}
    </div>
  );
}

export default function MyInquiries() {
  const [items, setItems] = useState<MyInquiry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<number | null>(null);

  useEffect(() => {
    let live = true;
    api.myInquiries().then((v) => { if (live) setItems(v); }).catch(() => { if (live) setError("문의 내역을 불러오지 못했어요. 잠시 후 다시 확인해 주세요."); });
    return () => { live = false; };
  }, []);

  return (
    <>
      <PageTitle title="1:1 문의 내역" right={<Link href="/support" className="btn-ghost text-sm">문의하기</Link>} />
      <ErrorNote error={error} />
      {items === null && !error && <p className="text-sm text-ink-500">불러오는 중…</p>}
      {items?.length === 0 && <Empty>보낸 문의가 없어요.</Empty>}
      <ul className="space-y-2">
        {items?.map((q) => (
          <li key={q.id} className="rounded-xl border border-ink-100 bg-surface p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`chip ${q.status === "resolved" ? "bg-emerald-50 text-emerald-700" : "bg-amber-50 text-amber-700"}`}>{INQUIRY_STATUS_KO[q.status] ?? "처리 중"}</span>
              <span className="font-semibold">{q.subject}</span>
              <span className="ml-auto text-xs text-ink-500">#{q.id} · {shortDateTime(q.updated_at)}</span>
            </div>
            <p className="mt-1 text-xs text-ink-500">주문 <Link href={`/my/orders/${encodeURIComponent(q.order_id)}`} className="font-mono underline">{q.order_id}</Link></p>
            {(q.notice || q.preview) && <p className="mt-2 text-ink-700">{q.notice ?? q.preview}</p>}
            <div className="mt-2 flex items-center gap-2">
              <button type="button" className="btn-ghost text-xs" aria-expanded={openId === q.id} onClick={() => setOpenId(openId === q.id ? null : q.id)}>{openId === q.id ? "접기" : "자세히 보기"}</button>
              {q.can_rate && openId !== q.id && <span className="text-xs text-brand-600">평가를 남길 수 있어요</span>}
              {q.rated && <span className="text-xs text-ink-500">평가 완료</span>}
            </div>
            {openId === q.id && <Detail id={q.id} />}
          </li>
        ))}
      </ul>
    </>
  );
}
