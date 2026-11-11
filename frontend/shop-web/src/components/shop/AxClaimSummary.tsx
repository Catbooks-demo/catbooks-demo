import { won } from "@/lib/format";
import type { Claim, ClaimStatus } from "@/lib/types";

// 고객지원 상태 → 화면 문구. 고객지원 모델에는 철회가 없다.
const STATUS: Record<ClaimStatus, { label: string; tone: string }> = {
  needs_info: { label: "정보 필요", tone: "bg-amber-50 text-amber-700" },
  needs_review: { label: "사람 확인 중", tone: "bg-violet-50 text-violet-700" },
  awaiting_return: { label: "반품 대기", tone: "bg-brand-50 text-brand-700" },
  return_received: { label: "입고 완료·처리 대기", tone: "bg-brand-50 text-brand-700" },
  approved: { label: "승인", tone: "bg-emerald-50 text-emerald-700" },
  completed: { label: "처리 완료", tone: "bg-emerald-50 text-emerald-700" },
  rejected: { label: "거절", tone: "bg-red-50 text-red-700" },
};
const LEVEL: Record<string, string> = { block: "text-red-800", review: "text-violet-700", warn: "text-amber-800", info: "text-ink-700" };

export function AxClaimStatusBadge({ status }: { status: ClaimStatus }) {
  const s = STATUS[status] ?? { label: status, tone: "bg-ink-100 text-ink-700" };
  return <span className={`chip ${s.tone}`}>{s.label}</span>;
}

/** 고객지원이 돌려준 판정: 예상 환불액, 품목, 규칙 사유. 문장은 고객지원이 만든 그대로 보여 준다. */
export function AxClaimSummary({ c }: { c: Claim }) {
  return (
    <div className="rounded-xl border border-ink-100 bg-surface-2 p-4 text-sm">
      {c.resolution && <p className="font-semibold">{c.resolution}</p>}
      <dl className="mt-3 grid grid-cols-3 gap-2 text-center">
        <div className="rounded-lg bg-surface p-2"><dt className="text-[11px] text-ink-500">환불 예정</dt><dd className="mt-0.5 font-bold tabular-nums">{won(c.expected_refund)}</dd></div>
        <div className="rounded-lg bg-surface p-2"><dt className="text-[11px] text-ink-500">적립금 반환</dt><dd className="mt-0.5 font-bold tabular-nums">{won(c.points_back)}</dd></div>
        <div className="rounded-lg bg-surface p-2"><dt className="text-[11px] text-ink-500">환불 완료</dt><dd className="mt-0.5 font-bold tabular-nums">{won(c.refund_paid)}</dd></div>
      </dl>
      {c.lines.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-ink-700">
          {c.lines.map((l) => (
            <li key={l.order_item_id} className="flex justify-between gap-2">
              <span className="min-w-0 truncate">{l.title} × {l.qty}{l.qty !== l.requested_qty && <span className="ml-1 text-amber-700">(신청 {l.requested_qty} → {l.qty}로 보정)</span>}</span>
              <span className="tabular-nums text-ink-500">{won(l.refund_cash)}</span>
            </li>
          ))}
        </ul>
      )}
      {c.reasons.length > 0 && (
        <ul className="mt-3 space-y-1 border-t border-ink-100 pt-3 text-xs leading-relaxed">
          {c.reasons.map((r, i) => <li key={i} className={LEVEL[r.level]}>· {r.message}{r.rule && <span className="ml-1 font-mono text-[10px] text-ink-500">[{r.rule}]</span>}</li>)}
        </ul>
      )}
      {c.requires_return && c.status === "awaiting_return" && <p className="mt-3 rounded-lg bg-brand-50 px-3 py-2 text-xs text-brand-700">상품을 반품한 뒤 물류센터에서 입고가 확인되면 환불됩니다.</p>}
      <p className="mt-2 font-mono text-[10px] text-ink-500">사건 {c.context_id}</p>
    </div>
  );
}
