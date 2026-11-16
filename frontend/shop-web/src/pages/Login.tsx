import { useState, type FormEvent } from "react";
import { Link, useRouter, useSearchParams } from "@/lib/nav";
import { ErrorNote, PageTitle } from "@/components/shop/bits";
import { useAuth } from "@/lib/auth";

/** `?next=`는 같은 사이트 안 경로(`/`로 시작하고 `//`나 `/\`가 아닌 것)만 따른다. */
export function safeNext(raw: string | null): string {
  return raw && raw.startsWith("/") && !raw.startsWith("//") && !raw.startsWith("/\\") ? raw : "/my";
}

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      router.replace(next);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md">
      <PageTitle title="로그인" sub="가입한 이메일과 비밀번호로 로그인해요." />
      <ErrorNote error={error} />
      <form onSubmit={submit} className="grid gap-3 rounded-xl border border-ink-100 bg-surface p-4">
        <label className="grid gap-1 text-sm">
          <span className="font-medium">이메일</span>
          <input className="field" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">비밀번호</span>
          <input className="field" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <button className="btn-primary" disabled={busy}>{busy ? "로그인 중…" : "로그인"}</button>
      </form>
      <p className="mt-4 text-center text-sm text-ink-500">
        아직 계정이 없나요? <Link href="/signup" className="font-semibold text-brand-600 underline">회원가입</Link>
      </p>
    </div>
  );
}
