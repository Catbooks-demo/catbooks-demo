import { useState, type CSSProperties } from "react";

const GLYPH: Record<string, string> = { book: "", foreign_book: "EN", ebook: "e-book", stationery: "文", goods: "GOODS" };

/** 사진 확장자. 앞에서부터 차례로 시도하고, 모두 없으면 제목 카드로 돌아간다. */
const PHOTO_EXTS = ["jpg", "png", "webp", "jpeg"];
/** 상품 사진 주소. `shop-web/public/covers/<product_id>.<확장자>`에 파일을 두면 쓰인다. */
const photoOf = (productId: string, ext: string) => `${import.meta.env.BASE_URL}covers/${productId}.${ext}`;

/** 표지 주소는 http·https만 쓴다(서버도 확인하지만 한 번 더 막는다). */
const isWebUrl = (url?: string | null): url is string => !!url && /^https?:\/\/[^\s"'<>\\`]+$/i.test(url);

/**
 * Cover: 순서대로 시도한다.
 * 1. `coverUrl`(도서관 정보나루가 준 표지 주소). 제공처 조건상 원래 주소로 **보여 주기만** 한다
 *    (서버가 받거나 저장·프록시하지 않는다). 리퍼러는 보내지 않는다.
 * 2. 사진(`public/covers/<product_id>.jpg|png|webp`).
 * 3. 모두 없거나 불러오지 못하면 상품마다 색으로 만든 제목 카드.
 */
export function Cover({ title, hue, category, productId, coverUrl, className = "", compact = false }: {
  title: string; hue: number; category: string; productId?: string; coverUrl?: string | null; className?: string; compact?: boolean;
}) {
  const sources = [
    ...(isWebUrl(coverUrl) ? [{ src: coverUrl, remote: true }] : []),
    ...(productId ? PHOTO_EXTS.map((ext) => ({ src: photoOf(productId, ext), remote: false })) : []),
  ];
  const [failed, setFailed] = useState<{ key: string; n: number }>({ key: "", n: 0 });
  const key = `${productId ?? ""}|${coverUrl ?? ""}`;
  const idx = failed.key === key ? failed.n : 0; // 다른 상품·주소면 처음부터 다시 시도한다
  const square = category === "stationery" || category === "goods";
  const style: CSSProperties = {
    background: `linear-gradient(155deg, hsl(${hue} 46% 34%), hsl(${(hue + 28) % 360} 52% 20%))`,
    aspectRatio: square ? "1 / 1" : "3 / 4",
  };
  const tag = GLYPH[category];
  if (idx < sources.length) {
    const { src, remote } = sources[idx];
    return (
      <div className={`relative overflow-hidden rounded-md shadow-[0_8px_18px_-10px_rgba(0,0,0,0.5)] ${className}`} style={{ aspectRatio: style.aspectRatio }}>
        <img key={src} src={src} alt={`${title} 표지`} loading="lazy" decoding="async"
          referrerPolicy={remote ? "no-referrer" : undefined}
          onError={() => setFailed({ key, n: idx + 1 })} className="h-full w-full object-cover" />
      </div>
    );
  }
  return (
    <div className={`relative flex overflow-hidden rounded-md text-white shadow-[0_8px_18px_-10px_rgba(0,0,0,0.5)] ${className}`} style={style} role="img" aria-label={`${title} 표지`}>
      {!square && <span className="absolute inset-y-0 left-0 w-[7%] bg-black/25" />}
      <div className={`relative flex w-full flex-col justify-between ${compact ? "p-2" : "p-3.5"} ${square ? "" : "pl-[11%]"}`}>
        <span className={`self-start rounded-full bg-white/20 px-1.5 py-0.5 font-semibold tracking-wider ${compact ? "text-[8px]" : "text-[10px]"} ${tag ? "" : "invisible"}`}>{tag || "·"}</span>
        <p className={`line-clamp-4 break-keep font-bold leading-snug ${compact ? "text-[10px]" : "text-[15px]"}`}>{title}</p>
        <svg viewBox="0 0 24 16" className={`${compact ? "h-3" : "h-5"} self-end opacity-70`} aria-hidden="true">
          <path d="M3 15V3l5 4h8l5-4v12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
        </svg>
      </div>
    </div>
  );
}
