import { useEffect, useState } from "react";
import { api } from "./api";
import type { Category } from "./types";

// 카테고리는 거의 바뀌지 않아 한 번 받아 모든 화면이 나눠 쓴다. 실패하면 다음 화면에서 다시 시도한다.
let cache: Promise<Category[]> | null = null;
function load(): Promise<Category[]> {
  if (!cache) cache = api.categories().catch((e) => { cache = null; throw e; });
  return cache;
}

/** shop-api `/api/shop/categories`. 표시 이름이 null이거나 아직 못 받았으면 코드를 그대로 보여 준다. */
export function useCategories() {
  const [categories, setCategories] = useState<Category[]>([]);
  useEffect(() => {
    let live = true;
    load().then((c) => live && setCategories(c)).catch(() => live && setCategories([]));
    return () => { live = false; };
  }, []);
  const label = (code: string) => categories.find((c) => c.category_code === code)?.name ?? code;
  return { categories, label };
}
