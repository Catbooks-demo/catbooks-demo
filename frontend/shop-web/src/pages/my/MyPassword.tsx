import { useState, type FormEvent } from "react";
import { ErrorNote, PageTitle } from "@/components/shop/bits";
import { api, authErrorMessage } from "@/lib/api";

export default function MyPassword() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setNote(null);
    if (next !== again) { setError("새 비밀번호가 서로 달라요."); return; }
    setBusy(true);
    setError(null);
    try {
      await api.changePassword(current, next);
      setCurrent(""); setNext(""); setAgain("");
      setNote("비밀번호를 바꿨어요. 다른 기기의 로그인은 모두 풀렸어요.");
    } catch (err) {
      setError(authErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-md">
      <PageTitle title="비밀번호 변경" />
      <ErrorNote error={error} />
      {note && <p role="status" className="mb-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800">{note}</p>}
      <form onSubmit={submit} className="grid gap-3 rounded-xl border border-ink-100 bg-surface p-4">
        <label className="grid gap-1 text-sm">
          <span className="font-medium">현재 비밀번호</span>
          <input className="field" type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">새 비밀번호</span>
          <input className="field" type="password" autoComplete="new-password" required value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">새 비밀번호 확인</span>
          <input className="field" type="password" autoComplete="new-password" required value={again} onChange={(e) => setAgain(e.target.value)} />
        </label>
        <button className="btn-primary" disabled={busy}>{busy ? "바꾸는 중…" : "비밀번호 변경"}</button>
      </form>
    </div>
  );
}
