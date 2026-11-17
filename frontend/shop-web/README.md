# shop-web

담당: 재헌 · 포트: 3000 · 연결 API: shop-api (8010)

## 1. 역할
캣북스(catbooks) 데모 서점의 판매 사이트. [`bxbae/iTshirt-cat`](https://github.com/bxbae/iTshirt-cat)의 Next.js 프론트엔드에서 Vite로 옮겨 왔고, 지금은 **우리 shop-api(`/api/shop/*`, 8010)만** 부른다(캣북스 백엔드는 쓰지 않는다).
상품 목록·상세, 장바구니, 주문·가짜 결제, 주문 내역, 취소·반품·교환 신청(고객지원이 판정)을 한다.
로그인(비밀번호)은 없다: `/login`에서 shop-api의 데모 구매자 목록(가명 ID + 표시 이름)에서 한 명을 고르면 주문 요청 본문의 `buyer_id`로 실린다(ID만 sessionStorage 보관).

| 경로 | 화면 |
| --- | --- |
| `/` | 홈 |
| `/products`, `/products/:id` | 상품 목록(카테고리·검색·가격 정렬), 상세 |
| `/cart`, `/checkout` | 장바구니(브라우저 저장), 주문·결제 |
| `/login` | 구매자 선택(가명 ID) |
| `/orders`, `/orders/:id` | 주문 내역, 주문 상세(결제·취소·클레임 신청) |
| `/payments/success`, `/payments/fail` | 토스 결제위젯 리다이렉트: 승인 결과, 실패·취소 안내 |
| `/support` | 고객센터(1:1 문의는 준비 중) |

## 2. 실행 방법
shop-api를 먼저 띄운다(ax-backend 레포):

```bash
docker compose -f infra/compose.yml up -d shop-api   # http://localhost:8010
```

ax-frontend 루트에서:

```bash
npm ci
npm run dev:shop               # http://localhost:3000
npm run build -w shop-web
```

API 주소는 루트 `.env`의 `VITE_SHOP_API_URL`(기본 `http://localhost:8010`)로 정한다. shop-api가 꺼져 있으면 홈에 "판매 서버(shop-api, 주소)에 연결할 수 없습니다"가 보인다.
브라우저가 직접 부르므로 shop-api의 CORS(`SHOP_CORS_ORIGINS`, 기본 `localhost:3000`)에 이 주소가 들어 있어야 한다.

## 3. API
호출 경로는 모두 `src/lib/api.ts` 한 곳에 있다. 응답은 `{data, meta}` / `{error: {code, message}}`이고, 모델은 `src/lib/types.ts`에 있다(ID는 `prd_`·`ord_`·`oit_`·`buy_` 문자열).
**임시:** shop-api가 OpenAPI 타입을 열고 `npm run gen:types`로 타입이 생기면 `src/lib/types.ts`를 지우고 `@ax/types/shop-api`로 바꾼다.

| 메서드 | 경로 | 화면 |
| --- | --- | --- |
| GET | `/api/shop/products?category_code`, `/api/shop/products/{id}` | 상품 목록·상세 |
| GET | `/api/shop/orders?buyer_id`, `/api/shop/orders/{id}` | 주문 내역·상세 |
| GET | `/api/shop/users`, `/api/shop/users/{id}` | 데모 구매자 목록·조회(가명 ID + 표시 이름, 적립금 없음) |
| POST | `/api/shop/sessions` | 구매자 선택 확인(토큰 없음) |
| GET | `/api/shop/banners` | 화면 맨 위 공지 배너(게시 중·기간 안만, 토큰 없음). 30초마다 다시 읽고, 0건·조회 실패면 아무것도 그리지 않는다. 문구는 텍스트로만 그린다 |
| GET | `/api/shop/categories` | 카테고리 목록(표시 이름이 null이면 코드를 그대로 표시) |
| POST | `/api/shop/cart/price` | 장바구니·결제 전 금액 미리보기(배송비·결제액은 서버 값, 쿠폰 할인은 null이면 "확인 필요") |
| POST | `/api/shop/orders` | 주문 생성(`pending_payment`, 합계·배송비는 서버 계산) |
| POST | `/api/shop/orders/{id}/payments` | 가짜 결제 |
| GET | `/api/shop/payment-config` | 결제 방식(`fake`·`toss_test`). 화면이 토스 결제위젯을 쓸지 정한다 |
| POST | `/api/shop/orders/{id}/payments/toss/prepare` | 토스 테스트 결제 시도(`toss_order_id`·서버 금액·주문명) |
| POST | `/api/shop/payments/toss/confirm` | 결제위젯 성공 리다이렉트 값으로 승인(다시 불러도 처음 결과, 두 번 승인 안 됨) |
| POST | `/api/shop/orders/{id}/cancel` | 결제 전 주문 취소 |
| POST | `/api/shop/claims` | 취소·반품·교환 신청(고객지원으로 전달) |
| POST | `/api/shop/inquiries` | 1:1 문의 접수(주문 선택 필수, 토큰 없음, 구매자당 5/분, 본문 2000자·제목 100자, 같은 내용은 10분 안에 같은 접수). ax-backend #419 머지 후 동작 |

### 토스페이먼츠 테스트 결제
shop-api가 `PAYMENT_PROVIDER=toss_test`이고 루트 `.env`에 `VITE_TOSS_CLIENT_KEY`(결제위젯 연동 키의 **클라이언트 키**, `test_gck_`로 시작)가 있으면 결제 화면·주문 상세의 결제 수단 자리에 토스 결제위젯이 나온다. 둘 중 하나라도 없으면 지금의 데모 결제 그대로다.

- 흐름: 주문 생성 → `prepare` → (금액이 다르면 서버 금액으로 `setAmount`) → 장바구니 비우기 → `requestPayment` → `/payments/success`에서 `confirm` / `/payments/fail`에서 안내
- 시크릿 키는 화면에 두지 않는다(shop-api만 가진다). 테스트 결제는 토스 문서의 테스트 결제 방법으로만 한다.
- 게이트웨이 이미지는 빌드 인자 `VITE_TOSS_CLIENT_KEY`(기본 비움)로 받는다. 두 경로는 nginx `try_files … /index.html`로 SPA가 연다.
- 코드: `src/lib/toss.ts`(경로 판단·위젯), `src/components/shop/TossWidgetBox.tsx`, `src/pages/PaymentSuccess.tsx`·`PaymentFail.tsx`

### 준비 중인 기능 (ax-backend#385 결과를 기다림)
shop-api에 아직 없어서 화면에서 오류 대신 "준비 중" 안내를 보여 준다. 코드에는 `TODO(ax-backend#385)`가 붙어 있고, 연결 자리는 `api.ts` 아래쪽 스텁(`previewClaim`·`claims`·`inquiryHistory`·`reviews`)이다.

- 적립금 조회·사용, 쿠폰 입력·할인 (쿠폰 할인은 가격 미리보기에서 "확인 필요"로 표시)
- 판매량순·신상품순 정렬, 서버 검색
- 클레임 환불액 미리보기, 신청 내역 조회·철회·사진 추가
- 1:1 문의 내역 조회(접수는 가능), 구매 후기

### 상품 표지 사진
상품 표지는 `shop-web/public/covers/<product_id>.jpg`(예: `prd_0001.jpg`, png·webp도 됨)에 파일을 두면 사진으로 나오고, 파일이 없거나 불러오지 못하면 상품마다 색으로 만든 제목 카드(`Cover.tsx`)로 돌아간다. 비율은 책 3:4, 문구·굿즈 1:1로 잘리므로 같은 비율로 준비한다. 사진은 상업적으로 써도 되는 라이선스(직접 촬영, Unsplash·Pexels·Pixabay 등)만 쓰고, 출처와 라이선스는 PR 설명에 적는다.

## 4. common-frontend
`@dejafix/common-frontend`를 git 태그(`git+https://github.com/DejaFix/common-frontend.git#v0.1.0`)로 설치해 두었다.
TODO: common-frontend 버전 태그로 설치 (AppShell 메뉴에 부서 항목, 색은 토큰만)
— 진입점에서 `import '@dejafix/common-frontend/styles.css'` 후 `AppShell`로 감싼다. 색은 디자인 토큰(CSS 변수)만 쓰고 직접 색 값을 넣지 않는다.

**현재 상태:** catbooks 화면은 자체 Tailwind 설정(`tailwind.config.ts`)과 `src/styles.css`의 CSS 변수(라이트·다크)로 그린다. 색 값은 모두 CSS 변수를 거치지만 common-frontend 토큰은 아직 쓰지 않는다.
두 스타일을 한 번에 섞으면 `.btn`·`.card`·`.chip` 클래스 이름이 겹치므로, common-frontend 적용은 별도 PR로 한다.
