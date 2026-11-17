import type { Order, Payment } from "./types";

// 결제 실패 코드별 안내. shop-api가 돌려주는 failure_code(PG_UNAVAILABLE·PG_DECLINED)만 갈라서 안내하고,
// 모르는 코드는 원인을 추측하지 않고 일반 안내만 한다.
export interface FailureGuide { title: string; hint: string }

export function failureGuide(code: string | null | undefined): FailureGuide {
  switch (code) {
    case "PG_UNAVAILABLE":
      return { title: "결제 서버에 문제가 있어 결제하지 못했습니다.", hint: "잠시 후 같은 주문으로 다시 시도해 주세요. 결제 금액은 빠져나가지 않았습니다." };
    case "PG_DECLINED":
      return { title: "결제가 거절되었습니다.", hint: "결제 수단을 확인하시거나 다른 결제 수단으로 다시 시도해 주세요." };
    default:
      return { title: "결제하지 못했습니다.", hint: "잠시 후 다시 시도해 주세요. 계속되면 문의해 주세요." };
  }
}

/** 가장 최근 실패한 결제(없으면 null). created_at이 같으면 목록의 뒤쪽을 최신으로 본다. */
export function lastFailedPayment(o: Pick<Order, "payments">): Payment | null {
  const failed = o.payments.filter((p) => p.status === "failed");
  if (!failed.length) return null;
  return failed.reduce((a, b) => (b.created_at >= a.created_at ? b : a));
}

/** 결제 실패 화면에서 문의 화면으로 가는 경로. 주문 번호만 넘긴다(개인정보 아님). */
export const supportHref = (orderId: string) => `/support?order_id=${encodeURIComponent(orderId)}`;
