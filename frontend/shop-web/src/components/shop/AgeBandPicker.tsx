import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AGE_BANDS, type AgeBand } from "@/lib/types";

/**
 * 선택한 구매자의 연령대 구간. 고르지 않아도 된다(비워 두면 "연령 모름"으로 센다).
 * 생년월일·나이는 받지 않고 구간만 판매 서버에 저장한다(`PATCH /api/shop/users/{buyer_id}`).
 */
export default function AgeBandPicker({ buyerId }: { buyerId: string }) {
  const [value, setValue] = useState<AgeBand | "" | null>(null); // null = 아직 불러오는 중
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setValue(null);
    setNote(null);
    setError(null);
    api.demoUser(buyerId)
      .then((u) => setValue(u.age_band ?? ""))
      .catch((e: Error) => { setValue(""); setError(e.message); });
  }, [buyerId]);

  async function change(next: AgeBand | "") {
    const previous = value;
    setValue(next);
    setSaving(true);
    setNote(null);
    setError(null);
    try {
      const saved = await api.setAgeBand(buyerId, next === "" ? null : next);
      setValue(saved.age_band ?? "");
      setNote("저장했어요");
    } catch (e) {
      setValue(previous);
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-xl border border-ink-100 bg-surface p-3 text-sm">
      <label className="flex flex-wrap items-center gap-2">
        <span className="font-medium">연령대</span>
        <span className="text-xs text-ink-500">(선택)</span>
        <select
          className="field w-auto py-1.5"
          value={value ?? ""}
          disabled={value === null || saving}
          onChange={(e) => change(e.target.value as AgeBand | "")}
        >
          <option value="">선택 안 함</option>
          {AGE_BANDS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
        </select>
        {saving && <span className="text-xs text-ink-500">저장 중…</span>}
        {note && !saving && <span className="text-xs text-ink-500">{note}</span>}
      </label>
      <p className="mt-1 text-xs text-ink-500">생년월일은 받지 않고 구간만 저장해요. 연령대별 추천과 이벤트에 써요.</p>
      {error && <p className="mt-1 text-xs text-red-800" role="alert">{error}</p>}
    </div>
  );
}
