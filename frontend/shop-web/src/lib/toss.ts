import { useEffect, useRef, useState } from "react";
import type { TossPaymentsWidgets, WidgetPaymentMethodWidget } from "@tosspayments/tosspayments-sdk";
import { api } from "./api";
import { TOSS_CLIENT_KEY } from "./env";
import type { TossPrepared } from "./types";

// 토스페이먼츠 결제위젯(테스트 모드). shop-api가 `toss_test`이고 클라이언트 키가 있을 때만 쓰고,
// 아니면 화면은 지금의 데모 결제(`POST /orders/{id}/payments`)를 그대로 쓴다.
// 흐름: 주문 생성 → prepare(서버 금액·toss_order_id) → requestPayment → /payments/success|fail → confirm

export type PayPath = "loading" | "toss" | "fake";

let pathPromise: Promise<PayPath> | null = null;
function loadPayPath(): Promise<PayPath> {
  if (!TOSS_CLIENT_KEY) return Promise.resolve("fake");
  pathPromise ??= api.paymentConfig()
    .then((c): PayPath => (c.provider === "toss_test" ? "toss" : "fake"))
    .catch((): PayPath => { pathPromise = null; return "fake"; }); // 다음 화면에서 다시 묻는다
  return pathPromise;
}

/** 이 화면이 토스 결제위젯을 쓸지, 데모 결제를 쓸지. 서버 설정을 읽는 동안은 `loading`. */
export function usePayPath(): PayPath {
  const [path, setPath] = useState<PayPath>(TOSS_CLIENT_KEY ? "loading" : "fake");
  useEffect(() => {
    let live = true;
    loadPayPath().then((p) => live && setPath(p));
    return () => { live = false; };
  }, []);
  return path;
}

/** 토스 주문번호(`{order_id}-a{시도 번호}`)에서 우리 주문 번호를 꺼낸다. 모양이 다르면 null. */
export function orderIdOfToss(tossOrderId: string | null | undefined): string | null {
  const m = /^(.+)-a\d+$/.exec(tossOrderId ?? "");
  return m ? m[1] : null;
}

/** 결제창을 사용자가 닫은 경우의 코드(리다이렉트는 PAY_PROCESS_CANCELED, Promise 오류는 USER_CANCEL). */
export const isUserCancel = (code: string | null | undefined) => code === "PAY_PROCESS_CANCELED" || code === "USER_CANCEL";

/** 결제창이 열리기 전, 위젯 입력이 모자라서 난 오류(카드사·약관·결제 수단 미선택 등). 실패가 아니라 다시 고르면 된다. */
const INPUT_ERRORS = new Set([
  "NEED_CARD_PAYMENT_DETAIL", "NEED_AGREEMENT_WITH_REQUIRED_TERMS", "NOT_SELECTED_PAYMENT_METHOD", "NEED_REFUND_ACCOUNT_DETAIL",
]);
export const isInputError = (code: string | null | undefined) => INPUT_ERRORS.has(code ?? "");

/** 결제위젯이 결제 요청 단계에서 낸 오류. 화면은 /payments/fail로 보낸다. */
export class TossRequestError extends Error {
  constructor(public code: string, message: string, public tossOrderId: string) {
    super(message);
  }
}

export const failHref = (e: TossRequestError) =>
  `/payments/fail?${new URLSearchParams({ code: e.code, message: e.message, orderId: e.tossOrderId })}`;

// 위젯 렌더·제거는 한 번에 하나씩(SDK가 동시에 다른 명령을 받으면 오류를 낸다. StrictMode의 두 번 마운트도 여기서 줄 세운다)
let chain: Promise<unknown> = Promise.resolve();
function enqueue<T>(fn: () => Promise<T>): Promise<T> {
  const next = chain.then(fn);
  chain = next.catch(() => undefined);
  return next;
}

export const TOSS_METHODS_ID = "toss-payment-methods";
export const TOSS_AGREEMENT_ID = "toss-agreement";

export interface TossWidget {
  status: "idle" | "loading" | "ready" | "error";
  error: string | null;
  /** 결제 전에 막을 사유(약관 미동의·결제 수단 미선택). 문제 없으면 null. */
  check(): Promise<string | null>;
  /** 서버 금액으로 맞춘 뒤 결제창을 연다. `beforeRedirect`는 결제창으로 넘어가기 직전에 한 번 부른다. */
  pay(p: TossPrepared, beforeRedirect?: () => void): Promise<void>;
}

