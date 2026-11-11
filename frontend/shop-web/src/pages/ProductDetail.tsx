import { Link } from "@/lib/nav";
import { useParams, useRouter } from "@/lib/nav";
import { useEffect, useState } from "react";
import { ErrorNote, NotReady, Price } from "@/components/shop/bits";
import { Cover } from "@/components/shop/Cover";
import { api } from "@/lib/api";
import { useCart } from "@/lib/cart";
import { hueOf } from "@/lib/format";
import { useCategories } from "@/lib/categories";
import type { Product } from "@/lib/types";

export default function ProductPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const cart = useCart();
  const { label: categoryLabel } = useCategories();
  const [p, setP] = useState<Product | null>(null);
  const [qty, setQty] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState(false);

  useEffect(() => {
    setP(null);
    setError(null);
    api.product(id).then(setP).catch((e: Error) => setError(e.message));
  }, [id]);

  if (error) return <ErrorNote error={error} />;
  if (!p) return <div className="py-20 text-center text-sm text-ink-500">불러오는 중…</div>;

  const soldOut = p.stock <= 0;

  return (
    <div>
      <nav className="mb-4 text-xs text-ink-500" aria-label="경로">
        <Link href="/" className="hover:underline">홈</Link> › <Link href={`/products?category=${p.category_code}`} className="hover:underline">{categoryLabel(p.category_code)}</Link>
      </nav>
      <div className="grid gap-8 md:grid-cols-[320px_1fr]">
        <div className="rounded-xl bg-surface-2 p-8"><Cover title={p.name} hue={hueOf(p.product_id)} category={p.category_code} productId={p.product_id} className="mx-auto max-w-[240px]" /></div>
        <div>
          <p className="text-xs font-semibold text-brand-600">{categoryLabel(p.category_code)}</p>
          <h1 className="mt-1 break-keep text-2xl font-bold leading-snug">{p.name}</h1>
          <p className="mt-1 text-sm text-ink-500">판매자 {p.seller_id} · 상품번호 {p.product_id}</p>
          <div className="mt-4 border-y border-ink-100 py-4">
            <Price amount={p.price} big />
            <p className="mt-1 text-xs text-ink-500">배송비·최종 결제 금액은 주문 시 판매 서버가 계산합니다.</p>
          </div>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            <div className="flex items-center rounded-full border border-ink-300">
              <button className="px-3 py-2 text-lg" onClick={() => setQty((q) => Math.max(1, q - 1))} aria-label="수량 줄이기">−</button>
              <span className="w-8 text-center text-sm font-semibold tabular-nums" aria-live="polite">{qty}</span>
              <button className="px-3 py-2 text-lg" onClick={() => setQty((q) => Math.min(20, q + 1))} aria-label="수량 늘리기">＋</button>
            </div>
            <button className="btn btn-ghost px-6 py-2.5 text-sm" disabled={soldOut} onClick={() => { cart.add(p, qty); setAdded(true); setTimeout(() => setAdded(false), 1800); }}>
              {added ? "담았어요 ✓" : "장바구니"}
            </button>
            <button className="btn-primary px-8 py-2.5" disabled={soldOut} onClick={() => { cart.add(p, qty); router.push("/cart"); }}>
              {soldOut ? "일시품절" : "바로구매"}
            </button>
          </div>
          {!soldOut && p.stock <= 5 && <p className="mt-2 text-xs font-medium text-accent">남은 수량 {p.stock}개</p>}
        </div>
      </div>

      <section id="reviews" className="mt-14">
        <h2 className="text-lg font-bold">구매 후기</h2>
        {/* TODO(ax-backend#385): 후기 API가 생기면 api.reviews로 연결한다 */}
        <div className="mt-3"><NotReady feature="구매 후기" /></div>
      </section>
    </div>
  );
}
