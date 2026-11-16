import { Link } from "@/lib/nav";
import { useRouter, useSearchParams } from "@/lib/nav";
import { Suspense, useEffect, useState } from "react";
import { Empty, ErrorNote, PageTitle } from "@/components/shop/bits";
import { ProductCard } from "@/components/shop/ProductCard";
import { api, ApiError } from "@/lib/api";
import { useCategories } from "@/lib/categories";
import { useGenres } from "@/lib/genres";
import type { ProductPage, ProductSort } from "@/lib/types";

const SORTS: { key: ProductSort; label: string }[] = [
  { key: "popular", label: "인기순" },
  { key: "new", label: "신상품순" },
  { key: "price_asc", label: "낮은 가격순" },
  { key: "price_desc", label: "높은 가격순" },
];
const SIZE = 24;

export default function ProductsPage() {
  return (
    <Suspense>
      <List />
    </Suspense>
  );
}

function List() {
  const params = useSearchParams();
  const router = useRouter();
  const genre = params.get("genre") ?? "";
  const category = params.get("category") ?? "";
  const q = params.get("q") ?? "";
  const sortParam = params.get("sort") ?? "";
  const sort: ProductSort = SORTS.some((s) => s.key === sortParam) ? (sortParam as ProductSort) : "popular";
  const page = Math.max(1, Number(params.get("page")) || 1);
  const [data, setData] = useState<ProductPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { genres } = useGenres();
  const { label: categoryLabel } = useCategories();

  useEffect(() => {
    let live = true;
    setError(null);
    setLoading(true);
    api.productsPage({ q, genre, category, sort, page, size: SIZE })
      .then((r) => { if (live) { setData(r); setLoading(false); } })
      .catch((e: Error) => {
        if (!live) return;
        setLoading(false);
        // 없는 장르(422)는 오류 대신 "결과 없음"으로 보여 준다
        if (e instanceof ApiError && e.status === 422) setData({ items: [], total: 0, page, size: SIZE });
        else setError(e.message);
      });
    return () => { live = false; };
  }, [q, genre, category, sort, page]);

  const go = (next: Record<string, string>) => {
    const sp = new URLSearchParams({ ...(genre && { genre }), ...(category && { category }), ...(q && { q }), sort, ...next });
    for (const [k, v] of [...sp.entries()]) if (!v || (k === "sort" && v === "popular") || (k === "page" && v === "1")) sp.delete(k);
    const qs = sp.toString();
    router.push(qs ? `/products?${qs}` : "/products");
  };
  /** 현재 검색어·분류·정렬은 두고 일부만 바꾼 목록 주소 */
  const keep = (next: Record<string, string>) => {
    const sp = new URLSearchParams({ ...(category && { category }), ...(q && { q }), ...(sort !== "popular" && { sort }), ...next });
    for (const [k, v] of [...sp.entries()]) if (!v) sp.delete(k);
    const qs = sp.toString();
    return qs ? `/products?${qs}` : "/products";
  };
  const genreName = genres.find((g) => g.genre_code === genre)?.name;
  const title = q ? `'${q}' 검색 결과` : genre ? (genreName ?? "장르") : category ? categoryLabel(category) : "전체 상품";
  const lastPage = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1;

  return (
    <div>
      <PageTitle title={title} sub={data ? `${data.total}개 상품` : undefined}
        right={
          <label className="flex items-center gap-2 text-sm text-ink-500">정렬
            <select className="field w-auto py-1.5" value={sort} onChange={(e) => go({ sort: e.target.value, page: "" })}>
              {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
        } />
      <div className="grid gap-6 lg:grid-cols-[180px_1fr]">
        <nav aria-label="장르" className="flex gap-2 overflow-x-auto lg:flex-col lg:gap-1 lg:overflow-visible">
          <Link href={keep({ genre: "" })}
            className={`chip shrink-0 whitespace-nowrap py-1.5 text-xs lg:rounded-md lg:px-3 lg:text-sm ${!genre ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>전체</Link>
          {genres.filter((g) => g.product_count > 0 || g.genre_code === genre).map((g) => (
            <Link key={g.genre_code} href={keep({ genre: g.genre_code })}
              className={`chip shrink-0 whitespace-nowrap py-1.5 text-xs lg:rounded-md lg:px-3 lg:text-sm ${genre === g.genre_code ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>{g.name}</Link>
          ))}
        </nav>
        <div>
          <ErrorNote error={error} />
          {data && !data.items.length && (
            <Empty>
              결과가 없어요.{" "}
              {data.total > 0 && page > lastPage && <button className="font-semibold text-brand-600 underline" onClick={() => go({ page: String(lastPage) })}>마지막 쪽으로 가기</button>}{" "}
              <Link href="/products" className="font-semibold text-brand-600 underline">전체 상품 보기</Link>
            </Empty>
          )}
          <div className={`grid grid-cols-2 gap-x-4 gap-y-8 sm:grid-cols-3 xl:grid-cols-4 transition-opacity ${loading ? "opacity-50" : ""}`}>
            {(data?.items ?? []).map((p) => <ProductCard key={p.product_id} p={p} />)}
          </div>
          {data && (lastPage > 1 || page > 1) && (
            <div className="mt-8 flex items-center justify-center gap-4 text-sm">
              <button className="btn-ghost" disabled={page <= 1} onClick={() => go({ page: String(Math.min(page - 1, lastPage)) })}>이전</button>
              <span className="tabular-nums text-ink-500">{page} / {lastPage}</span>
              <button className="btn-ghost" disabled={page >= lastPage} onClick={() => go({ page: String(page + 1) })}>다음</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
