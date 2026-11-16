import { useEffect, useState } from "react";
import { Empty, ErrorNote, PageTitle } from "@/components/shop/bits";
import { api } from "@/lib/api";
import { INQUIRY_STATUS_KO } from "@/lib/labels";
import { shortDateTime } from "@/lib/format";
import { Link } from "@/lib/nav";
import type { MyInquiry } from "@/lib/types";


export default function MyInquiries() {
  const [items, setItems] = useState<MyInquiry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

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
          </li>
        ))}
      </ul>
    </>
  );
}
