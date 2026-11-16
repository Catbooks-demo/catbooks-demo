import { Link } from "@/lib/nav";
import type { Product } from "@/lib/types";
import { ProductCard } from "./ProductCard";

/** 제목과 "더 보기" 링크가 붙은 표지 한 줄. */
export function ProductRow({ title, href, items, ranked = false, cols = "lg:grid-cols-6" }: {
  title: string; href?: string; items: Product[]; ranked?: boolean; cols?: string;
}) {
  return (
    <section className="mt-10">
      <div className="mb-4 flex items-end justify-between">
        <h2 className="text-xl font-bold tracking-tight">{title}</h2>
        {href && <Link href={href} className="text-sm text-ink-500 hover:text-ink-900">더 보기 ›</Link>}
      </div>
      <div className={`grid grid-cols-2 gap-x-4 gap-y-8 sm:grid-cols-3 ${cols}`}>
        {items.map((p, i) => <ProductCard key={p.product_id} p={p} rank={ranked ? i + 1 : undefined} />)}
      </div>
    </section>
  );
}
