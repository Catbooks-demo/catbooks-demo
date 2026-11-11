import { useEffect, useState } from "react";
import { useRouter } from "@/lib/nav";
import { ErrorNote, NotReady, PageTitle } from "@/components/shop/bits";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { DemoUser } from "@/lib/types";

export default function LoginPage() {
  const { login, logout, user } = useAuth();
  const router = useRouter();
  const [users, setUsers] = useState<DemoUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => { api.demoUsers().then(setUsers).catch((e: Error) => setError(e.message)); }, []);

  async function pick(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await login(id);
      router.push("/orders");
    } catch (e) {
      setError((e as Error).message);
      setBusyId(null);
    }
  }

  return (
    <div className="mx-auto max-w-xl">
      <PageTitle title="구매자 선택" sub="비밀번호 없이 데모 구매자 한 명을 골라 주문과 환불 신청을 해 볼 수 있습니다. 개인정보는 없고 가명 ID와 데모 이름만 보입니다." />
      <div className="mb-4"><NotReady feature="적립금 조회" /></div>
      <ErrorNote error={error} />
      {!users && !error && <p className="py-6 text-center text-sm text-ink-500">불러오는 중…</p>}
      <ul className="grid gap-2 sm:grid-cols-3">
        {(users ?? []).map((u) => (
          <li key={u.buyer_id}>
            <button onClick={() => pick(u.buyer_id)} disabled={busyId !== null} aria-pressed={user?.id === u.buyer_id}
              className={`w-full rounded-xl border bg-surface p-3 text-left text-sm transition hover:border-brand-500 ${user?.id === u.buyer_id ? "border-brand-600" : "border-ink-100"}`}>
              <span className="block font-medium">{u.display_name}</span>
              <span className="block font-mono text-xs text-ink-500">{u.buyer_id}</span>
            </button>
          </li>
        ))}
      </ul>
      {user && <button className="btn-ghost mt-4 text-sm" onClick={logout}>선택 해제</button>}
    </div>
  );
}
