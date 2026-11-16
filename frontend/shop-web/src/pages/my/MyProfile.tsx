import { useState, type FormEvent } from "react";
import { ErrorNote, PageTitle } from "@/components/shop/bits";
import { api, authErrorMessage } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useRouter } from "@/lib/nav";
import { AGE_BANDS, GENDERS, OCCUPATIONS, type AgeBand, type Gender, type MeUpdate, type Occupation } from "@/lib/types";

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
      // 이름은 계정(이메일)이 있는 회원이 바꿨을 때만 보낸다(계정 없는 데모 구매자는 이름을 못 바꾼다)
      const body: MeUpdate = { age_band: age || null, gender: gender || null, occupation: job || null };
      if (user?.email && name.trim() !== user.display_name) body.display_name = name.trim();
      await api.updateMe(body);
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
          <input className="field" required maxLength={20} value={name} disabled={!user.email} onChange={(e) => setName(e.target.value)} />
          {!user.email && <span className="text-xs text-ink-500">이메일 계정이 없는 시험용 구매자는 이름을 바꿀 수 없어요.</span>}
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
      {user.email && <Withdraw />}
    </div>
  );
}

/** 회원 탈퇴. 비밀번호를 한 번 더 받고, 지워지는 것과 남는 것을 먼저 알린다. */
function Withdraw() {
  const { logout } = useAuth();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy || !agree) return;
    setBusy(true);
    setError(null);
    try {
      await api.withdraw(password);
      await logout();
      router.replace("/");
    } catch (err) {
      const code = (err as { code?: string }).code;
      setError(code === "INVALID_PASSWORD" ? "비밀번호가 맞지 않아요." : authErrorMessage(err));
      setBusy(false);
    }
  }

  return (
    <section className="mt-8 rounded-xl border border-red-200 bg-surface p-4">
      <h2 className="text-sm font-semibold text-red-800">회원 탈퇴</h2>
      {!open ? (
        <button type="button" className="btn-ghost mt-2 text-sm text-red-700" onClick={() => setOpen(true)}>탈퇴하기</button>
      ) : (
        <form onSubmit={submit} className="mt-2 grid gap-3">
          <ul className="list-disc pl-5 text-xs text-ink-700">
            <li>회원정보(이메일·이름·연령대·성별·직업)와 후기, 로그인 기록은 바로 지워져요. 되돌릴 수 없어요.</li>
            <li>주문·클레임 기록은 법에 따라 5년 보관한 뒤 지워요.</li>
          </ul>
          <ErrorNote error={error} />
          <label className="grid gap-1 text-sm">
            <span className="font-medium">비밀번호 확인</span>
            <input className="field" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
            <span>위 내용을 확인했고 탈퇴할게요.</span>
          </label>
          <div className="flex gap-2">
            <button className="btn-primary bg-red-600 hover:bg-red-700" disabled={busy || !agree || !password}>{busy ? "탈퇴하는 중…" : "회원 탈퇴"}</button>
            <button type="button" className="btn-ghost text-sm" disabled={busy} onClick={() => { setOpen(false); setPassword(""); setAgree(false); setError(null); }}>취소</button>
          </div>
        </form>
      )}
    </section>
  );
}
