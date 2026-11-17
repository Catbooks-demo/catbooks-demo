import { SHOP_API_URL } from "./env";
import type { AgeBand, Banner, CartPrice, Category, ClaimInput, ClaimReceipt, DemoUser, IdeaInput, IdeaReceipt, Inquiry, Me, MeUpdate, MyInquiry, MyInquiryDetail, InquiryFeedbackInput, InquiryFeedbackReceipt, MyReview, Reviewable, ReviewPage, ReviewsData, SignupInput, InquiryInput, MyClaim, MyIdea, Order, OrderLineInput, PaymentConfig, Product, ProductPage, ProductQuery, Genre, BestProduct, Seller, TossPrepared } from "./types";

// 판매 사이트 → shop-api(`/api/shop/*`)의 모든 호출은 이 파일 한 곳에 둔다.
// 성공은 `{data, meta}`, 실패는 `{error: {code, message}}`. 구매자는 이메일로 가입·로그인하고 로그인 토큰을 `Authorization: Bearer`로 보낸다.
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

// ---------------------------------------------------------------- 로그인 토큰
// 로그인 토큰은 localStorage(`cb.session`)에 둔다. 비밀번호는 저장하지 않는다.
const TOKEN_KEY = "cb.session";
export function storedToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // storage blocked
  }
}
export function storeToken(t: string | null) {
  try {
    if (t) localStorage.setItem(TOKEN_KEY, t);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 저장 못 하면 이 탭에서만 유지 */
  }
}

// 서버가 토큰을 무효라고 하면 api.ts가 토큰을 지운다. 로그인 상태(AuthProvider)도 따라 비우도록 알린다.
const sessionListeners = new Set<() => void>();
export function onSessionCleared(fn: () => void): () => void {
  sessionListeners.add(fn);
  return () => { sessionListeners.delete(fn); };
}

// ---------------------------------------------------------------- 호출
interface Body<T> { data?: T; meta?: Record<string, unknown>; error?: { code?: string; message?: string } }

async function requestFull<T>(path: string, init?: RequestInit): Promise<{ data: T; meta: Record<string, unknown> }> {
  let res: Response;
  const token = storedToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new ApiError(`판매 서버(shop-api, ${BASE})에 연결할 수 없습니다. 서버가 실행 중인지 확인하세요.`, 0);
  }
  const body = (await res.json().catch(() => null)) as Body<T> | null;
  if (!res.ok || !body || body.data === undefined) {
    const code = body?.error?.code;
    if (res.status === 401 && (code === "UNAUTHORIZED" || code === "LOGIN_REQUIRED")) { storeToken(null); sessionListeners.forEach((fn) => fn()); }
    throw new ApiError(body?.error?.message ?? `요청 실패 (${res.status})`, res.status, body?.error?.code);
  }
  return { data: body.data, meta: body.meta ?? {} };
}

/** 가입·로그인·비밀번호 확인 오류 문구. 시도 제한(429)만 화면 문구로 바꾸고 나머지는 서버 문구 그대로다. */
export function authErrorMessage(err: unknown): string {
  const e = err as ApiError;
  if (e?.code === "RATE_LIMITED" || e?.status === 429) return "시도가 너무 많아요. 잠시 후 다시 시도해 주세요.";
  return (err as Error).message;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  return (await requestFull<T>(path, init)).data;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });

const notReady = <T>(feature: string): Promise<T> => Promise.reject(new NotReadyError(feature));

