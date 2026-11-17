import { Link } from "@/lib/nav";
import { Empty, PageTitle, PriceSummary, useCartPrice } from "@/components/shop/bits";
import { Cover } from "@/components/shop/Cover";
import { useCart } from "@/lib/cart";
import { hueOf, won } from "@/lib/format";

export default function CartPage() {
  const cart = useCart();
  const { price, error } = useCartPrice(cart.lines);

  if (!cart.lines.length) return <><PageTitle title="장바구니" /><Empty>담아 둔 상품이 없습니다. <Link href="/products" className="font-semibold text-brand-600 underline">상품 보러 가기</Link></Empty></>;

  return (
    <div>
      <PageTitle title="장바구니" sub={`${cart.count}개`} />
      <div className="grid gap-8 lg:grid-cols-[1fr_320px]">
        <ul className="divide-y divide-ink-100 rounded-xl border border-ink-100 bg-surface">
          {cart.lines.map((l) => (
            <li key={l.id} className="flex gap-4 p-4">
              <Link href={`/products/${l.id}`} className="w-20 shrink-0"><Cover title={l.title} hue={hueOf(l.id)} category={l.category} productId={l.id} compact /></Link>
              <div className="min-w-0 flex-1">
                <Link href={`/products/${l.id}`} className="line-clamp-2 break-keep text-sm font-semibold hover:underline">{l.title}</Link>
                <div className="mt-2 flex items-center gap-3">
                  <div className="flex items-center rounded-full border border-ink-300 text-sm">
                    <button className="px-2.5 py-1" onClick={() => cart.setQty(l.id, l.qty - 1)} aria-label="수량 줄이기">−</button>
                    <span className="w-7 text-center tabular-nums">{l.qty}</span>
                    <button className="px-2.5 py-1" onClick={() => cart.setQty(l.id, l.qty + 1)} aria-label="수량 늘리기">＋</button>
                  </div>
                  <button className="text-xs text-ink-500 hover:text-ink-900" onClick={() => cart.remove(l.id)}>삭제</button>
                </div>
              </div>
              <p className="text-right text-sm font-bold tabular-nums">{won(l.price * l.qty)}</p>
            </li>
          ))}
        </ul>
        <aside className="h-fit rounded-xl border border-ink-100 bg-surface p-5 text-sm">
          <PriceSummary price={price} error={error} />
          <Link href="/checkout" className="btn-primary mt-4 w-full py-3">주문하기</Link>
        </aside>
      </div>
    </div>
  );
}