/**
 * `#toss-payment-methods`·`#toss-agreement`에 결제위젯을 그린다(화면에 하나만).
 * `amount`가 처음 정해지면 그리고, 바뀌면 setAmount로 맞춘다.
 */
export function useTossWidget(enabled: boolean, amount: number | null): TossWidget {
  const widgets = useRef<TossPaymentsWidgets | null>(null);
  const methods = useRef<WidgetPaymentMethodWidget | null>(null);
  const amountRef = useRef<number | null>(amount);
  const agreed = useRef<boolean | null>(null); // 약관 이벤트가 오기 전에는 모른다(null)
  const [status, setStatus] = useState<TossWidget["status"]>("idle");
  const [error, setError] = useState<string | null>(null);
  const hasAmount = amount !== null;

  useEffect(() => {
    if (!enabled || !hasAmount) return;
    let live = true;
    const rendered: { destroy(): Promise<void> }[] = []; // 하나만 그려지고 실패해도 지울 수 있게 그릴 때마다 담는다
    setStatus("loading");
    setError(null);
    enqueue(async () => {
      if (!live) return;
      const { loadTossPayments, ANONYMOUS } = await import("@tosspayments/tosspayments-sdk");
      const toss = await loadTossPayments(TOSS_CLIENT_KEY);
      const w = toss.widgets({ customerKey: ANONYMOUS });
      await w.setAmount({ currency: "KRW", value: amountRef.current ?? 0 });
      const [m] = await Promise.all([
        w.renderPaymentMethods({ selector: `#${TOSS_METHODS_ID}`, variantKey: "DEFAULT" }).then((m) => { rendered.push(m); return m; }),
        w.renderAgreement({ selector: `#${TOSS_AGREEMENT_ID}`, variantKey: "AGREEMENT" }).then((a) => {
          rendered.push(a);
          a.on("agreementStatusChange", (s) => { agreed.current = s.agreedRequiredTerms; });
          return a;
        }),
      ]);
      if (!live) return; // 정리는 아래 cleanup이 줄을 서서 한다
      widgets.current = w;
      methods.current = m;
      setStatus("ready");
    }).catch((e: Error) => {
      if (!live) return;
      setStatus("error");
      setError(e?.message || "결제창을 불러오지 못했습니다.");
    });
    return () => {
      live = false;
      widgets.current = null;
      methods.current = null;
      agreed.current = null;
      enqueue(() => Promise.all(rendered.map((r) => r.destroy().catch(() => undefined))));
    };
  }, [enabled, hasAmount]);

  // 장바구니·주문 금액이 바뀌면 위젯 금액도 맞춘다
  useEffect(() => {
    amountRef.current = amount;
    const w = widgets.current;
    if (w && amount !== null) enqueue(() => w.setAmount({ currency: "KRW", value: amount })).catch(() => undefined);
  }, [amount, status]);

  async function check(): Promise<string | null> {
    const w = widgets.current;
    if (!w) return "결제창을 불러오는 중입니다. 잠시 후 다시 눌러 주세요.";
    if (agreed.current === false) return "필수 약관에 동의해 주세요.";
    try {
      await methods.current?.getSelectedPaymentMethod();
    } catch {
      return "결제 방법을 골라 주세요.";
    }
    return null;
  }

  async function pay(p: TossPrepared, beforeRedirect?: () => void): Promise<void> {
    const w = widgets.current;
    if (!w) throw new Error("결제창을 불러오는 중입니다. 잠시 후 다시 눌러 주세요.");
    if (p.amount !== amountRef.current) { // 서버 금액이 기준이다
      await w.setAmount({ currency: "KRW", value: p.amount }); // 줄을 서지 않는다(앞 작업이 멈춰도 결제는 막히지 않게)
      amountRef.current = p.amount;
    }
    beforeRedirect?.();
    try {
      await w.requestPayment({
        orderId: p.toss_order_id,
        orderName: p.order_name,
        successUrl: `${window.location.origin}/payments/success`,
        failUrl: `${window.location.origin}/payments/fail`,
      });
    } catch (e) {
      const err = e as { code?: string; message?: string };
      throw new TossRequestError(err.code ?? "UNKNOWN", err.message ?? "결제를 요청하지 못했습니다.", p.toss_order_id);
    }
  }

  return { status, error, check, pay };
}
