import { useEffect, useState } from "react";
import { Empty, ErrorNote, PageTitle } from "@/components/shop/bits";
import { api } from "@/lib/api";
import { dateStr } from "@/lib/format";
import { Link } from "@/lib/nav";
import { ReviewForm } from "@/components/shop/ReviewForm";
import type { MyReview, Reviewable } from "@/lib/types";

export default function MyReviews() {
  const [reviews, setReviews] = useState<MyReview[] | null>(null);
  const [todo, setTodo] = useState<Reviewable[]>([]);
  const [names, setNames] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    api.myReviews().then((v) => {
      if (!live) return;
      setReviews(v);
      for (const id of new Set(v.map((r) => r.product_id))) {
        api.product(id).then((p) => { if (live) setNames((n) => ({ ...n, [id]: p.name })); }).catch(() => undefined);
      }
    }).catch(() => { if (live) setError("후기를 불러오지 못했어요. 잠시 후 다시 확인해 주세요."); });
    api.reviewable().then((v) => { if (live) setTodo(v); }).catch(() => undefined);
    return () => { live = false; };
  }, [tick]);

  return (
    <>
      <PageTitle title="내가 쓴 후기" />
      <ErrorNote error={error} />
      {todo.length > 0 && (
        <section className="mb-6 rounded-xl border border-ink-100 bg-surface p-4">
          <h2 className="mb-2 text-sm font-semibold">후기를 쓸 수 있어요</h2>
          <ul className="space-y-1 text-sm">
            {todo.map((t) => {
              const key = `${t.order_id}/${t.product_id}`;
              return (
                <li key={key}>
                  <div className="flex items-center justify-between gap-2">
                    <span className="min-w-0 truncate">{t.title ?? t.product_id}</span>
                    <button type="button" className="btn-ghost shrink-0 py-1 text-xs" aria-expanded={open === key} onClick={() => setOpen(open === key ? null : key)}>
                      {open === key ? "닫기" : "후기 쓰기"}
                    </button>
                  </div>
                  {open === key && (
                    <div className="mt-2">
                      <ReviewForm productId={t.product_id} targets={[{ order_id: t.order_id }]} onDone={() => { setOpen(null); setTick((n) => n + 1); }} />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {reviews === null && !error && <p className="text-sm text-ink-500">불러오는 중…</p>}
      {reviews?.length === 0 && <Empty>아직 쓴 후기가 없어요.</Empty>}
      <ul className="space-y-2">
        {reviews?.map((r) => (
          <li key={r.review_id} className="rounded-xl border border-ink-100 bg-surface p-4 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span aria-label={`별점 ${r.rating}점`} className="text-amber-500">{"★".repeat(r.rating)}{"☆".repeat(Math.max(0, 5 - r.rating))}</span>
              <Link href={`/products/${encodeURIComponent(r.product_id)}#reviews`} className="text-sm font-semibold underline">{names[r.product_id] ?? r.product_id}</Link>
              <span className="ml-auto text-xs text-ink-500">{dateStr(r.created_at)}</span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-ink-700">{r.body}</p>
          </li>
        ))}
      </ul>
    </>
  );
}
