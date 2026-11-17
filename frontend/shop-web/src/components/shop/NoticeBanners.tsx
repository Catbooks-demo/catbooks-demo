import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Banner } from "@/lib/types";

const POLL_MS = 30_000;

/**
 * 게시 중인 공지 배너를 화면 맨 위에 보여 준다(shop-api `GET /api/shop/banners`).
 * - 문구는 서버가 준 값 그대로, React 텍스트로만 그린다(HTML 해석 없음).
 * - 0건이거나 조회에 실패하면 아무것도 그리지 않는다(오류 표시 없음). 실패해도 직전 목록은 유지한다.
 * - 서버는 오래된 순으로 주므로 최신 게시가 위로 오게 뒤집는다.
 */
export function NoticeBanners() {
  const [banners, setBanners] = useState<Banner[]>([]);

  useEffect(() => {
    let live = true;
    const load = () => {
      api.banners().then((b) => live && setBanners([...b].reverse())).catch(() => { /* 조용히 둔다 */ });
    };
    load();
    const t = setInterval(load, POLL_MS);
    return () => { live = false; clearInterval(t); };
  }, []);

  if (banners.length === 0) return null;
  return (
    <section aria-label="공지" className="border-b border-brand-100 bg-brand-50 text-ink-900">
      <ul className="mx-auto flex max-w-6xl flex-col gap-1 px-4 py-2 text-sm">
        {banners.map((b) => (
          <li key={b.notice_id} role="status" className="break-words">
            <span className="mr-2 rounded bg-brand-600 px-1.5 py-0.5 text-[11px] font-bold text-white">공지</span>
            {b.text}
          </li>
        ))}
      </ul>
    </section>
  );
}
