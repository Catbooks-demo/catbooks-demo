import { Link } from "@/lib/nav";
import { useCategories } from "@/lib/categories";
import { hueOf } from "@/lib/format";
import type { Product } from "@/lib/types";
import { Cover } from "./Cover";
import { Price } from "./bits";

/** 표지·제목·저자·가격 카드. `rank`가 있으면 베스트 순위 표시. */
export function ProductCard({ p, rank }: { p: Product; rank?: number }) {
  const soldOut = p.stock <= 0;
  const { label } = useCategories();
  const sub = [p.author, p.publisher].filter(Boolean).join(" · ") || label(p.category_code);
  return (
    <Link href={`/products/${p.product_id}`} className="group flex w-full flex-col gap-2">
      <div className="relative rounded-md bg-surface-2 p-4 transition group-hover:bg-ink-100">
        <Cover title={p.name} hue={hueOf(p.product_id)} category={p.category_code} productId={p.product_id} className="mx-auto max-w-[170px]" />
        {rank !== undefined && <span className="absolute left-2 top-2 chip bg-brand-600 text-white">{rank}</span>}
        {soldOut && <span className="absolute right-2 top-2 chip bg-ink-900 text-surface">일시품절</span>}
      </div>
      <div className="min-w-0">
        <h3 className="line-clamp-2 break-keep text-sm font-semibold leading-snug group-hover:underline">{p.name}</h3>
        <p className="mt-0.5 truncate text-xs text-ink-500">{sub}</p>
        <div className="mt-1.5"><Price amount={p.price} /></div>
      </div>
    </Link>
  );
}
