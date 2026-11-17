import { Link } from "@/lib/nav";
import { PointsNotReady } from "./MyLayout";

const SHORTCUTS = [
  { href: "/my/orders", title: "주문·배송 조회", sub: "주문 상태를 보고 취소·반품·교환을 신청해요." },
  { href: "/my/inquiries", title: "1:1 문의 내역", sub: "보낸 문의의 처리 상태를 확인해요." },
  { href: "/my/reviews", title: "내가 쓴 후기", sub: "쓴 후기와 쓸 수 있는 후기를 봐요." },
  { href: "/my/profile", title: "회원정보 수정", sub: "이름과 연령대·성별·직업을 바꿔요." },
];

export default function MyHome() {
  return (
    <div className="space-y-4">
      <ul className="grid gap-3 sm:grid-cols-2">
        {SHORTCUTS.map((s) => (
          <li key={s.href}>
            <Link href={s.href} className="block rounded-xl border border-ink-100 bg-surface p-4 hover:border-brand-500">
              <span className="block font-semibold">{s.title}</span>
              <span className="mt-1 block text-xs text-ink-500">{s.sub}</span>
            </Link>
          </li>
        ))}
      </ul>
      <PointsNotReady />
    </div>
  );
}
