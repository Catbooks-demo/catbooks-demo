import { useState, type FormEvent } from "react";
import { Link, useRouter } from "@/lib/nav";
import { ErrorNote, PageTitle } from "@/components/shop/bits";
import { useAuth } from "@/lib/auth";
import { AGE_BANDS, GENDERS, OCCUPATIONS, type AgeBand, type Gender, type Occupation, type SignupInput } from "@/lib/types";

const PASSWORD_MIN = 8;

export default function SignupPage() {
  const { signup } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [ageBand, setAgeBand] = useState<AgeBand | "">("");
  const [gender, setGender] = useState<Gender | "">("");
  const [occupation, setOccupation] = useState<Occupation | "">("");
  const [terms, setTerms] = useState(false);
  const [privacy, setPrivacy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (password.length < PASSWORD_MIN) { setError(`비밀번호는 ${PASSWORD_MIN}자 이상이어야 해요.`); return; }
    if (!terms || !privacy) { setError("이용약관과 개인정보처리방침에 동의해 주세요."); return; }
    setBusy(true);
    setError(null);
    // 고르지 않은 칸은 보내지 않는다(모름으로 남는다)
    const input: SignupInput = { email: email.trim(), password, display_name: name.trim(), agree_terms: true, agree_privacy: true };
    if (ageBand) input.age_band = ageBand;
    if (gender) input.gender = gender;
    if (occupation) input.occupation = occupation;
    try {
      await signup(input);
      router.push("/");
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-md">
      <PageTitle title="회원가입" sub="이메일로 가입하면 주문과 환불 신청을 내 계정으로 할 수 있어요." />
      <ErrorNote error={error} />
      <form onSubmit={submit} className="grid gap-3 rounded-xl border border-ink-100 bg-surface p-4">
        <label className="grid gap-1 text-sm">
          <span className="font-medium">이메일</span>
          <input className="field" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">비밀번호</span>
          <input className="field" type="password" autoComplete="new-password" required minLength={PASSWORD_MIN} value={password} onChange={(e) => setPassword(e.target.value)} />
          <span className="text-xs text-ink-500">{PASSWORD_MIN}자 이상으로 정해 주세요.</span>
        </label>
        <label className="grid gap-1 text-sm">
          <span className="font-medium">표시 이름</span>
          <input className="field" type="text" autoComplete="nickname" required maxLength={20} value={name} onChange={(e) => setName(e.target.value)} />
        </label>

        <fieldset className="grid gap-2 rounded-lg bg-surface-2 p-3 text-sm">
          <legend className="px-1 text-xs font-medium text-ink-500">선택 항목</legend>
          <p className="text-xs text-ink-500">연령대·성별·직업은 추천과 이벤트 분석에 써요. 고르지 않아도 가입할 수 있어요.</p>
          <label className="flex items-center gap-2">
            <span className="w-14 font-medium">연령대</span>
            <select className="field w-auto flex-1 py-1.5" value={ageBand} onChange={(e) => setAgeBand(e.target.value as AgeBand | "")}>
              <option value="">선택 안 함</option>
              {AGE_BANDS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="w-14 font-medium">성별</span>
            <select className="field w-auto flex-1 py-1.5" value={gender} onChange={(e) => setGender(e.target.value as Gender | "")}>
              <option value="">선택 안 함</option>
              {GENDERS.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-2">
            <span className="w-14 font-medium">직업</span>
            <select className="field w-auto flex-1 py-1.5" value={occupation} onChange={(e) => setOccupation(e.target.value as Occupation | "")}>
              <option value="">선택 안 함</option>
              {OCCUPATIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
        </fieldset>

        <div className="grid gap-1.5 text-sm">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={terms} onChange={(e) => setTerms(e.target.checked)} />
            <span>(필수) <Link href="/terms" target="_blank" className="font-semibold text-brand-600 underline">이용약관</Link>에 동의해요</span>
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={privacy} onChange={(e) => setPrivacy(e.target.checked)} />
            <span>(필수) <Link href="/privacy" target="_blank" className="font-semibold text-brand-600 underline">개인정보처리방침</Link>에 동의해요</span>
          </label>
        </div>

        <button className="btn-primary" disabled={busy}>{busy ? "가입 중…" : "가입하기"}</button>
      </form>
      <p className="mt-4 text-center text-sm text-ink-500">
        이미 계정이 있나요? <Link href="/login" className="font-semibold text-brand-600 underline">로그인</Link>
      </p>
    </div>
  );
}
