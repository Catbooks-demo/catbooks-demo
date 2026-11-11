import { Link } from "@/lib/nav";
import { useRouter, useSearchParams } from "@/lib/nav";
import { Suspense, useEffect, useMemo, useState } from "react";
import { Empty, ErrorNote, PageTitle, ProductCard } from "@/components/shop/bits";
import { api } from "@/lib/api";
import { useCategories } from "@/lib/categories";
import type { Product } from "@/lib/types";

// TODO(ax-backend#385): 판매량순·신상품순과 서버 검색이 생기면 서버 기준으로 바꾼다. 지금은 shop-api 상품 목록을 화면에서 거른다.
const SORTS = [
  { key: "default", label: "기본순" },
  { key: "price_asc", label: "낮은 가격순" },
  { key: "price_desc", label: "높은 가격순" },
];

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
  const category = params.get("category") ?? "";
  const q = params.get("q") ?? "";
  const sort = params.get("sort") ?? "default";
  const [all, setAll] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api.products().then(setAll).catch((e: Error) => setError(e.message)); }, []);

  const { categories, label: categoryLabel } = useCategories();
  const items = useMemo(() => {
    if (!all) return null;
    const needle = q.trim().toLowerCase();
    const list = all.filter((p) => (!category || p.category_code === category) && (!needle || p.name.toLowerCase().includes(needle)));
    if (sort === "price_asc") return [...list].sort((a, b) => a.price - b.price);
    if (sort === "price_desc") return [...list].sort((a, b) => b.price - a.price);
    return list;
  }, [all, category, q, sort]);

  const go = (next: Record<string, string>) => {
    const sp = new URLSearchParams({ ...(category && { category }), ...(q && { q }), sort, ...next });
    for (const [k, v] of [...sp.entries()]) if (!v || (k === "sort" && v === "default")) sp.delete(k);
    router.push(`/products?${sp.toString()}`);
  };
  const title = q ? `'${q}' 검색 결과` : category ? categoryLabel(category) : "전체 상품";

  return (
    <div>
      <PageTitle title={title} sub={items ? `${items.length}개 상품` : undefined}
        right={
          <label className="flex items-center gap-2 text-sm text-ink-500">정렬
            <select className="field w-auto py-1.5" value={sort} onChange={(e) => go({ sort: e.target.value })}>
              {SORTS.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
        } />
      <div className="mb-6 flex flex-wrap gap-2">
        <Link href="/products" className={`chip py-1.5 text-xs ${!category ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>전체</Link>
        {categories.map(({ category_code: c }) => (
          <Link key={c} href={`/products?category=${c}`}
            className={`chip py-1.5 text-xs ${category === c ? "bg-brand-600 text-white" : "bg-ink-100 text-ink-700"}`}>{categoryLabel(c)}</Link>
        ))}
      </div>
      <ErrorNote error={error} />
      {items && !items.length && <Empty>조건에 맞는 상품이 없습니다.</Empty>}
      <div className="grid grid-cols-2 gap-x-4 gap-y-8 sm:grid-cols-3 lg:grid-cols-5">
        {(items ?? []).map((p) => <ProductCard key={p.product_id} p={p} />)}
      </div>
    </div>
  );
}
