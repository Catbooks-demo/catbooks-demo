// ax-backend shop-api(`/api/shop/*`) 응답 모양. 성공은 `{data, meta}`, 실패는 `{error: {code, message}}`.
// ID는 문자열이다: 상품 `prd_0001`, 주문 `ord_0001`, 주문 품목 `oit_0001`, 구매자 `buy_0001`.
// TODO: shop-api가 OpenAPI 타입을 열면 `npm run gen:types`의 `@ax/types/shop-api`로 바꾼다.

export interface Product {
  product_id: string; seller_id: string; name: string; category_code: string; price: number; stock: number; active: boolean;
}

export interface OrderItem {
  order_item_id: string; product_id: string; seller_id: string; title: string; qty: number; unit_price: number; points_share: number; paid_amount: number;
}
export interface Payment {
  payment_id: string; amount: number; method_code: string; status: string; failure_code: string | null; created_at: string;
}
/** `GET /payment-config`. `toss_test`면 토스 결제위젯, `fake`면 데모 결제. */
export interface PaymentConfig { provider: "fake" | "toss_test" | string }
/** `POST /orders/{id}/payments/toss/prepare`. `toss_order_id` = `{order_id}-a{시도 번호}` */
export interface TossPrepared { toss_order_id: string; amount: number; order_name: string }
export type OrderStatus = "pending_payment" | "payment_failed" | "paid" | "cancelled";
export interface Order {
  order_id: string; buyer_id: string; status: OrderStatus | string; total_amount: number; refunded_amount: number; shipping_fee: number; points_used: number;
  promised_delivery_at: string | null; shipped_at: string | null; delivered_at: string | null; created_at: string;
  items: OrderItem[]; payments: Payment[];
}

// ---------------------------------------------------------------- inquiries
export interface InquiryInput { buyer_id: string; order_id: string; subject?: string; body: string }
export interface Inquiry { id: number | string; status: string; order_id: string; updated_at: string }

export interface OrderLineInput { product_id: string; qty: number }

// ---------------------------------------------------------------- claims
export type ClaimType = "cancel" | "return" | "exchange";
export type ClaimReason = "change_of_mind" | "defect" | "wrong_item" | "damaged" | "missing_item" | "late_delivery" | "other";
export type ClaimStatus = "needs_info" | "needs_review" | "awaiting_return" | "return_received" | "approved" | "completed" | "rejected";

export interface ClaimInput {
  buyer_id: string; customer_name: string; order_id: string; type: ClaimType; reason: ClaimReason;
  items?: { order_item_id: string; qty: number }[]; unsealed?: boolean; evidence_count?: number; description?: string;
}

export interface Claim {
  id: number; context_id: string; order_id: string; order_no: string; buyer_id: string; type: ClaimType; reason: ClaimReason; status: ClaimStatus;
  created_at: string; outcome: string; resolution: string; requires_return: boolean; evidence_count: number; unsealed: boolean;
  reasons: { code: string; level: "block" | "review" | "warn" | "info"; message: string; rule: string | null }[];
  lines: { order_item_id: string; title: string; qty: number; requested_qty: number; unit_price: number; refund_cash: number; points_back: number }[];
  shipping_refund: number; expected_refund: number; points_back: number; refund_paid: number; processed_at: string | null;
}

// ---------------------------------------------------------------- 데모 사용자·카테고리·장바구니 가격
/** 가명 구매자. 이름은 표시용 데모 이름이고 개인정보·적립금은 없다. */
export interface DemoUser { buyer_id: string; display_name: string }
/** `GET /api/shop/banners`가 주는 게시 중 배너. `text`는 서버가 준 공개 문구 그대로 보여 준다. */
export interface Banner { notice_id: string; text: string; status: string; valid_from: string | null; valid_to: string | null; context_id: string | null; created_at: string; ended_at: string | null }
/** 표시 이름(`name`)을 모르는 코드는 null이다. 지어내지 않고 코드를 그대로 보여 준다. */
export interface Category { category_code: string; name: string | null; product_count: number }
export interface CartPriceItem { product_id: string; title: string; qty: number; unit_price: number; line_amount: number }
/** 저장하지 않는 계산 결과. 금액은 모두 서버 값이고 화면에서 다시 계산하지 않는다. `coupon_discount`는 항상 null(+사유)이다. */
export interface CartPrice {
  items: CartPriceItem[]; subtotal: number; shipping_fee: number; free_shipping_from: number;
  coupon_discount: number | null; coupon_unknown_reason: string | null; points_used: number; payable_amount: number;
}
