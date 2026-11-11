import { SHOP_API_URL } from "./env";
import type { Banner, CartPrice, Category, ClaimInput, ClaimReceipt, DemoUser, IdeaInput, IdeaReceipt, Inquiry, InquiryInput, MyClaim, MyIdea, Order, OrderLineInput, PaymentConfig, Product, Seller, TossPrepared } from "./types";

// 판매 사이트 → shop-api(`/api/shop/*`)의 모든 호출은 이 파일 한 곳에 둔다.
// 성공은 `{data, meta}`, 실패는 `{error: {code, message}}`. 로그인·토큰은 없고 구매자는 가명 ID(`buy_0001`…)다.
const BASE = SHOP_API_URL;

export class ApiError extends Error {
  constructor(message: string, public status: number, public code?: string) {
    super(message);
  }
}

/** shop-api에 아직 없는 기능. 화면은 오류 대신 "준비 중" 안내를 보여 준다. */
export class NotReadyError extends Error {
  constructor(public feature: string) {
    super(`${feature}은(는) 준비 중입니다.`);
  }
}
export const isNotReady = (e: unknown): e is NotReadyError => e instanceof NotReadyError;

// ---------------------------------------------------------------- 구매자(데모)
// 구매자 목록은 shop-api(`/api/shop/users`)에서 받는다. 선택한 ID만 sessionStorage에 둔다(비밀번호·토큰 없음).
const BUYER_KEY = "ax.buyer";
export function storedBuyer(): string | null {
  try {
    return sessionStorage.getItem(BUYER_KEY);
  } catch {
    return null; // storage blocked
  }
}
export function storeBuyer(id: string | null) {
  try {
    if (id) sessionStorage.setItem(BUYER_KEY, id);
    else sessionStorage.removeItem(BUYER_KEY);
  } catch {
    /* session-only */
  }
}

// ---------------------------------------------------------------- 호출
interface Body<T> { data?: T; meta?: Record<string, unknown>; error?: { code?: string; message?: string } }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers: { "Content-Type": "application/json" }, cache: "no-store" });
  } catch {
    throw new ApiError(`판매 서버(shop-api, ${BASE})에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.`, 0);
  }
  const body = (await res.json().catch(() => null)) as Body<T> | null;
  if (!res.ok || !body || body.data === undefined) {
    throw new ApiError(body?.error?.message ?? `요청 실패 (${res.status})`, res.status, body?.error?.code);
  }
  return body.data;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

const notReady = <T>(feature: string): Promise<T> => Promise.reject(new NotReadyError(feature));

