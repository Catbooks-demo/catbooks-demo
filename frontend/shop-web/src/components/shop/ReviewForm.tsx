import { useState } from "react";
import { api, ApiError } from "@/lib/api";

export interface ReviewTarget { order_id: string; label?: string }

/** 별점 1~5와 후기(10~1000자). 주문이 여럿이면 주문을 고른다. */
export function ReviewForm({ productId, targets, onDone }: { productId: string; targets: ReviewTarget[]; onDone: () => void }) {
  const [orderId, setOrderId] = useState(targets[0]?.order_id ?? "");
  const [rating, setRating] = useState(5);
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const len = body.trim().length;
  const valid = len >= 10 && len <= 1000 && !!orderId;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!valid || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.writeReview(productId, { order_id: orderId, rating, body: body.trim() });
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setError("이미 후기를 썼어요.");
      else if (err instanceof ApiError && err.status === 403) setError("배송이 끝난 주문만 후기를 쓸 수 있어요.");
      else if (err instanceof ApiError && err.status === 422) setError("별점과 후기 내용(10~1000자)을 확인해 주세요.");
      else setError("후기를 저장하지 못했어요. 잠시 후 다시 시도해 주세요.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-xl border border-ink-100 bg-surface p-4">
      {targets.length > 1 && (
        <label className="block text-sm">
          <span className="mb-1 block font-medium">어느 주문의 후기인가요?</span>
          <select className="field" value={orderId} onChange={(e) => setOrderId(e.target.value)}>
            {targets.map((t) => <option key={t.order_id} value={t.order_id}>{t.label ?? `주문 ${t.order_id}`}</option>)}
          </select>
        </label>
      )}
      <fieldset>
        <legend className="mb-1 text-sm font-medium">별점</legend>
        <div className="flex gap-1" role="radiogroup" aria-label="별점">
          {[1, 2, 3, 4, 5].map((n) => (
            <button key={n} type="button" role="radio" aria-checked={rating === n} aria-label={`${n}점`} onClick={() => setRating(n)}
              className={`text-2xl leading-none ${n <= rating ? "text-amber-500" : "text-ink-300"}`}>★</button>
          ))}
        </div>
      </fieldset>
      <label className="block text-sm">
        <span className="mb-1 block font-medium">후기</span>
        <textarea className="field min-h-[96px]" value={body} maxLength={1000} onChange={(e) => setBody(e.target.value)} placeholder="읽은 느낌을 10자 이상 적어 주세요." />
        <span className={`mt-1 block text-xs ${len > 0 && len < 10 ? "text-accent" : "text-ink-500"}`}>{len} / 1000자 (10자 이상)</span>
      </label>
      {error && <p role="alert" className="text-sm text-accent">{error}</p>}
      <button type="submit" className="btn-primary px-6 py-2 text-sm" disabled={!valid || busy}>{busy ? "저장하는 중…" : "후기 등록"}</button>
    </form>
  );
}