export const api = {
  // --- shop-api에 있는 것 -------------------------------------------------
  products: (p: { category_code?: string } = {}) =>
    request<Product[]>(`/api/shop/products${p.category_code ? `?category_code=${encodeURIComponent(p.category_code)}` : ""}`),
  /** 서버 검색·정렬·페이지. `page`를 보내므로 `{data, meta}` 모양으로 온다. */
  productsPage: async (p: ProductQuery): Promise<ProductPage> => {
    const sp = new URLSearchParams();
    if (p.q) sp.set("q", p.q);
    if (p.genre) sp.set("genre", p.genre);
    if (p.category) sp.set("category_code", p.category);
    if (p.sort) sp.set("sort", p.sort);
    sp.set("page", String(p.page ?? 1));
    sp.set("size", String(p.size ?? 24));
    const r = await requestFull<Product[]>(`/api/shop/products?${sp.toString()}`);
    return { items: r.data, total: Number(r.meta.total ?? r.data.length), page: Number(r.meta.page ?? 1), size: Number(r.meta.size ?? 24) };
  },
  genres: () => request<Genre[]>("/api/shop/genres"),
  best: (p: { genre?: string; limit?: number; days?: number } = {}) => {
    const sp = new URLSearchParams();
    if (p.genre) sp.set("genre", p.genre);
    if (p.days) sp.set("days", String(p.days));
    sp.set("limit", String(p.limit ?? 10));
    return request<BestProduct[]>(`/api/shop/products/best?${sp.toString()}`);
  },
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

  /** 내 1:1 문의 내역(본인 것만). 접수 직후에는 `preview`만 있고 고객지원이 처리하면 상태가 바뀐다. */
  myInquiries: () => request<MyInquiry[]>("/api/shop/inquiries"),
  /** 내 문의 하나와 메시지. 404 NOT_FOUND. */
  myInquiry: (id: number) => request<MyInquiryDetail>(`/api/shop/inquiries/${id}`),
  /** 해결된 문의 평가(한 번만). 409 INVALID_STATE, 429 RATE_LIMITED. */
  rateInquiry: (id: number, b: InquiryFeedbackInput) => post<InquiryFeedbackReceipt>(`/api/shop/inquiries/${id}/feedback`, b),
  /** 내가 쓴 후기. */
  myReviews: () => request<MyReview[]>("/api/shop/me/reviews"),
  /** 후기를 쓸 수 있는 주문 품목(배송 완료 + 아직 안 쓴 것). */
  reviewable: () => request<Reviewable[]>("/api/shop/me/reviewable"),

  /** 상품 공개 후기와 요약(토큰 없음). 구매자 이름 없이 `author_label`만 온다. */
  reviews: async (productId: string, page = 1, size = 10): Promise<ReviewPage> => {
    const r = await requestFull<ReviewsData>(`/api/shop/products/${encodeURIComponent(productId)}/reviews?page=${page}&size=${size}`);
    return { ...r.data, total: Number(r.meta.total ?? r.data.items.length), page: Number(r.meta.page ?? page), size: Number(r.meta.size ?? size) };
  },
  /** 배송이 끝난 내 주문 상품에 후기를 쓴다. 403 NOT_REVIEWABLE, 409 REVIEW_EXISTS. */
  writeReview: (productId: string, b: { order_id: string; rating: number; body: string }) =>
    post<MyReview>(`/api/shop/products/${encodeURIComponent(productId)}/reviews`, b),

  /** 1:1 문의 접수(토큰 없음). 주문이 필수이며 shop-api가 고객지원으로 넘긴다. 같은 내용을 10분 안에 다시 보내면 같은 접수로 처리된다. */
  submitInquiry: (b: InquiryInput) => post<Inquiry>("/api/shop/inquiries", b),

  /** 고객의 소리(아이디어) 접수. 문의·불만과 따로 받고 하루 5건까지다. 환불·취소류는 처리 결과가 "문의로 안내"로 돌아온다. */
  submitIdea: (b: IdeaInput) => post<IdeaReceipt>("/api/shop/ideas", b),
  /** 내 아이디어 내역: 번호·종류·앞부분 30자·처리 상태·안내 한 줄. 판매 서버에 저장된 것을 읽는다(원문은 없다). */
  myIdeas: (buyerId: string) => request<MyIdea[]>(`/api/shop/ideas?buyer_id=${encodeURIComponent(buyerId)}`),

  /** 시험용 구매자 목록(`SHOP_DEMO_LOGIN=on`일 때만, 토큰 없이). 계정 없는 옛 데모 구매자의 가명 ID + 표시용 이름만. */
  demoUsers: () => request<DemoUser[]>("/api/shop/auth/demo-buyers"),
  demoUser: (buyerId: string) => request<DemoUser>(`/api/shop/users/${encodeURIComponent(buyerId)}`),
  /** 시험용 구매자 로그인(`SHOP_DEMO_LOGIN=on`일 때만). 옛 데모 구매자에게 비밀번호 없이 토큰을 준다. */
  demoLogin: (buyerId: string) => post<{ token: string; user: Me }>("/api/shop/auth/demo-login", { buyer_id: buyerId }),
  authConfig: () => request<{ demo_login: boolean }>("/api/shop/auth/config"),
  signup: (b: SignupInput) => post<{ token: string; user: Me }>("/api/shop/auth/signup", b),
  login: (email: string, password: string) => post<{ token: string; user: Me }>("/api/shop/auth/login", { email, password }),
  /** 이 토큰을 무효로 한다. 이미 무효여도 성공이다. */
  logout: () => post<Record<string, never>>("/api/shop/auth/logout"),
  me: () => request<Me>("/api/shop/me"),
  /** 보낸 칸만 바뀐다. 연령대·성별·직업은 null이면 지운다. */
  updateMe: (b: MeUpdate) => patch<Me>("/api/shop/me", b),
  /** 바꾸면 이 기기를 뺀 다른 로그인은 모두 끊긴다. */
  changePassword: (current: string, next: string) => post<{ changed: boolean }>("/api/shop/me/password", { current, new: next }),
  /** 회원 탈퇴. 비밀번호가 틀리면 403 INVALID_PASSWORD. 회원정보·후기·로그인은 바로 지워지고 주문·클레임 기록은 남는다. */
  withdraw: (password: string) => post<{ withdrawn: boolean }>("/api/shop/me/withdraw", { password }),
  /** 연령대 구간을 정한다(null이면 지운다). 비밀번호·토큰은 없다(데모). */
  setAgeBand: (buyerId: string, ageBand: AgeBand | null) =>
    patch<DemoUser>(`/api/shop/users/${encodeURIComponent(buyerId)}`, { age_band: ageBand }),
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
};
