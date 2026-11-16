import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { dateStr } from "@/lib/format";
import type { ReviewPage } from "@/lib/types";

export function Stars({ value, className = "" }: { value: number; className?: string }) {
  const n = Math.max(0, Math.min(5, Math.round(value)));
  return <span role="img" aria-label={`별점 5점 만점에 ${value}점`} className={`text-amber-500 ${className}`}>{"★".repeat(n)}{"☆".repeat(5 - n)}</span>;
}

/** 후기 요약(평균·분포 막대)과 목록·페이지. `reloadKey`가 바뀌면 처음부터 다시 받는다. */
export function ReviewList({ productId, reloadKey, onSummary }: { productId: string; reloadKey: number; onSummary?: (count: number, avg: number) => void }) {
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ReviewPage | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => { setPage(1); }, [productId, reloadKey]);
  useEffect(() => {
    let live = true;
    setError(false);
    api.reviews(productId, page).then((r) => { if (live) { setData(r); onSummary?.(r.summary.count, r.summary.avg); } }).catch(() => { if (live) setError(true); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productId, page, reloadKey]);

  if (error) return <p className="text-sm text-ink-500">후기를 불러오지 못했어요. 잠시 후 다시 확인해 주세요.</p>;
  if (!data) return <p className="text-sm text-ink-500">불러오는 중…</p>;
  const { summary, items } = data;
  if (summary.count === 0) return <p className="rounded-xl bg-surface-2 p-6 text-center text-sm text-ink-500">아직 후기가 없어요. 첫 후기를 남겨 보세요.</p>;
  const pages = Math.max(1, Math.ceil(data.total / data.size));

  return (
    <div>
      <div className="flex flex-wrap items-center gap-6 rounded-xl bg-surface-2 p-4">
        <div className="text-center">
          <p className="text-3xl font-bold tabular-nums">{summary.avg.toFixed(1)}</p>
          <Stars value={summary.avg} />
          <p className="text-xs text-ink-500">후기 {summary.count}개</p>
        </div>
        <ul className="min-w-[200px] flex-1 space-y-1 text-xs" aria-label="별점 분포">
          {[5, 4, 3, 2, 1].map((n) => {
            const c = summary.by_rating[String(n)] ?? 0;
            return (
              <li key={n} className="flex items-center gap-2">
                <span className="w-8 shrink-0">{n}점</span>
                <span className="h-2 flex-1 overflow-hidden rounded-full bg-ink-100"><span className="block h-full bg-amber-500" style={{ width: `${(c / summary.count) * 100}%` }} /></span>
                <span className="w-8 shrink-0 text-right tabular-nums text-ink-500">{c}</span>
              </li>
            );
          })}
        </ul>
      </div>
      <ul className="mt-4 divide-y divide-ink-100">
        {items.map((r) => (
          <li key={r.review_id} className="py-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Stars value={r.rating} />
              <span className="font-medium">{r.author_label}</span>
              <span className="ml-auto text-xs text-ink-500">{dateStr(r.created_at)}</span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-ink-700">{r.body}</p>
          </li>
        ))}
      </ul>
      {pages > 1 && (
        <div className="mt-4 flex items-center justify-center gap-3 text-sm">
          <button className="btn-ghost px-3 py-1" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>이전</button>
          <span className="tabular-nums text-ink-500">{page} / {pages}</span>
          <button className="btn-ghost px-3 py-1" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>다음</button>
        </div>
      )}
    </div>
  );
}
