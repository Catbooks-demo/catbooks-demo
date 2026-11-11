import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { shortDateTime } from "@/lib/format";
import { CLAIM_REASON_KO, CLAIM_TYPE_KO } from "@/lib/labels";
import type { MyClaim } from "@/lib/types";
import { AxClaimStatusBadge, AxClaimSummary } from "./AxClaimSummary";

// 신청은 판매 서버가 저장하고 AXcellent 고객지원이 읽어 가 판정한 뒤 결과를 적는다("접수됨 → 나중에 처리").
// 그래서 접수 직후에는 판정·환불액이 없다. 결과가 오기 전에는 값을 지어내지 않고 "접수됨"만 보여 준다.
const REFRESH_MS = 8_000;
// 고객지원이 읽어 가기를 기다리는 동안(접수됨)과 목록을 못 불러왔을 때만 다시 읽는다. 사람 확인·반품 배송처럼
// 며칠 걸리는 상태는 주기로 읽지 않고, 탭이 다시 보일 때와 고객이 신청을 보낸 직후에 읽는다.
const MAX_POLLS = 15;

/** 내 신청 목록(주문별로 거를 수 있다). 마지막 요청의 응답만 반영하고, 대상이 바뀌면 옛 목록을 비운다. */
export function useMyClaims(buyerId: string | undefined, orderId?: string) {
  const [claims, setClaims] = useState<MyClaim[]>([]);
  const [failed, setFailed] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const latest = useRef(0);
  const polls = useRef(0);

  const reload = useCallback(async () => {
    if (!buyerId) return;
    const mine = ++latest.current;
    try {
      const list = await api.myClaims(buyerId, orderId);
      if (mine !== latest.current) return; // 더 나중 요청이 있으면 이 응답은 버린다
      setClaims(list);
      setFailed(false);
    } catch {
      if (mine === latest.current) setFailed(true);
    }
  }, [buyerId, orderId]);

  // 구매자·주문이 바뀌면 이전 대상의 목록과 늦게 온 응답을 버리고 새로 읽는다
  useEffect(() => {
    latest.current += 1;
    polls.current = 0;
    setClaims([]);
    setFailed(false);
    void reload();
  }, [reload]);

  const waiting = failed || claims.some((c) => c.status === "received");
  useEffect(() => {
    if (!waiting) { polls.current = 0; return; }
    const t = setInterval(() => {
      if (document.hidden) return; // 보이지 않는 탭에서는 읽지 않는다
      polls.current += 1;
      if (polls.current > MAX_POLLS) { clearInterval(t); return; }
      void reload();
    }, REFRESH_MS);
    return () => clearInterval(t);
  }, [waiting, reload, epoch]);

  // 탭이 다시 보이면 한 번 읽는다(며칠 걸리는 상태의 변화를 따라간다)
  useEffect(() => {
    const onVisible = () => { if (!document.hidden) void reload(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [reload]);

  /** 새 신청을 보낸 직후나 고객이 다시 불러올 때: 횟수를 처음부터 센다 */
  const refresh = useCallback(() => {
    polls.current = 0;
    setEpoch((n) => n + 1);
    return reload();
  }, [reload]);

  return { claims, failed, reload: refresh };
}

/** 신청 내역을 못 불러왔을 때의 고객용 안내(서버 주소 같은 개발자용 문구는 보이지 않는다). */
export function ClaimsLoadError({ failed, onRetry }: { failed: boolean; onRetry: () => void }) {
  if (!failed) return null;
  return (
    <p role="status" className="mt-3 rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900">
      신청 내역을 불러오지 못했습니다. 잠시 후 자동으로 다시 시도합니다.{" "}
      <button type="button" className="font-semibold underline" onClick={onRetry}>지금 다시 불러오기</button>
    </p>
  );
}

export function MyClaimCard({ c }: { c: MyClaim }) {
  const rejected = c.status === "not_accepted";
  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <AxClaimStatusBadge status={c.status} />
        <span className="text-sm font-semibold">{CLAIM_TYPE_KO[c.type]} · {CLAIM_REASON_KO[c.reason]}</span>
        <span className="text-xs text-ink-500">#{c.id} · {shortDateTime(c.created_at)}</span>
      </div>
      {c.result && !rejected && <AxClaimSummary c={{ ...c.result, status: c.status }} />}
      {rejected && (
        <p role="status" className="rounded-xl bg-amber-50 px-4 py-3 text-sm text-amber-900">
          신청을 접수하지 못했습니다{c.result && "message" in c.result && c.result.message ? `: ${String(c.result.message)}` : ""}. 궁금한 점은 고객센터로 문의해 주세요.
        </p>
      )}
      {/* 최종 상태(접수 불가 등)에서는 "결과가 나타납니다" 안내를 내지 않는다. 고객지원이 읽어 가기 전에만 보인다 */}
      {!c.result && c.status === "received" && (
        <p role="status" className="rounded-xl bg-surface-2 px-4 py-3 text-sm text-ink-700">
          신청이 접수되었습니다. 고객지원이 확인하는 대로 이 화면에 결과가 나타납니다 (잠시 걸릴 수 있어요).
        </p>
      )}
    </div>
  );
}
