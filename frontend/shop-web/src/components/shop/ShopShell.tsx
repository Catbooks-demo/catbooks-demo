import { Link } from "@/lib/nav";
import { usePathname, useRouter } from "@/lib/nav";
import { useState, type FormEvent, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { useCart } from "@/lib/cart";
import { DASHBOARD_WEB_URL } from "@/lib/env";
import { useCategories } from "@/lib/categories";
import { IconCart, IconSearch, IconUser } from "../icons";
import { NoticeBanners } from "./NoticeBanners";
import { ThemeToggle } from "../ThemeToggle";

function Logo() {
  return (
    <Link href="/" className="flex shrink-0 items-center gap-2 whitespace-nowrap" aria-label="캣북스 홈">
      <img src="/logo.png" width={34} height={34} alt="" aria-hidden="true" />
      <span className="text-xl font-extrabold tracking-tight text-brand-600">캣북스</span>
    </Link>
  );
}

export function ShopShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const { user, logout } = useAuth();
  const cart = useCart();
  const { categories, label } = useCategories();
  const [q, setQ] = useState("");

  function search(e: FormEvent) {
    e.preventDefault();
    router.push(`/products${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ""}`);
  }

  const nav = [
    { href: "/products", label: "전체 상품" },
    ...categories.map((c) => ({ href: `/products?category=${c.category_code}`, label: label(c.category_code) })),
    { href: "/support", label: "고객센터" },
  ];

  return (
    <div className="theme-shop flex min-h-screen flex-col bg-canvas text-ink-900">
      <NoticeBanners />
      <div className="border-b border-ink-100 bg-surface-2 text-xs text-ink-500">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5">
          <span className="hidden sm:inline">데모 서점입니다 · 실제 결제·배송은 일어나지 않습니다 · 판매 서버(shop-api)에 연결됩니다</span>
          <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-1 whitespace-nowrap">
            {user ? (
              <>
                <span className="font-medium text-ink-700">{user.name ?? user.id}</span>
                <button className="hover:text-ink-900" onClick={() => { logout(); router.push("/"); }}>선택 해제</button>
              </>
            ) : (
              <Link href="/login" className="hover:text-ink-900">구매자 선택</Link>
            )}
            <Link href="/orders" className="hover:text-ink-900">주문·배송</Link>
            {/* 다른 앱(게이트웨이의 /admin/)이라 라우터 Link가 아니라 일반 <a>로 연다. Link는 http(s)가 아니면 쇼핑몰 안 경로로 처리한다 */}
            <a href={DASHBOARD_WEB_URL} className="hover:text-ink-900">상담원 콘솔</a>
            <ThemeToggle />
          </div>
        </div>
      </div>

      <header className="border-b border-ink-100 bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-3 px-4 py-4">
          <Logo />
          <nav className="order-2 ml-auto flex items-center gap-1 sm:order-3 sm:ml-0" aria-label="바로가기">
            <Link href={user ? "/orders" : "/login"} className="flex flex-col items-center gap-0.5 whitespace-nowrap rounded-lg px-3 py-1 text-[11px] text-ink-700 hover:bg-ink-100">
              <IconUser size={20} />마이
            </Link>
            <Link href="/cart" className="relative flex flex-col items-center gap-0.5 whitespace-nowrap rounded-lg px-3 py-1 text-[11px] text-ink-700 hover:bg-ink-100">
              <IconCart size={20} />장바구니
              {cart.count > 0 && <span className="absolute right-1.5 top-0 min-w-[18px] rounded-full bg-accent px-1 text-center text-[10px] font-bold text-white">{cart.count}</span>}
            </Link>
          </nav>
          <form onSubmit={search} role="search" className="relative order-3 w-full sm:order-2 sm:mx-auto sm:w-auto sm:max-w-xl sm:flex-1">
            <input className="field rounded-full border-2 border-brand-600 py-2.5 pl-5 pr-12" placeholder="책 제목, 저자, 문구를 검색해 보세요"
              value={q} onChange={(e) => setQ(e.target.value)} aria-label="상품 검색" />
            <button className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded-full bg-brand-600 p-2 text-white" aria-label="검색"><IconSearch size={16} /></button>
          </form>
        </div>
        <nav className="border-t border-ink-100" aria-label="카테고리">
          <ul className="mx-auto flex max-w-6xl gap-1 overflow-x-auto px-4 text-sm font-medium">
            {nav.map((n) => {
              const on = n.href.includes("?") ? false : path.startsWith(n.href);
              return (
                <li key={n.href}>
                  <Link href={n.href} className={`block whitespace-nowrap px-3 py-3 hover:text-brand-600 ${on ? "text-brand-600" : "text-ink-700"}`}>{n.label}</Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8">{children}</main>

      <footer className="border-t border-ink-100 bg-surface-2 text-xs text-ink-500">
        <div className="mx-auto grid max-w-6xl gap-4 px-4 py-8 sm:grid-cols-3">
          <div>
            <p className="font-semibold text-ink-700">캣북스 고객센터</p>
            <p className="mt-1 leading-relaxed">영수증 기준으로 환불·교환·보상을 계산하고, 반품 상품이 도착한 순서대로 처리합니다.</p>
          </div>
          <div>
            <p className="font-semibold text-ink-700">반품·환불 안내</p>
            <ul className="mt-1 space-y-0.5 leading-relaxed">
              <li>단순변심: 수령 후 7일 이내, 미개봉·미사용 (반품 배송비 3,000원)</li>
              <li>불량·파손·오배송: 수령 후 30일 이내, 배송비 무료</li>
              <li>전자책·주문제작 굿즈는 단순변심 환불 불가</li>
            </ul>
          </div>
          <div>
            <p className="font-semibold text-ink-700">데모 안내</p>
            <p className="mt-1 leading-relaxed">상품은 모두 가상 데이터입니다. 취소·반품·교환 신청은 고객지원이 판정합니다.</p>
          </div>
        </div>
        <nav aria-label="약관" className="mx-auto flex max-w-6xl gap-4 px-4 pb-6">
          <Link href="/terms" className="font-semibold text-ink-700 hover:underline">이용약관</Link>
          <Link href="/buyer-terms" className="font-semibold text-ink-700 hover:underline">구매자 이용약관</Link>
          <Link href="/privacy" className="font-semibold text-ink-700 hover:underline">개인정보처리방침</Link>
          <a href={DASHBOARD_WEB_URL} className="ml-auto text-ink-500 hover:underline">관리자</a>
        </nav>
      </footer>
    </div>
  );
}
