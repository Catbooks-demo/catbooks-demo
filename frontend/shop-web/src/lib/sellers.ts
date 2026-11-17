import { useEffect, useState } from "react";
import { api } from "./api";
import type { Seller } from "./types";

// 판매자 목록은 거의 바뀌지 않아 한 번 받아 모든 화면이 나눠 쓴다. 실패하면 다음 화면에서 다시 시도한다.
let cache: Promise<Seller[]> | null = null;
function load(): Promise<Seller[]> {
  if (!cache) cache = api.sellers().catch((e) => { cache = null; throw e; });
  return cache;
}

/** shop-api `/api/shop/sellers`. 아직 못 받았거나 실패했거나 목록에 없는 판매자 ID는 번호를 그대로 보여 준다. */
export function useSellers() {
  const [names, setNames] = useState<Map<string, string>>(() => new Map());
  useEffect(() => {
    let live = true;
    load()
      .then((list) => live && setNames(new Map(list.filter((s) => s.name).map((s) => [s.seller_id, s.name]))))
      .catch(() => live && setNames(new Map()));
    return () => { live = false; };
  }, []);
  const label = (sellerId: string) => names.get(sellerId) ?? sellerId;
  return { label };
}
