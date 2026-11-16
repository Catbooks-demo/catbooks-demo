import { ClaimsLoadError, MyClaimCard, useMyClaims } from "@/components/shop/MyClaimCard";
import { Empty, PageTitle } from "@/components/shop/bits";
import { Link } from "@/lib/nav";
import { useAuth } from "@/lib/auth";

export default function MyClaims() {
  const { user } = useAuth();
  const { claims, failed, reload } = useMyClaims(user?.buyer_id);
  return (
    <>
      <PageTitle title="취소·반품·교환" sub="신청은 주문·배송 조회에서 하고, 처리 결과는 여기서 확인해요." right={<Link href="/my/orders" className="btn-ghost text-sm">주문 보기</Link>} />
      <ClaimsLoadError failed={failed} onRetry={() => { void reload(); }} />
      {!failed && claims.length === 0 && <Empty>신청한 내역이 없어요.</Empty>}
      <ul className="space-y-3">
        {claims.map((c) => (
          <li key={c.id}>
            <p className="mb-1 text-xs text-ink-500">주문 <Link href={`/my/orders/${encodeURIComponent(c.order_id)}`} className="font-mono underline">{c.order_id}</Link></p>
            <MyClaimCard c={c} />
          </li>
        ))}
      </ul>
    </>
  );
}