export const api = {
  // --- shop-api에 있는 것 -------------------------------------------------
  products: (p: { category_code?: string } = {}) =>
    request<Product[]>(`/api/shop/products${p.category_code ? `?category_code=${encodeURIComponent(p.category_code)}` : ""}`),
  product: (id: string) => request<Product>(`/api/shop/products/${encodeURIComponent(id)}`),
  orders: (buyerId: string) => request<Order[]>(`/api/shop/orders?buyer_id=${encodeURIComponent(buyerId)}`),
  order: (id: string) => request<Order>(`/api/shop/orders/${encodeURIComponent(id)}`),
  /** 합계·배송비·포인트 배분은 서버가 계산한다(주문 생성 직후 `pending_payment`). */
  placeOrder: (b: { buyer_id: string; items: OrderLineInput[]; points_used?: number }) => post<Order>("/api/shop/orders", b),
  /** 가짜 결제. 실패하면 주문이 `payment_failed`로 돌아오고 다시 결제할 수 있다. */
  pay: (orderId: string, method_code: string) => post<Order>(`/api/shop/orders/${encodeURIComponent(orderId)}/payments`, { method_code }),
  /** 결제 방식(`fake`·`toss_test`). 키는 담기지 않는다. */
  paymentConfig: () => request<PaymentConfig>("/api/shop/payment-config"),
  /** 토스 테스트 결제 시도 만들기. 금액은 서버의 주문 합계(배송비 포함)이고 시도마다 `toss_order_id`가 새로 나온다. */
  tossPrepare: (orderId: string) => post<TossPrepared>(`/api/shop/orders/${encodeURIComponent(orderId)}/payments/toss/prepare`),
  /** 결제위젯 성공 리다이렉트 값으로 승인. 이미 끝난 시도는 처음 결과를 그대로 돌려준다(두 번 승인하지 않음). */
  tossConfirm: (b: { toss_order_id: string; payment_key: string; amount: number }) => post<Order>("/api/shop/payments/toss/confirm", b),
  /** 결제 전(pending_payment·payment_failed) 주문만 취소된다. 결제 후 취소·환불은 클레임으로 한다. */
  cancel: (orderId: string) => post<Order>(`/api/shop/orders/${encodeURIComponent(orderId)}/cancel`, { reason_code: "buyer" }),
  /** 고객 신청 → shop-api에 저장 → AXcellent가 읽어 가 판정. 금액은 보내지 않는다(고객지원이 주문 사실로 계산). */
  submitClaim: (b: ClaimInput) => post<ClaimReceipt>("/api/shop/claims", b),
  /** 내 신청과 AXcellent가 적어 준 결과. 판매 서버에 저장된 것을 읽는다. */
  myClaims: (buyerId: string, orderId?: string) =>
    request<MyClaim[]>(`/api/shop/claims?buyer_id=${encodeURIComponent(buyerId)}${orderId ? `&order_id=${encodeURIComponent(orderId)}` : ""}`),

  /** 1:1 문의 접수(토큰 없음). 주문이 필수이며 shop-api가 고객지원으로 넘긴다. 같은 내용을 10분 안에 다시 보내면 같은 접수로 처리된다. */
  submitInquiry: (b: InquiryInput) => post<Inquiry>("/api/shop/inquiries", b),

  /** 고객의 소리(아이디어) 접수. 문의·불만과 따로 받고 하루 5건까지다. 환불·취소류는 처리 결과가 "문의로 안내"로 돌아온다. */
  submitIdea: (b: IdeaInput) => post<IdeaReceipt>("/api/shop/ideas", b),
  /** 내 아이디어 내역: 번호·종류·앞부분 30자·처리 상태·안내 한 줄. 판매 서버에 저장된 것을 읽는다(원문은 없다). */
  myIdeas: (buyerId: string) => request<MyIdea[]>(`/api/shop/ideas?buyer_id=${encodeURIComponent(buyerId)}`),

  /** 데모 구매자 목록(가명 ID + 표시용 이름). 적립금은 없다. */
  demoUsers: () => request<DemoUser[]>("/api/shop/users"),
  demoUser: (buyerId: string) => request<DemoUser>(`/api/shop/users/${encodeURIComponent(buyerId)}`),
  /** 구매자 선택. 서버는 토큰을 내주지 않고 `{user}`만 돌려준다. */
  selectUser: (buyerId: string) => post<{ user: DemoUser }>("/api/shop/sessions", { buyer_id: buyerId }),
  /** 게시 중이고 기간 안인 공지 배너(토큰 없음). 서버는 게시 오래된 순으로 준다. */
  banners: () => request<Banner[]>("/api/shop/banners"),
  /** `name`이 null이면 코드를 그대로 보여 준다. */
  categories: () => request<Category[]>("/api/shop/categories"),
  /** 판매자 목록(`seller_id`·`name`). 상품 화면이 번호 대신 이름을 보여 줄 때 쓴다. */
  sellers: () => request<Seller[]>("/api/shop/sellers"),
  /** 저장·재고 차감 없는 금액 미리보기. 배송비·결제액은 서버 값을 그대로 쓴다. */
  cartPrice: (items: OrderLineInput[], pointsUsed = 0) => post<CartPrice>("/api/shop/cart/price", { items, points_used: pointsUsed }),

  // --- shop-api에 아직 없는 것: ax-backend#385 결과로 연결 --------------------
  // TODO(ax-backend#385): 클레임 환불액 미리보기, 신청 철회·사진 추가
  previewClaim: (_b: ClaimInput) => notReady<never>("환불액 미리보기"),
  // TODO(ax-backend#385): 문의 내역 조회(shop-api에 목록 API 없음)
  inquiryHistory: (_buyerId: string) => notReady<never>("문의 내역 조회"),
  // TODO(ax-backend#385): 후기
  reviews: (_productId: string) => notReady<never>("구매 후기"),
};
