import { useState, type FormEvent } from "react";
import { ErrorNote, PageTitle } from "@/components/shop/bits";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { AGE_BANDS, GENDERS, OCCUPATIONS, type AgeBand, type Gender, type Occupation } from "@/lib/types";

/** 회원정보 수정. 연령대·성별·직업은 선택이고 비우면 "모름"으로 저장한다(채워 넣지 않는다). */
export default function MyProfile() {
  const { user, refresh } = useAuth();
  const [name, setName] = useState(user?.display_name ?? "");
  const [age, setAge] = useState<AgeBand | "">(user?.age_band ?? "");
  const [gender, setGender] = useState<Gender | "">(user?.gender ?? "");
  const [job, setJob] = useState<Occupation | "">(user?.occupation ?? "");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setNote(null);
    setError(null);
    try {
      await api.updateMe({ display_name: name.trim(), age_band: age || null, gender: gender || null, occupation: job || null });
      await refresh();
      setNote("저장했어요.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (!user) return null;
  return (
    <div className="max-w-md">
      <PageTitle title="회원정보 수정" />
      <ErrorNote error={error} />
      {note && <p role="status" className="mb-3 rounded-lg bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800">{note}</p>}
      <form onSubmit={submit} className="grid gap-3 rounded-xl border border-ink-100 bg-surface p-4">
        <div className="grid gap-1 text-sm">
          <span className="font-medium">이메일</span>
          <span className="text-ink-500">{user.email ?? "등록된 이메일이 없어요"}</span>
        </div>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">이름</span>
          <input className="field" required maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">연령대 <span className="text-xs font-normal text-ink-500">(선택)</span></span>
          <select className="field" value={age} onChange={(e) => setAge(e.target.value as AgeBand | "")}>
            <option value="">선택 안 함</option>
            {AGE_BANDS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
          </select>
        </label>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">성별 <span className="text-xs font-normal text-ink-500">(선택)</span></span>
          <select className="field" value={gender} onChange={(e) => setGender(e.target.value as Gender | "")}>
            <option value="">선택 안 함</option>
            {GENDERS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
          </select>
        </label>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">직업 <span className="text-xs font-normal text-ink-500">(선택)</span></span>
          <select className="field" value={job} onChange={(e) => setJob(e.target.value as Occupation | "")}>
            <option value="">선택 안 함</option>
            {OCCUPATIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <p className="text-xs text-ink-500">생년월일은 받지 않고 구간만 저장해요. 추천과 이벤트에만 써요.</p>
        <button className="btn-primary" disabled={busy || !name.trim()}>{busy ? "저장 중…" : "저장"}</button>
      </form>
    </div>
  );
}
