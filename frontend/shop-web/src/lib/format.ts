export const won = (n: number) => `${n.toLocaleString("ko-KR")}원`;

const kst = (iso: string) => new Date(iso);

export function dateStr(iso: string | null | undefined) {
  if (!iso) return "–";
  return kst(iso).toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric" });
}

export function shortDateTime(iso: string | null | undefined) {
  if (!iso) return "–";
  return kst(iso).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function ago(iso: string) {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (min < 1) return "방금 전";
  if (min < 60) return `${min}분 전`;
  if (min < 60 * 24) return `${Math.round(min / 60)}시간 전`;
  return `${Math.round(min / 60 / 24)}일 전`;
}

export const pct = (v: number | null | undefined, digits = 0) => (v == null ? "–" : `${(v * 100).toFixed(digits)}%`);
export const shortDate = (iso: string) => { const d = new Date(iso + "T00:00:00"); return `${d.getMonth() + 1}/${d.getDate()}`; };
export const longDate = (iso: string) => new Date(iso + "T00:00:00").toLocaleDateString("ko-KR", { month: "long", day: "numeric", weekday: "short" });

/** 표지 색상. 상품 이미지가 없어서 ID로 정한다(같은 상품은 늘 같은 색). */
export function hueOf(id: string) {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return h;
}
