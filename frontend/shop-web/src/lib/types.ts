// ax-backend shop-api(`/api/shop/*`) 응답 모양. 성공은 `{data, meta}`, 실패는 `{error: {code, message}}`.
// ID는 문자열이다: 상품 `prd_0001`, 주문 `ord_0001`, 주문 품목 `oit_0001`, 구매자 `buy_0001`.
// TODO: shop-api가 OpenAPI 타입을 열면 `npm run gen:types`의 `@ax/types/shop-api`로 바꾼다.

export interface Product {
  product_id: string; seller_id: string; name: string; category_code: string; price: number; stock: number; active: boolean;
  isbn13?: string | null; author?: string | null; publisher?: string | null; genre_code?: string | null; description?: string | null;
}
export interface BestProduct extends Product { sold_qty: number }
/** `GET /api/shop/genres` */
export interface Genre { genre_code: string; name: string; product_count: number }
export type ProductSort = "popular" | "new" | "price_asc" | "price_desc";
export interface ProductQuery { q?: string; category?: string; genre?: string; sort?: ProductSort; page?: number; size?: number }
export interface ProductPage { items: Product[]; total: number; page: number; size: number }

/** `GET /api/shop/sellers`. `name`은 판매자 표시 이름. */
export interface Seller { seller_id: string; name: string; category_codes: string[] }

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

/** `GET /api/shop/inquiries`. `status`: open(답변 대기)·resolved(해결) 등. 고객지원이 처리하기 전에는 `preview`가 비거나 안내문이다. */
export interface MyInquiry { id: number; subject: string; status: string; order_id: string; updated_at: string; rated?: boolean; can_rate?: boolean; preview?: string; notice?: string; intake_failed?: boolean }
export interface MyReview { review_id: number | string; product_id: string; order_id: string; rating: number; body: string; created_at: string }
export interface Reviewable { order_id: string; product_id: string; title: string | null }

// ---------------------------------------------------------------- ideas (고객의 소리)
export type IdeaKind = "event" | "feature" | "improvement" | "product" | "other";
export interface IdeaInput { buyer_id: string; kind: IdeaKind; text: string }
export interface IdeaReceipt { idea_id: number; received: boolean }
/** pending: 고객지원이 아직 안 읽음 · received: 받음 · redirected: 문의로 남겨야 함 · not_accepted: 받지 않음 */
export type IdeaStatus = "pending" | "received" | "redirected" | "not_accepted";
export interface MyIdea { idea_id: number; kind: IdeaKind; preview: string; status: IdeaStatus; notice: string | null; created_at: string }

export interface OrderLineInput { product_id: string; qty: number }

// ---------------------------------------------------------------- claims
export type ClaimType = "cancel" | "return" | "exchange";
export type ClaimReason = "change_of_mind" | "defect" | "wrong_item" | "damaged" | "missing_item" | "late_delivery" | "other";
// received: 판매 서버에 접수만 됨(고객지원이 아직 읽지 않았거나 판정 전), not_accepted: 클레임으로 받지 않음
export type ClaimStatus = "received" | "needs_info" | "needs_review" | "awaiting_return" | "return_received" | "approved" | "completed" | "rejected" | "not_accepted";

/** 신청 직후 돌려받는 접수증. 판정·환불액은 없다(AXcellent가 읽어 간 뒤 결과가 적힌다). */
export interface ClaimReceipt {
  // context_id는 접수 응답의 meta에 있어 data에는 없을 수 있다
  id: number; context_id?: string; status: ClaimStatus; order_id: string; type: ClaimType; reason: ClaimReason; created_at: string;
}
/** 내 신청: 접수증에 AXcellent가 적어 준 결과(없으면 null)가 붙는다. */
export interface MyClaim extends ClaimReceipt {
  updated_at: string; result: (Partial<Claim> & { message?: string }) | null;
}

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
/** 구매자 연령대 구간. 생년월일·나이는 받지 않고 구간만 저장한다. 모르면 null. */
export type AgeBand = "10s" | "20s" | "30s" | "40s" | "50s_plus";
export const AGE_BANDS: { value: AgeBand; label: string }[] = [
  { value: "10s", label: "10대" },
  { value: "20s", label: "20대" },
  { value: "30s", label: "30대" },
  { value: "40s", label: "40대" },
  { value: "50s_plus", label: "50대 이상" },
];
export type Gender = "female" | "male" | "no_answer";
export const GENDERS: { value: Gender; label: string }[] = [
  { value: "female", label: "여성" },
  { value: "male", label: "남성" },
  { value: "no_answer", label: "응답 안 함" },
];
export type Occupation = "student" | "employee" | "self_employed" | "other" | "no_answer";
export const OCCUPATIONS: { value: Occupation; label: string }[] = [
  { value: "student", label: "학생" },
  { value: "employee", label: "직장인" },
  { value: "self_employed", label: "자영업" },
  { value: "other", label: "기타" },
  { value: "no_answer", label: "응답 안 함" },
];
/** 로그인한 구매자 본인(`GET /api/shop/me`). 프로필 칸은 고르지 않았으면 null(모름)이다. */
export interface Me {
  buyer_id: string; display_name: string; email: string | null;
  age_band: AgeBand | null; gender: Gender | null; occupation: Occupation | null;
}
export interface SignupInput {
  email: string; password: string; display_name: string; agree_terms: true; agree_privacy: true;
  age_band?: AgeBand; gender?: Gender; occupation?: Occupation;
}
export type MeUpdate = Partial<Pick<Me, "display_name" | "age_band" | "gender" | "occupation">>;
/** 목록(`GET /api/shop/users`)에는 `age_band`가 없고, 한 명 조회·선택(`sessions`) 응답에만 있다. */
export interface DemoUser { buyer_id: string; display_name: string; age_band?: AgeBand | null }
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
