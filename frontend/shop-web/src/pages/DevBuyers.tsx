import { useEffect, useState } from "react";
import { useRouter } from "@/lib/nav";
import { ErrorNote, NotReady, PageTitle } from "@/components/shop/bits";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { DemoUser } from "@/lib/types";

/** 시험용: 옛 데모 구매자 한 명을 비밀번호 없이 골라 로그인한다. 서버의 `SHOP_DEMO_LOGIN=on`일 때만 열린다. */
export default function DevBuyersPage() {
  const { demoLogin, logout, user } = useAuth();
  const router = useRouter();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [users, setUsers] = useState<DemoUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    api.authConfig()
      .then((c) => {
        setEnabled(c.demo_login);
        if (c.demo_login) api.demoUsers().then(setUsers).catch((e: Error) => setError(e.message));
      })
      .catch((e: Error) => { setEnabled(false); setError(e.message); });
  }, []);

  async function pick(id: string) {
    setBusyId(id);
    setError(null);
    try {
      await demoLogin(id);
      router.push("/my/orders");
    } catch (e) {
      setError((e as Error).message);
      setBusyId(null);
    }
  }

  if (enabled === false) {
    return (
      <div className="mx-auto max-w-xl">
        <PageTitle title="시험용 구매자 고르기" />
        <ErrorNote error={error} />
        <p className="rounded-xl border border-dashed border-ink-300 px-4 py-8 text-center text-sm text-ink-500">시험용 로그인이 꺼져 있어요.</p>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-xl">
      <PageTitle title="시험용 구매자 고르기" sub="비밀번호 없이 데모 구매자 한 명으로 로그인해요. 개인정보는 없고 가명 ID와 데모 이름만 보여요." />
      <div className="mb-4"><NotReady feature="적립금 조회" /></div>
      <ErrorNote error={error} />
      {!users && !error && <p className="py-6 text-center text-sm text-ink-500">불러오는 중…</p>}
      <ul className="grid gap-2 sm:grid-cols-3">
        {(users ?? []).map((u) => (
          <li key={u.buyer_id}>
            <button onClick={() => pick(u.buyer_id)} disabled={busyId !== null} aria-pressed={user?.buyer_id === u.buyer_id}
              className={`w-full rounded-xl border bg-surface p-3 text-left text-sm transition hover:border-brand-500 ${user?.buyer_id === u.buyer_id ? "border-brand-600" : "border-ink-100"}`}>
              <span className="block font-medium">{u.display_name}</span>
              <span className="block font-mono text-xs text-ink-500">{u.buyer_id}</span>
            </button>
          </li>
        ))}
      </ul>
      {user && (
        <div className="mt-4">
          <button className="btn-ghost text-sm" onClick={() => { void logout(); }}>로그아웃</button>
        </div>
      )}
    </div>
  );
}
