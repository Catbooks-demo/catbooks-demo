import { Link } from "@/lib/nav";
import { useParams, useRouter } from "@/lib/nav";
import { useCallback, useEffect, useRef, useState } from "react";
import { ErrorNote, Price } from "@/components/shop/bits";
import { Cover } from "@/components/shop/Cover";
import { api } from "@/lib/api";
import { useCart } from "@/lib/cart";
import { hueOf } from "@/lib/format";
import { useCategories } from "@/lib/categories";
import { useSellers } from "@/lib/sellers";
import { useAuth } from "@/lib/auth";
import { useGenres } from "@/lib/genres";
import { ProductRow } from "@/components/shop/ProductRow";
import { ReviewForm } from "@/components/shop/ReviewForm";
import { ReviewList, Stars } from "@/components/shop/ReviewList";
import type { Product, Reviewable } from "@/lib/types";

type Tab = "intro" | "reviews" | "returns";
const TABS: { key: Tab; label: string }[] = [
  { key: "intro", label: "책 소개" },
  { key: "reviews", label: "구매 후기" },
  { key: "returns", label: "교환·반품 안내" },
];

export default function ProductPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const cart = useCart();
  const { label: categoryLabel } = useCategories();
  const { label: sellerLabel } = useSellers();
  const [p, setP] = useState<Product | null>(null);
  const [qty, setQty] = useState(1);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState(false);
  const { user } = useAuth();
  const { genres } = useGenres();
  const [tab, setTab] = useState<Tab>(() => (typeof window !== "undefined" && window.location.hash === "#reviews" ? "reviews" : "intro"));
  const [reviewKey, setReviewKey] = useState(0);
  const [summary, setSummary] = useState<{ count: number; avg: number } | null>(null);
  const [todo, setTodo] = useState<Reviewable[]>([]);
  const [same, setSame] = useState<Product[]>([]);
  const [wrote, setWrote] = useState(false);
  const reviewsRef = useRef<HTMLElement>(null);

  useEffect(() => {
    setP(null);
    setError(null);
    api.product(id).then(setP).catch((e: Error) => setError(e.message));
  }, [id]);

  // 주소에 #reviews가 있으면 후기 탭을 열고 그 자리로 간다.
  useEffect(() => {
    const open = () => { if (window.location.hash === "#reviews") setTab("reviews"); };
    open();
    window.addEventListener("hashchange", open);
    return () => window.removeEventListener("hashchange", open);
  }, []);
  useEffect(() => {
    if (p && window.location.hash === "#reviews") requestAnimationFrame(() => reviewsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  }, [p]);

  useEffect(() => {
    setSummary(null);
    setWrote(false);
    api.reviews(id, 1, 1).then((r) => setSummary({ count: r.summary.count, avg: r.summary.avg })).catch(() => undefined);
  }, [id]);

  const loadTodo = useCallback(() => {
    if (!user) { setTodo([]); return; }
    api.reviewable().then((v) => setTodo(v.filter((t) => t.product_id === id))).catch(() => setTodo([]));
  }, [user, id]);
  useEffect(loadTodo, [loadTodo]);

  const genreCode = p?.genre_code ?? null;
  useEffect(() => {
    setSame([]);
    if (!genreCode) return;
    api.best({ genre: genreCode, limit: 6 }).then((l) => setSame(l.filter((b) => b.product_id !== id).slice(0, 6))).catch(() => undefined);
  }, [genreCode, id]);

  if (error) return <ErrorNote error={error} />;
  if (!p) return <div className="py-20 text-center text-sm text-ink-500">불러오는 중…</div>;

  const soldOut = p.stock <= 0;
  const genreName = p.genre_code ? genres.find((g) => g.genre_code === p.genre_code)?.name ?? null : null;
  const info: [string, string | null | undefined][] = [
    ["저자", p.author], ["출판사", p.publisher], ["ISBN", p.isbn13],
  ];
  const onWrote = () => { setWrote(true); setReviewKey((k) => k + 1); loadTodo(); };

  return (
    <div>
      <nav className="mb-4 text-xs text-ink-500" aria-label="경로">
        <Link href="/" className="hover:underline">홈</Link> › <Link href={`/products?category=${p.category_code}`} className="hover:underline">{categoryLabel(p.category_code)}</Link>
      </nav>
      <div className="grid gap-8 md:grid-cols-[320px_1fr]">
        <div className="rounded-xl bg-surface-2 p-8">
          <Cover title={p.name} hue={hueOf(p.product_id)} category={p.category_code} productId={p.product_id} coverUrl={p.cover_url} className="mx-auto max-w-[240px]" />
          {(p.isbn13 || p.cover_url) && <p className="mt-3 text-center text-[11px] text-ink-500">도서 정보·표지: 도서관 정보나루(국립중앙도서관)</p>}
        </div>
        <div>
          <p className="text-xs font-semibold text-brand-600">{[categoryLabel(p.category_code), genreName].filter(Boolean).join(" · ")}</p>
          <h1 className="mt-1 break-keep text-2xl font-bold leading-snug">{p.name}</h1>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            {info.filter(([, v]) => v && v.trim()).map(([k, v]) => (<div key={k} className="contents"><dt className="text-ink-500">{k}</dt><dd>{v}</dd></div>))}
            <dt className="text-ink-500">판매자</dt><dd>{sellerLabel(p.seller_id)}</dd>
            <dt className="text-ink-500">상품번호</dt><dd>{p.product_id}</dd>
          </dl>
          <p className="mt-3 flex items-center gap-2 text-sm">
            {summary && summary.count > 0 ? (<><Stars value={summary.avg} /><span className="font-semibold tabular-nums">{summary.avg.toFixed(1)}</span><button type="button" className="text-ink-500 underline" onClick={() => { setTab("reviews"); reviewsRef.current?.scrollIntoView({ behavior: "smooth" }); }}>후기 {summary.count}개</button></>) : (<span className="text-ink-500">아직 후기가 없어요</span>)}
          </p>
          <div className="mt-4 border-y border-ink-100 py-4">
            <Price amount={p.price} big />
            <p className="mt-1 text-xs text-ink-500">배송비·최종 결제 금액은 주문할 때 계산돼요.</p>
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

      <section id="reviews" ref={reviewsRef} className="mt-14 scroll-mt-20">
        <div role="tablist" aria-label="상품 안내" className="flex border-b border-ink-100">
          {TABS.map((t) => (
            <button key={t.key} role="tab" id={`tab-${t.key}`} aria-selected={tab === t.key} aria-controls={`panel-${t.key}`} type="button" onClick={() => setTab(t.key)}
              className={`-mb-px border-b-2 px-4 py-3 text-sm font-semibold ${tab === t.key ? "border-ink-900 text-ink-900" : "border-transparent text-ink-500 hover:text-ink-900"}`}>
              {t.label}{t.key === "reviews" && summary ? ` (${summary.count})` : ""}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="pt-6">
          {tab === "intro" && (p.description && p.description.trim()
            ? <p className="whitespace-pre-wrap break-keep text-sm leading-relaxed text-ink-700">{p.description}</p>
            : <p className="text-sm text-ink-500">소개를 준비하고 있어요</p>)}
          {tab === "reviews" && (
            <div className="space-y-6">
              {wrote && <p role="status" className="rounded-xl bg-surface-2 p-3 text-sm">후기를 등록했어요. 고마워요!</p>}
              {todo.length > 0
                ? <ReviewForm key={todo.map((t) => t.order_id).join()} productId={id} targets={todo.map((t) => ({ order_id: t.order_id }))} onDone={onWrote} />
                : <p className="text-sm text-ink-500">구매한 분만 쓸 수 있어요. 배송이 끝난 주문의 후기는 마이페이지에서도 쓸 수 있어요.</p>}
              <ReviewList productId={id} reloadKey={reviewKey} onSummary={(count, avg) => setSummary({ count, avg })} />
            </div>
          )}
          {tab === "returns" && (
            <div className="space-y-2 text-sm leading-relaxed text-ink-700">
              <ul className="list-disc space-y-1 pl-5">
                <li>단순변심은 모의 수령일로부터 7일 이내, 불량·파손·오배송은 30일 이내에 신청할 수 있어요.</li>
                <li>반품 배송비는 단순변심이면 3,000원이고, 불량·파손·오배송이면 무료예요.</li>
                <li>환불·교환은 마이페이지의 주문에서 클레임으로 신청해요. 신청 전에 환불 예정액을 미리 볼 수 있어요.</li>
                <li>환불과 교환은 시연 기록이며 실제 금전 반환이나 상품 교환을 뜻하지 않아요.</li>
              </ul>
              <p><Link href="/buyer-terms" className="underline">구매 이용약관 전체 보기</Link></p>
            </div>
          )}
        </div>
      </section>

      {genreName && p.genre_code && same.length > 0 && (
        <ProductRow title={`${genreName} 분야 베스트`} href={`/products?genre=${p.genre_code}`} items={same} />
      )}
    </div>
  );
}
