import { useEffect, useState } from "react";
import { api } from "./api";
import type { Genre } from "./types";

// 장르 목록은 한 번 받아 모든 화면이 나눠 쓴다. 실패하면 다음 화면에서 다시 시도한다.
let cache: Promise<Genre[]> | null = null;
function load(): Promise<Genre[]> {
  if (!cache) cache = api.genres().catch((e) => { cache = null; throw e; });
  return cache;
}

/** shop-api `/api/shop/genres`. `loaded`가 false면 아직 받는 중이에요. */
export function useGenres() {
  const [genres, setGenres] = useState<Genre[]>([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let live = true;
    load().then((g) => live && (setGenres(g), setLoaded(true))).catch(() => live && setLoaded(true));
    return () => { live = false; };
  }, []);
  return { genres, loaded };
}
