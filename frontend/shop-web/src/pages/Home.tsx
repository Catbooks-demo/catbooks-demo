import { Link } from "@/lib/nav";
import { useEffect, useState } from "react";
import { ErrorNote, ProductCard } from "@/components/shop/bits";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { Product } from "@/lib/types";
import { IconBox, IconReceipt, IconRefresh } from "@/components/icons";

function Shelf({ title, href, items }: { title: string; href: string; items: Product[] }) {
  return (
    <section className="mt-12">
      <div className="mb-4 flex items-end justify-between">
        <h2 className="text-xl font-bold tracking-tight">{title}</h2>
        <Link href={href} className="text-sm text-ink-500 hover:text-ink-900">더 보기 ›</Link>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-8 sm:grid-cols-3 lg:grid-cols-5">
        {items.map((p) => <ProductCard key={p.product_id} p={p} />)}
      </div>
    </section>
  );
}

export default function Home() {
  const { user } = useAuth();
  const [all, setAll] = useState<Product[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.products().then(setAll).catch((e: Error) => setError(e.message));
  }, []);
  // TODO(ax-backend#385): 판매량순·신상품순 API가 생기면 선반을 서버 기준으로 바꾼다. 지금은 상품 목록을 화면에서 나눈다.
  const books = all.filter((p) => p.category_code === "book").slice(0, 5);
  const goods = all.filter((p) => p.category_code === "stationery").slice(0, 5);

  return (
    <div>
      <ErrorNote error={error} />
      <section className="grid gap-4 lg:grid-cols-[1fr_320px]">
        <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-hero-from to-hero-to p-8 text-white sm:p-10">
          <p className="text-xs font-semibold uppercase tracking-widest text-white/80">Night Library Fair</p>
          <h1 className="mt-3 break-keep text-3xl font-extrabold leading-tight sm:text-4xl">밤의 도서관,<br />고양이가 책을 골라 드려요</h1>
          <p className="mt-3 max-w-md text-sm text-white/90">책과 문구를 한 곳에서. 주문은 판매 서버(shop-api)로 바로 들어갑니다.</p>
          <div className="mt-6 flex gap-2">
            <Link href="/products" className="rounded-full bg-white px-5 py-2.5 text-sm font-bold text-brand-700">전체 상품</Link>
            <Link href="/products?category=stationery" className="rounded-full border border-white/60 px-5 py-2.5 text-sm font-semibold text-white">문구 보기</Link>
          </div>
          <svg viewBox="0 0 200 120" className="pointer-events-none absolute -bottom-4 -right-4 hidden w-72 opacity-25 sm:block" aria-hidden="true">
            <path d="M20 118V30l40 30h80l40-30v88Z" fill="white" />
          </svg>
        </div>
        <aside className="rounded-2xl border border-ink-100 bg-surface p-5">
          <h2 className="text-sm font-bold">{user ? `${user.id} 구매자로 보는 중` : "처음 오셨나요?"}</h2>
          <p className="mt-1 text-xs text-ink-500">
            {user ? "주문 상세에서 취소·반품·교환·지연 보상을 바로 신청할 수 있어요." : "구매자(데모)를 고르면 주문과 환불 신청을 체험해 볼 수 있어요."}
          </p>
          <ul className="mt-4 space-y-3 text-sm">
            <li className="flex gap-3"><span className="text-brand-600"><IconReceipt /></span><span><b>영수증 기준 자동 계산</b><br /><span className="text-xs text-ink-500">수량·금액·적립금을 영수증과 대조해 환불액을 정해요</span></span></li>
            <li className="flex gap-3"><span className="text-brand-600"><IconRefresh /></span><span><b>고객지원이 환불액을 계산</b><br /><span className="text-xs text-ink-500">신청하면 고객지원이 주문 사실로 금액을 정해요 (신청 전 미리보기는 준비 중)</span></span></li>
            <li className="flex gap-3"><span className="text-brand-600"><IconBox /></span><span><b>반품 입고 순서대로 처리</b><br /><span className="text-xs text-ink-500">검수가 끝난 순서대로 환불·보상이 지급돼요</span></span></li>
          </ul>
          <Link href={user ? "/orders" : "/login"} className="btn-primary mt-4 w-full">{user ? "내 주문 보기" : "구매자 선택"}</Link>
        </aside>
      </section>

      <Shelf title="전체 상품" href="/products" items={all.slice(0, 5)} />
      <Shelf title="책" href="/products?category=book" items={books} />
      <Shelf title="책상 위를 채우는 문구" href="/products?category=stationery" items={goods} />
    </div>
  );
}
