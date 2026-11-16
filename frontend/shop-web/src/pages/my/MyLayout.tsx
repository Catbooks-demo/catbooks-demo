import { useEffect, useState } from "react";
import { Outlet } from "react-router";
import { Link, usePathname, useRouter } from "@/lib/nav";
import { NotReady, RequireLogin } from "@/components/shop/bits";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CLAIM_TERMINAL } from "@/lib/labels";
import type { MyClaim, MyInquiry, Order } from "@/lib/types";

const MENU: { title: string; items: { href: string; label: string; exact?: boolean }[] }[] = [
  { title: "쇼핑", items: [
    { href: "/my/orders", label: "주문·배송 조회" },
    { href: "/my/claims", label: "취소·반품·교환" },
    { href: "/cart", label: "장바구니" },
  ] },
  { title: "활동", items: [
    { href: "/my/inquiries", label: "1:1 문의 내역" },
    { href: "/my/ideas", label: "고객의 소리" },
    { href: "/my/reviews", label: "내가 쓴 후기" },
  ] },
  { title: "내 정보", items: [
    { href: "/my/profile", label: "회원정보 수정" },
    { href: "/my/password", label: "비밀번호 변경" },
  ] },
];

const THREE_MONTHS_MS = 92 * 24 * 3600 * 1000;

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-ink-100 bg-surface px-4 py-3">
      <p className="text-xs text-ink-500">{label}</p>
      <p className="mt-1 text-xl font-bold tabular-nums">{value}</p>
    </div>
  );
}

function Summary({ buyer }: { buyer: string }) {
  const [orders, setOrders] = useState<Order[] | null>(null);
  const [claims, setClaims] = useState<MyClaim[] | null>(null);
  const [inquiries, setInquiries] = useState<MyInquiry[] | null>(null);
  const path = usePathname(); // 다른 메뉴로 갔다 오면 숫자를 다시 읽는다

  useEffect(() => {
    let live = true;
    api.orders(buyer).then((v) => { if (live) setOrders(v); }).catch(() => undefined);
    api.myClaims(buyer).then((v) => { if (live) setClaims(v); }).catch(() => undefined);
    api.myInquiries().then((v) => { if (live) setInquiries(v); }).catch(() => undefined);
    return () => { live = false; };
  }, [buyer, path]);

  const since = Date.now() - THREE_MONTHS_MS;
  const recent = orders?.filter((o) => new Date(o.created_at).getTime() >= since).length;
  const active = claims?.filter((c) => !CLAIM_TERMINAL.has(c.status)).length;
  const waiting = inquiries?.filter((q) => q.status === "open").length;
  const show = (n: number | undefined) => (n === undefined ? "–" : `${n}건`);

  return (
    <div className="grid gap-3 sm:grid-cols-4">
      <Stat label="최근 3개월 주문" value={show(recent)} />
      <Stat label="진행 중 클레임" value={show(active)} />
      <Stat label="답변 대기 문의" value={show(waiting)} />
      <div className="rounded-xl border border-ink-100 bg-surface px-4 py-3">
        <p className="text-xs text-ink-500">적립금</p>
        <p className="mt-1 text-sm font-semibold text-ink-500">나중에 열려요</p>
      </div>
    </div>
  );
}

export default function MyLayout() {
  const { user, ready, logout } = useAuth();
  const router = useRouter();
  const path = usePathname();

  return (
    <RequireLogin user={user} ready={ready}>
      {user && (
        <>
          <div className="mb-6">
            <h1 className="mb-3 text-2xl font-bold tracking-tight"><Link href="/my">마이페이지</Link> <span className="text-base font-medium text-ink-500">{user.display_name}님</span></h1>
            <Summary buyer={user.buyer_id} />
          </div>
          <div className="grid gap-6 md:grid-cols-[13rem_1fr]">
            <nav aria-label="마이페이지 메뉴" className="space-y-4 text-sm">
              {MENU.map((g) => (
                <div key={g.title}>
                  <p className="mb-1 px-3 text-xs font-semibold text-ink-500">{g.title}</p>
                  <ul>
                    {g.items.map((m) => {
                      const on = path === m.href || path.startsWith(`${m.href}/`);
                      return (
                        <li key={m.href}>
                          <Link href={m.href} aria-current={on ? "page" : undefined} className={`block rounded-lg px-3 py-2 hover:bg-ink-100 ${on ? "bg-ink-100 font-semibold text-brand-600" : "text-ink-700"}`}>{m.label}</Link>
                        </li>
                      );
                    })}
                    {g.title === "내 정보" && (
                      <li><button className="block w-full rounded-lg px-3 py-2 text-left text-ink-700 hover:bg-ink-100" onClick={() => { void logout().then(() => router.push("/")); }}>로그아웃</button></li>
                    )}
                  </ul>
                </div>
              ))}
            </nav>
            <section className="min-w-0"><Outlet /></section>
          </div>
        </>
      )}
    </RequireLogin>
  );
}

/** 적립금 자리. 마이페이지 첫 화면에서 안내만 한다. */
export function PointsNotReady() { return <NotReady feature="적립금 조회" />; }
