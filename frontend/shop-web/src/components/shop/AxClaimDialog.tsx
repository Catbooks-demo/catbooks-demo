import { useState } from "react";
import { ErrorNote, Modal } from "./bits";
import { won } from "@/lib/format";
import { CLAIM_REASON_KO, CLAIM_TYPE_KO } from "@/lib/labels";
import { AxClaimStatusBadge, AxClaimSummary } from "./AxClaimSummary";
import { api } from "@/lib/api";
import type { Claim, ClaimReason, ClaimType, Order } from "@/lib/types";

const REASONS: Record<ClaimType, ClaimReason[]> = {
  cancel: ["change_of_mind", "other"],
  return: ["change_of_mind", "defect", "wrong_item", "damaged", "missing_item", "other"],
  exchange: ["defect", "wrong_item", "damaged", "change_of_mind", "other"],
};

/** 배송 완료 전에는 취소, 완료 후에는 반품·교환. 결제된 주문만. */
export function axClaimTypes(o: Order): ClaimType[] {
  if (o.status !== "paid") return [];
  return o.delivered_at ? ["return", "exchange"] : ["cancel"];
}

export function AxClaimDialog({ order, onClose, onDone }: { order: Order; onClose: () => void; onDone: (c: Claim) => void }) {
  const [types] = useState(() => axClaimTypes(order));
  const [type, setType] = useState<ClaimType>(types[0]);
  const [reason, setReason] = useState<ClaimReason>(REASONS[types[0]][0]);
  const [on, setOn] = useState<Record<string, boolean>>(() => Object.fromEntries(order.items.map((i) => [i.order_item_id, true])));
  const [qty, setQty] = useState<Record<string, number>>(() => Object.fromEntries(order.items.map((i) => [i.order_item_id, i.qty])));
  const [unsealed, setUnsealed] = useState(false);
  const [photos, setPhotos] = useState(0);
  const [text, setText] = useState("");
  const [result, setResult] = useState<Claim | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const picked = order.items.filter((i) => on[i.order_item_id]);
  const sellerFault = ["defect", "wrong_item", "damaged"].includes(reason);

  function pickType(t: ClaimType) {
    setType(t);
    if (!REASONS[t].includes(reason)) setReason(REASONS[t][0]);
  }

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const data = await api.submitClaim({
        buyer_id: order.buyer_id, customer_name: order.buyer_id, order_id: order.order_id, type, reason,
        items: type === "cancel" ? undefined : picked.map((i) => ({ order_item_id: i.order_item_id, qty: Math.max(1, Math.min(i.qty, qty[i.order_item_id] ?? 1)) })),
        unsealed, evidence_count: photos, description: text || undefined,
      });
      setResult(data);
      onDone(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!types.length) return null;
  return (
    <Modal title="취소·반품·교환 신청" onClose={onClose} wide>
      {result ? (
        <div className="space-y-3">
          <p className="flex items-center gap-2 text-sm font-semibold">신청이 접수되었습니다 <span className="font-normal text-ink-500">(#{result.id})</span><AxClaimStatusBadge status={result.status} /></p>
          <AxClaimSummary c={result} />
          <button className="btn-primary" onClick={onClose}>확인</button>
        </div>
      ) : (
        <div className="space-y-4 text-sm">
          <fieldset>
            <legend className="mb-1.5 text-xs font-semibold text-ink-500">신청 종류</legend>
            <div className="flex flex-wrap gap-2">
              {types.map((t) => (
                <button key={t} aria-pressed={type === t} onClick={() => pickType(t)}
                  className={`chip py-1.5 text-sm ${type === t ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>{CLAIM_TYPE_KO[t]}</button>
              ))}
            </div>
          </fieldset>
          <label className="block">
            <span className="mb-1.5 block text-xs font-semibold text-ink-500">사유</span>
            <select className="field" value={reason} onChange={(e) => setReason(e.target.value as ClaimReason)}>
              {REASONS[type].map((r) => <option key={r} value={r}>{CLAIM_REASON_KO[r]}</option>)}
            </select>
          </label>
          {type !== "cancel" && (
            <fieldset>
              <legend className="mb-1.5 text-xs font-semibold text-ink-500">상품과 수량</legend>
              <ul className="space-y-1.5">
                {order.items.map((i) => (
                  <li key={i.order_item_id} className="flex items-center gap-2 rounded-lg border border-ink-100 p-2">
                    <input type="checkbox" checked={!!on[i.order_item_id]} onChange={(e) => setOn((c) => ({ ...c, [i.order_item_id]: e.target.checked }))} aria-label={`${i.title} 선택`} />
                    <span className="min-w-0 flex-1 truncate text-xs">{i.title}</span>
                    <input type="number" min={1} max={i.qty} disabled={!on[i.order_item_id]} value={qty[i.order_item_id] ?? 1}
                      onChange={(e) => setQty((c) => ({ ...c, [i.order_item_id]: Number(e.target.value) }))} className="field w-16 py-1 text-center text-xs" aria-label={`${i.title} 수량`} />
                    <span className="w-12 text-right text-[11px] text-ink-500">/ {i.qty}개</span>
                  </li>
                ))}
              </ul>
            </fieldset>
          )}
          {type !== "cancel" && reason === "change_of_mind" && (
            <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={unsealed} onChange={(e) => setUnsealed(e.target.checked)} />상품을 개봉했거나 사용했습니다</label>
          )}
          {sellerFault && (
            <label className="block">
              <span className="mb-1.5 block text-xs font-semibold text-ink-500">상태 사진 (데모: 파일은 올라가지 않고 개수만 전달)</span>
              <input type="file" accept="image/*" multiple className="text-xs" onChange={(e) => setPhotos(e.target.files?.length ?? 0)} aria-label="상태 사진 첨부" />
              <span className="mt-1 block text-[11px] text-ink-500">첨부 {photos}장 · 불량·파손·오배송은 사진이 있어야 접수됩니다.</span>
            </label>
          )}
          <label className="block">
            <span className="mb-1.5 block text-xs font-semibold text-ink-500">상세 내용 (선택)</span>
            <textarea className="field h-20 resize-none" maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <p className="text-[11px] text-ink-500">환불액은 신청 후 고객지원이 영수증 기준으로 계산합니다(주문 결제 {won(order.total_amount)}). 신청 결과는 화면을 닫거나 새로고침하면 사라집니다.</p>
          <ErrorNote error={error} />
          <div className="flex justify-end gap-2">
            <button className="btn-ghost" onClick={onClose}>닫기</button>
            <button className="btn-primary" disabled={busy || (type !== "cancel" && !picked.length)} onClick={submit}>{busy ? "접수 중…" : "신청하기"}</button>
          </div>
        </div>
      )}
    </Modal>
  );
}
