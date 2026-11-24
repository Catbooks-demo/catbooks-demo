# shop-api
담당: 재헌 · 포트: 8010
## 1. 역할
판매 사이트 서버(LLM 없음, 코드만). 도서·문구 오픈마켓의 상품·주문·가짜 결제 데이터의 원천이고, 모든 사건의 출발점인 이벤트를 발행한다.
- PR 1: 상품 관리, 주문(장바구니 없이 바로), 가짜 결제(성공·실패 응답), 시드 데이터, 주문·결제 이벤트 4종 발행.
- PR 2: 장애 주입·해제(SHOP-06)와 `shop.degraded`·`shop.recovered` 직접 측정.
- PR 3: 공지 배너 API(SHOP-08)와 쿠폰 발급 API(SHOP-10).
- 이후 PR: data용 조회 API(SHOP-11). 환불 API(SHOP-12)와 2차 이벤트(배송·반품·환불)는 2차다.
## 2. 실행 방법
- 서버: `docker compose -f infra/compose.yml up shop-api`, 또는 `uv run uvicorn ax_shop_api.main:app --port 8010`
- 목 모드: `SHOP_MOCK=1` (CI는 항상 1). 저장소는 SQLite이며 `SHOP_DB_PATH`로 파일을 정한다(기본 `:memory:`). 비어 있으면 시드를 넣는다.
- 단독 테스트: `uv run pytest shop-api`
## 3. 입출력 계약
- 경로 `/api/shop/<복수 명사>`, 성공 `{data, meta}`, 실패 `{error: {code, message}}`. 금액은 정수 원, 시간은 UTC.

| 메서드·경로 | 내용 |
| --- | --- |
| `GET /api/shop/products?q&genre&sort&page&size` | 쿼리가 없으면 지금처럼 활성 상품 **전체 배열**(AXcellent가 이렇게 읽는다). `q`는 이름·저자·출판사 부분 일치(대소문자 무시, 100자까지), `genre`는 `genre_code`로 거르고, `sort`는 `popular`(최근 30일 결제 수량, 기본)·`new`·`price_asc`·`price_desc`. `page`(1부터)를 주면 `size`(1~60, 기본 24)개씩 자르고 `meta`에 `total`·`page`·`size`를 싣는다 |
| `GET /api/shop/genres` | `[{genre_code, name, product_count}]`, `GENRES` 순서, 판매 중 상품 수(토큰 없음) |
| `GET /api/shop/products/best?genre&days=30&limit=10` | 토큰 없는 공개 베스트. 최근 `days`(1~93)일 **결제된 주문**의 수량 순 상품과 `sold_qty`(`limit` 1~50). `(genre, days, limit)`별로 `SHOP_BEST_CACHE_S`초 프로세스 안에 캐시(수량 묶음을 `(days, genre)`별로 캐시). 인기순·베스트는 환불 안 뺀 **결제 수량**(전액 환불 주문도 안 뺌)을 세므로 `sales-summary`의 `net_qty`와 다르다. 모르는 `genre`는 422 |
| `GET /api/shop/sellers`, `GET /api/shop/products[?seller_id&category_code]`, `GET /api/shop/products/{id}` | 판매자·상품 조회. 상품에 `description`(소개, 최대 4000자, 모르면 `null`)과 `isbn13`·`author`·`publisher`(책 정보)·`genre_code`(장르 코드, 모르면 `null`)가 있다 |
| `POST /api/shop/products`, `PATCH /api/shop/products/{id}` | 상품 등록·수정(판매 중지는 `active=false`). 선택 **장르** `genre_code`(`ax_shop_api/genres.py`의 `GENRES` 16개 코드 중 하나, 아니면 422, 빈 글·`null`은 모름·지움이고 PATCH에서 칸을 안 보내면 그대로)와 선택 **소개** `description`(4000자까지, 앞뒤 공백은 지움)과 선택 **책 정보** `isbn13`·`author`·`publisher`를 받는다: `isbn13`은 하이픈·공백을 뺀 13자리 숫자이고 `978`·`979`로 시작하며 확인 숫자가 맞아야 한다(아니면 422, 하이픈은 지우고 저장), `author`·`publisher`는 100자까지(앞뒤 공백은 지운다). 빈 글·`null`은 "모른다"이고 `PATCH`에서는 지운다(칸을 안 보내면 그대로). 판매자 입점(`POST /api/shop/sellers`)의 첫 상품도 같은 칸을 받는다. 시드 상품에는 넣지 않았다(값을 지어내지 않는다). 쓰기 권한은 기존과 같다(`admin` 등 상품 쓰기 토큰) |
| `GET /api/shop/users`, `GET /api/shop/users/{buyer_id}`, `POST /api/shop/sessions` | 쇼핑몰 화면용 구매자. 목록은 `admin` 토큰만, 한 명 조회는 본인(로그인 토큰) 또는 `admin`만이다(아래 3-1). `sessions`는 `SHOP_DEMO_LOGIN=on`일 때만 `{buyer_id}`를 받아 비밀번호 없는 데모 로그인 `{user, token}`을 주고, 꺼져 있으면 404다(`SHOP_BUYER_AUTH=legacy`에서는 옛 동작대로 토큰 없이 `{user}`만 준다). 한 명 조회·`sessions`의 사용자에는 `age_band`(연령대 구간 `10s`·`20s`·`30s`·`40s`·`50s_plus`, 모르면 `null`)와 `gender`(성별 코드 `female`·`male`·`no_answer`, 모르면 `null`)가 있고, 전체 목록에는 싣지 않는다 |
| `PATCH /api/shop/users/{buyer_id}` | 구매자 연령대 구간·성별 코드를 정한다. **보낸 칸만 바꾸고**(안 보낸 칸은 그대로) 본문 `{age_band, gender}` 중 하나는 있어야 한다(없으면 422). `gender`는 `female`·`male`·`no_answer` 중 하나이거나 `null`(지움)이고 직업·생년월일은 받지 않는다. 아래 `age_band`는 `10s`·`20s`·`30s`·`40s`·`50s_plus` 중 하나이거나 `null`(지움)이고 칸이 없거나 다른 값·다른 키는 422다. 생년월일·나이는 받지 않고 구간만 저장한다(개인정보 부담을 줄인다). 없는 구매자는 404 `BUYER_NOT_FOUND`. 본인(로그인 토큰) 또는 `admin` 토큰만 바꿀 수 있고(`GET`으로 읽는 것도 같다, 아래 3-1) 바꾸면 `buyers.updated_at`을 적는다. 값이 들어오기 전의 구매자는 `age_band`가 `null`이고 채워 넣지 않는다 |
| `GET /api/shop/categories` | 판매 중 상품의 카테고리(`category_code`·`name`·`product_count`). 표시 이름을 모르는 코드는 `name`이 null |
| `POST /api/shop/cart/price` | 장바구니 가격 미리보기(저장·재고 차감 없음). `items`, 선택 `points_used`, 선택 `coupon_id`·`coupon_units`(기본 1)·`buyer_id`. 상품 합계 → 배송비(5만 원 미만 3,000원) → 쿠폰 → 적립금 순서. 쿠폰은 `coupon_id`와 `buyer_id`가 함께 있을 때만 검증해 `coupon_discount`를 계산하고(없으면 0, `buyer_id` 없이 쿠폰만 주면 400 `BUYER_REQUIRED`) 장수는 차감하지 않는다. `payable_amount`는 `POST /orders`의 `total_amount`와 같다 |
| `POST /api/shop/orders` | 주문. 가격·총액은 DB 값으로 계산하고 재고를 차감한다. 선택 `points_used`(원, 품목 합계 이하)를 받는다. 선택 `coupon_id`와 `coupon_units`(기본 1)로 **본인의 쿠폰 한 건**을 쓴다(`campaign`·`compensation` 모두). 할인은 `min(장수×unit_value, 상품 합계)`로 배송비는 할인하지 않고, 적립금은 쿠폰을 뺀 상품 금액을 넘을 수 없다. 다른 구매자의 쿠폰은 403 `COUPON_NOT_YOURS`, 없는 쿠폰은 404 `COUPON_NOT_FOUND`, 만료는 409 `COUPON_EXPIRED`, 남은 장수 초과는 409 `COUPON_UNITS_EXCEEDED`다. 응답에 `coupon_id`·`coupon_units`·`coupon_discount`, 품목에 `coupon_share`(할인을 품목 금액 비율로 나눈 몫)가 있고 `paid_amount` = 단가×수량 − `points_share` − `coupon_share`다. 선택 `idempotency_key`(1~128자)를 주면 같은 구매자의 같은 키는 주문 하나다: 다시 오면 재고와 쿠폰 장수를 건드리지 않고 처음 주문을 200으로 돌려주고(`meta.replayed: true`, 새로 만들면 201), 상품·수량·포인트·쿠폰이 다르면 409 `IDEMPOTENCY_MISMATCH`다. 키는 주문 한 건당 하나를 만들어 재시도에만 다시 보낸다(매번 새 키를 보내면 보호가 안 되고, 새 주문에 같은 키를 쓰면 처음 주문이 돌아온다). 키가 없으면 매번 새 주문이다. `total_amount` = 품목 합계 − 포인트 + `shipping_fee`(품목 합계 5만 원 미만 3,000원, 이상 0원)로 **실제 결제(현금) 금액**이고 결제 `amount`와 같다 |
| `GET /api/shop/orders[?buyer_id&since&until&limit&cursor]`, `GET /api/shop/orders/{id}` | 주문 조회. 목록은 order_id 순 **한 페이지**다: `limit` 기본 200(최대 1000, 넘으면 422), `buyer_id`가 있어도 같다. `meta.next_cursor`(마지막 페이지면 `null`)를 다음 요청의 `cursor`로 주면 이어서 나온다. 기간 안 전체가 필요하면 `since`·`until`과 함께 cursor로 끝까지 넘긴다(25만 건을 한 번에 읽다 서버가 멈춘 일, 2026-11-10). 품목·결제·환불은 페이지 안 주문을 묶어 한 번에 읽고, DB 읽기는 이벤트 루프 밖(스레드)에서 한다. 주문 필드 `shipping_fee`·`points_used`·`delivered_at`(배송 전 null), 품목 필드 `order_item_id`(`oit_`)·`title`(주문 당시 이름)·`points_share`(포인트를 품목 금액 비율로 나눈 몫, 남는 1원 단위는 뒤 품목부터)·`paid_amount`(= 단가×수량 − `points_share`), 결제 필드 `created_at`. 항상 `total_amount` = Σ`paid_amount` + `shipping_fee`. 환불 기록 목록 `refunds`(`refund_id`·`payment_id`·`amount`·`created_at`, `created_at` 오름차순, 없으면 `[]`)가 `refunded_amount`(합계)와 함께 온다 — 환불을 실제 환불 시각으로 잡는 근거 |
| `POST /api/shop/orders/{id}/payments` | 가짜 결제. `simulate`: `success`·`failure`. `pending_payment`·`payment_failed` 주문만 결제할 수 있고(그 밖은 409 `ORDER_NOT_PAYABLE`), 실패한 주문을 다시 부르면 새 결제 기록이 쌓이며 성공하면 `paid`가 된다. 상태 확인·결제 기록·상태 변경은 한 트랜잭션이라 동시에 들어와도 성공 결제는 1건이고 나머지는 `ORDER_NOT_PAYABLE`이다. 실패 코드(`PG_UNAVAILABLE` 서버 문제 / `PG_DECLINED` 거절)는 응답의 `payments[].failure_code`와 `payment.failed` 이벤트 `failure_code`에 그대로 담긴다 |
| `GET /api/shop/payment-config` | 화면이 결제 방식을 고르는 값 `{provider: "fake" \| "toss_test"}`. 키는 담지 않는다(아래 4-2) |
| `POST /api/shop/orders/{id}/payments/toss/prepare` | 토스 테스트 결제 시도 만들기 → `{toss_order_id, amount, order_name}`. `pending_payment`·`payment_failed` 주문만(그 밖 409 `ORDER_NOT_PAYABLE`), 금액은 DB 주문 합계(배송비 포함, 요청 값 없음). `toss_order_id` = `{order_id}-a{시도 번호}`로 시도마다 새로, `order_name`은 "첫 상품명 외 N건". 가짜 결제 모드면 409 `TOSS_DISABLED` |
| `POST /api/shop/payments/toss/confirm` | 토스 승인. 본문 `{toss_order_id, payment_key, amount}`(결제위젯 성공 리다이렉트 값). 없는 시도 404 `PAYMENT_ATTEMPT_NOT_FOUND`, 금액이 시도 금액과 다르면 400 `AMOUNT_MISMATCH`(토스 호출 안 함). 이미 끝난 시도는 처음 결과를 200으로 돌려준다(`meta.replayed: true`, 토스에 다시 보내지 않음). 응답 `data`는 주문(`GET /orders/{id}`와 같은 모양). 가짜 결제 모드면 409 `TOSS_DISABLED` |
| `POST /api/shop/admin/orders/{id}/shipped` | 발송 시각 기록(시연·시드용). `admin` 토큰만, 결제된 주문만(409 `ORDER_NOT_PAID`), 본문 `shipped_at`(시간대 있는 시각, 비우면 지금), 이미 있으면 처음 값을 유지한다. 이미 도착한 주문이면 도착 시각보다 늦을 수 없다(409 `INVALID_TIME`). 주문 상태는 바꾸지 않고 이벤트도 내지 않는다. 주문 응답에는 `shipped_at`과 **약속 배송일 `promised_delivery_at`**(결제 성공 시각 + 3일, 결제 전·옛 주문은 null)이 있다 — 고객지원이 '약속한 날보다 늦었나'를 코드로 판정하는 근거 |
| `POST /api/shop/admin/orders/{id}/delivered` | 배송 완료 시각 기록(시연·시드용). `admin` 토큰만, 결제된 주문만(409 `ORDER_NOT_PAID`), 본문 `delivered_at`(시간대 있는 시각, 비우면 지금), 이미 있으면 처음 값을 유지한다. 주문 상태는 바꾸지 않고 `order.delivered` 이벤트도 내지 않는다 |
| `POST /api/shop/claims` | 고객 반품·환불 신청(토큰 없음, 주문 구매자만). 고객지원 `create_claim`을 A2A로 부른다(요청자 `shop-api`, 사건 ID `ctx_shop_<hex>`). 금액은 받지 않고, 사유 문장 `description`은 `untrusted`로만 넘긴다. 응답 `data`는 고객지원의 클레임이고 `meta.context_id`가 사건 ID다. 구매자가 다르면 403 `BUYER_MISMATCH`, 고객지원 거부·타임아웃·오류는 403·504·502 |
| `POST /api/shop/inquiries` | 고객 문의 접수(구매자 로그인 토큰 필요, #385 B안). 요청 `{buyer_id, order_id, subject?, body, images?}`로 `order_id`는 필수이고 그 구매자의 주문이어야 한다(없는 주문과 남의 주문은 같은 404 `ORDER_NOT_FOUND`). **고객지원을 부르지 않고 이 서버의 문의함(`inquiry_inbox`, `inbox.py`)에 저장한다**(예진 님 제안: 저장 → AXcellent가 읽어 오기). 고객지원이 정해진 간격으로 `GET /api/shop/admin/inquiries`로 읽어 가 처리하고 `POST /api/shop/admin/inquiries/{seq}/synced`로 읽었다고 알린다. 멱등 키 대신 접수 지문(`digest`)이 같은 접수는 한 건이다. 지문은 구매자·주문·제목·본문과 시간 구간 번호(`floor(epoch/창)`)의 해시로 만들어, 같은 구간(`SHOP_INQUIRY_DEDUP_WINDOW_SEC`, 기본 600초, 0 이하·잘못된 값이면 기본값)의 같은 접수는 한 번만 처리되고 창이 지나면 새 문의가 된다(구간 경계에서 연타하면 키가 둘로 갈릴 수 있으나 수용). 원문은 로그·키에 남기지 않는다. `images`(선택)는 영수증 사진 `[{ref, media_type(jpeg·png·webp), data_base64}]`로 최대 4장·한 장 base64 6,000,000자(약 4.5MB)·합계 12,000,000자이고, base64가 아니면 400 `INVALID_IMAGE`, 5장 이상은 400 `TOO_MANY_IMAGES`, 한 장이 한도를 넘으면 413 `IMAGE_TOO_LARGE`, 합계 초과는 413 `IMAGES_TOO_LARGE`이다(둘 다 우리 오류 모양이고 사진 값은 싣지 않는다). 형식이 틀린 요청의 422는 `detail`에서 요청이 보낸 값(`input`)을 뺀다. 사진은 문의함에 읽어 갈 때까지만 보관하고(읽어 가면 지운다) 로그에는 남기지 않으며, 같은 글에 사진이 다르면 지문도 달라진다. 응답 `data`는 `{id, status, order_id, updated_at}`뿐이고 `id`는 이 서버의 접수 번호(고객용 목록·상세·평가도 이 번호로 열고, 고객지원이 읽어 간 문의는 이 서버가 고객지원 문의 번호로 바꿔 부르며, 아직 읽어 가지 않은 문의는 접수한 내용만 보여 준다), `status`는 `open`, `meta.context_id`가 사건 ID다. 형식·빈 본문·길이 초과 400, 속도 제한 429 `RATE_LIMITED`(`SHOP_INQUIRY_RATE_PER_BUYER` 기본 5/분, `SHOP_INQUIRY_RATE_GLOBAL` 기본 60/분, 본문 `SHOP_INQUIRY_MAX_BODY` 2000자, 제목 `SHOP_INQUIRY_MAX_SUBJECT` 100자), 접수는 고객지원이 꺼져 있어도 된다. 주문·결제는 읽기만 한다 |
| `GET /api/shop/admin/inquiries?limit=` | 문의함에서 아직 읽어 가지 않은 문의를 접수 순서로(`seq`·`context_id`·`buyer_id`·`age_band`(구매자 연령대 구간, 모르면 `null`)·`customer_name`·`order_id`·`subject`·`message`·`images`·`created_at`), `meta.total`은 남은 전체 수. 요청자는 `support`·`admin` 토큰만(그 밖은 403, 토큰 없으면 401). 문의 원문과 사진이 들어 있다 |
| `POST /api/shop/admin/inquiries/{seq}/synced` | 고객지원이 이 문의를 처리했다고 표시한다. 요청 `{support_inquiry_id}`(1 이상, 그 밖의 키는 422). 사진을 지우고 이미 표시했으면 처음 결과를 그대로 돌려준다. 없는 번호는 404 `INQUIRY_NOT_FOUND`. 요청자는 `support`·`admin`만 |
| `POST /api/shop/admin/inquiries/{seq}/failed` | 읽어 간 쪽이 이 문의를 처리하지 못했다고 알린다. 요청 `{reason}`(짧은 코드 1~40자, 그 밖의 키는 422). 실패할 때마다 다시 읽는 시각을 늦추고(1분부터 두 배, 최대 30분) `SHOP_INBOX_MAX_ATTEMPTS`(기본 6)번째 실패에서 실패로 빼 둔다(`GET /admin/inquiries` 응답 `meta.failed`에 센다). 그래서 계속 실패하는 접수가 문의함 앞을 막지 않는다. 이미 처리된 문의는 그대로 두고 없는 번호는 404. 요청자는 `support`·`admin`만 |
| `GET /api/shop/claims?buyer_id=&order_id=` | 내 클레임(취소·반품·교환) 신청과 AXcellent가 적어 준 결과(토큰 없음, 읽기). 최근 순, 결과가 오기 전에는 `status=received`·`result=null`. `order_id`로 좁힐 수 있다 |
| `GET /api/shop/admin/claims?state=new|open` | `new`(기본): 읽을 차례가 된 신청(`seq`·`key`·구매자·`age_band`(구매자 연령대 구간, 모르면 `null`)·주문·종류·사유·품목·설명·`attempts`…), `meta.total`·`meta.failed`. `open`: 읽어 갔지만 끝나지 않은 신청(`seq`·`support_claim_id`·`status`·`result_digest`)을 `after`(접수 번호)·`limit`으로 쪽 나눠 주고 `meta.next_after`로 이어 읽는다. 반려된 신청도 일주일은 포함한다. 요청자는 `support`·`admin`만 |
| `POST /api/shop/admin/claims/{seq}/result` | 고객지원이 판정한 결과를 적는다. 요청 `{support_claim_id?, status, result}`. 클레임으로 받지 않았으면 번호 없이 `status=not_accepted`와 이유만. 같은 값이면 그대로, 다른 클레임 번호가 이미 적혀 있으면 409 `CLAIM_MISMATCH`, 없는 번호는 404 |
| `POST /api/shop/admin/claims/{seq}/failed` | 문의함과 같다. 상한에 닿으면 고객에게는 `not_accepted`(접수를 처리하지 못했다)로 닫는다 |
| `GET /api/shop/admin/intake/stuck` | 실패로 빼 둔 문의·클레임 접수 목록(`kind`·`seq`·`reason`·`attempts`·`failed_at`·`created_at`). 고객이 쓴 글은 싣지 않는다. 요청자는 `support`·`admin`만 |
| `POST /api/shop/admin/inquiries/{seq}/retry` · `POST /api/shop/admin/claims/{seq}/retry` | 실패로 뺀 접수를 다시 읽는 차례로 돌린다(횟수 0). 클레임은 고객 화면에도 다시 `received`로 보인다. 응답 `{seq, requeued}`(실패 상태가 아니었으면 `false`로 그대로 둔다), 없는 번호는 404. 요청자는 `support`·`admin`만 |
| `GET /api/shop/inquiries?buyer_id=&limit=` | 내 문의 목록(구매자 로그인 토큰 필요, 읽기). **이 서버가 저장한 값만 읽는다(고객지원을 부르지 않는다).** 고객지원이 읽어 간 문의는 되돌려 쓴 값(`POST /admin/inquiries/{seq}/view`)을, 아직 안 읽은 문의는 접수한 내용(제목·본문 앞 80자)을 보여 준다(읽어 간 뒤에는 본문 사본을 지우므로 되돌려 쓰기 전에는 제목만 보인다). 여러 번 처리하지 못해 따로 빼 둔 접수는 `intake_failed`·`notice`로 접수 실패를 알린다. 고객용 키(`id`·`subject`·`status`·`order_id`·`updated_at`·`rated`·`preview`)만 남기고 `can_rate`(해결됐고 아직 평가 안 함)를 붙인다. 평가를 남겼으면 읽어 가기 전이라도 `rated`다. 최근 순, `limit` 1~100(기본 50), `meta.total`은 전체 수. 구매자 ID 형식 400 |
| `GET /api/shop/inquiries/{inquiry_id}?buyer_id=` | 내 문의 하나와 주고받은 메시지(`id`·`role`·`text`·`created_at`). 같은 방식으로 저장값만 읽는다. 남의 문의와 없는 문의는 같은 404 `NOT_FOUND`. 문의 번호는 1 이상 정수 |
| `POST /api/shop/inquiries/{inquiry_id}/feedback` | 해결된 문의의 고객 평가(구매자 로그인 토큰 필요). 요청 `{buyer_id, rating(1~5), resolved, comment?(300자)}`(그 밖의 키는 422). **저장만 하고(고객지원을 부르지 않는다)** AXcellent가 읽어 가 `submit_feedback`으로 후기로 남긴다. 되돌려 쓴 문의 값이 해결(`resolved`)이고 아직 평가 안 했을 때만 받는다: 남의 문의와 없는 문의는 같은 404 `NOT_FOUND`, 읽어 가기 전·해결 전·이미 남김은 409 `INVALID_STATE`. 응답 `data`는 `{inquiry_id, rated, rating}`뿐이다. 구매자 ID 형식 400, 속도 제한 429 `RATE_LIMITED`(문의 접수와 따로 같은 환경변수 기준) |
| `POST /api/shop/ideas` | 고객 아이디어(토큰 없음). 요청 `{buyer_id, kind, text(500자)}`. **저장만 한다.** AXcellent가 읽어 가 `submit_idea`로 분류·저장하고, 환불류·광고·너무 짧은 글은 받지 않고 결과 코드만 남긴다(고객에게는 접수됨만 보인다). 같은 구매자·종류·글은 한 건. 읽어 간 뒤에는 글 원문 사본을 지우고 앞부분 30자만 남긴다. 응답 `{idea_id(접수 번호), received: true}`. 속도 제한 429 |
| `GET /api/shop/ideas?buyer_id=&limit=` | 내 아이디어 내역(토큰 없음, 읽기). 접수 번호·종류·앞부분·`status`(`pending` 처리 전, `received`, `redirected` 문의로 가야 함, `not_accepted`)·고객지원이 되돌려 쓴 안내 한 줄(`notice`). 구매자 ID 형식 400 |
| `POST /api/shop/admin/claims/{claim_id}/return-received` | 반품 입고(`admin` 토큰만). **이 서버에 `ret_` 번호로 기록하고 `return.received`를 발행할 뿐 고객지원을 부르지 않는다.** AXcellent가 읽어 가 `receive_return`으로 클레임을 입고 처리한다. 같은 클레임을 다시 부르면 이전 기록을 돌려준다. 없는 주문·모르는 클레임은 404, 신청의 주문과 다르면 409 `ORDER_MISMATCH`, 아직 입고 단계(`awaiting_return`)가 아니면 409 `INVALID_STATE`이고 이 경우 기록도 이벤트도 없다(이 서버의 클레임 신청 기록으로 확인한다) |
| `GET /api/shop/admin/inquiries/open?after&limit` | 읽어 갔지만 고객이 보는 값이 아직 끝나지 않은 문의(`seq`·`support_inquiry_id`·`view_digest`)를 쪽 나눠 준다(`meta.next_after`). 해결되고 평가까지 끝난 문의, 해결된 지 `SHOP_VIEW_RESOLVED_DAYS`(기본 7)일이 지난 문의는 뺀다. `sweep=true`면 이 조건을 무시하고 고객 화면에 남은 문의를 모두 준다(고객지원이 가끔 한 번 훑어 늦은 개인정보 삭제를 놓치지 않는다). `support`·`admin`만 |
| `POST /api/shop/admin/inquiries/{seq}/view` | 고객지원이 정한 고객용 문의 값을 되돌려 쓴다. 요청 `{view, view_digest}`. 허용한 키(`CUSTOMER_FIELDS`·메시지 네 칸)만 남기고 같은 지문이면 그대로 둔다. `support`·`admin`만, 없는 번호 404 |
| `POST /api/shop/admin/inquiries/{seq}/gone` | 고객지원에서 찾을 수 없는 문의(개인정보 삭제·고객지원 재시작)를 고객 화면에서 빼고 되돌려 쓴 값을 지운다. 이미 뺀 문의는 그대로, 없는 번호는 404. `support`·`admin`만 |
| `POST /api/shop/admin/inquiries/by-support/{support_inquiry_id}/gone` | 위와 같은데 고객지원 문의 번호로 부른다. 고객지원이 문의를 지우는 그 자리에서 알려 고객 화면에서 빼고 되돌려 쓴 값·원문 사본을 지운다(못 알리면 읽어 가기가 다음 주기에 이어서 한다). 이미 뺀 문의도 200, 읽어 간 적 없는 번호는 404. `support`·`admin`만 |
| `GET /api/shop/admin/ideas?limit` · `POST /api/shop/admin/ideas/{seq}/synced` | 아이디어함 읽기(접수 순서, 항목에 `age_band`(구매자 연령대 구간, 모르면 `null`), `meta.total`)와 처리 표시. 표시 요청 `{outcome(짧은 코드), ref?, notice?(120자)}`. 이미 표시한 건 바뀌지 않는다. `support`·`admin`만 |
| `GET /api/shop/admin/feedback?limit` · `POST /api/shop/admin/inquiries/{seq}/feedback-synced` | 읽어 가지 않은 평가 읽기와 처리 표시(`{outcome}`). 평가 글(`comment`)은 읽는 쪽이 `untrusted`로만 다룬다. 고객지원이 받지 않은 평가(`rated`·`already_rated`가 아닌 결과)는 지워서 고객이 다시 남길 수 있다. `support`·`admin`만 |
| `GET /api/shop/admin/returns?limit` · `POST /api/shop/admin/returns/{claim_id}/synced` | 기록된 반품 입고 읽기(입고 순서, `meta.total`)와 처리 표시(`{outcome}`). `support`·`admin`만 |
| `POST /api/shop/admin/returns/{claim_id}/requeue` | 처리를 포기(`gave_up`)했거나 잘못 닫은 반품 입고의 읽은 표시를 지워 다시 읽는 차례로 돌린다(사람이 확인한 뒤). 입고 기록과 `return.received`는 그대로다. 없는 번호 404. `support`·`admin`만 |
| `POST /api/shop/payments/{id}/refund` | 현금 환불(SHOP-12, **`support` 토큰만**, 마케팅·재무·관리자 거부). `refund_amount`·`idempotency_key`(`<claim.context_id>:refund_request:claim:<id>`)·선택 `context_id`. 같은 키·같은 내용은 이전 환불을 200으로 돌려주고(`meta.replayed`), 다르면 409, 성공한 결제의 환불 가능 금액을 넘으면 409 `REFUND_EXCEEDS_PAYMENT`. 반영되면 `payment.refunded`를 발행하고 주문 `refunded_amount`가 늘어난다(상태는 `paid` 그대로) |
| `GET·POST·DELETE /api/shop/admin/faults` | 장애 상태 조회·주입(`kind`: `latency`+`delay_ms`, `failure`)·해제. 주입은 `admin`, 해제는 `admin`·`infra` 토큰만(`Authorization: Bearer`) |
| `GET·PUT /api/shop/admin/payment-version`, `POST /api/shop/admin/payment-version/rollback` | 결제 설정 버전(잘못된 배포 시연). v1 정상, v2는 결제사 승인이 500대로 실패(토스 테스트 키면 `TossPayments-Test-Code`). 배포(PUT)는 `admin`만, 이전 버전으로 되돌리기(rollback)는 `admin`·`infra`(인프라 롤백), 라이브 키면 v2 거절. 장애 스위치(`active`)가 아니다 |
| `POST /api/shop/admin/faults` `kind=pg_slow` · `DELETE /api/shop/admin/faults/pg` | 시험용 "결제사 쪽 지연"(`delay_ms` 1~10000). 결제마다 실제로 기다리고 그 지연을 표본에 남긴다. 장애 스위치(`active`)가 아니라 위 해제로는 꺼지지 않는다. 켜기·끄기 모두 `admin`만. 상태는 `pg_delay_ms` |
| `GET /api/shop/banners`, `POST /api/shop/banners`, `DELETE /api/shop/banners/{notice_id}` | 공지 배너. 고객 화면은 토큰 없이 게시 중이고 기간 안인 배너만 읽고, 게시·해제는 `marketing` 토큰만(같은 `notice_id` 재게시는 첫 배너 그대로) |
| `POST /api/shop/coupons`, `GET /api/shop/coupons[?buyer_id&purpose_code&campaign_id]`, `GET /api/shop/coupons/{id}` | 쿠폰. 발급은 `marketing` 토큰만, `idempotency_key` 필수(같은 키·같은 내용은 이전 쿠폰, 다르면 409), 총액은 서버가 건수×단가로 계산, 조회는 `marketing`·`support`·`finance`·`orchestrator`·`data`(캠페인 성과를 세는 읽기 전용). 발급에 선택 `expires_at`(시간대가 있는 시각, 지금보다 뒤)을 주면 그 시각부터 주문에 쓸 수 없고 없으면 무기한이다. 응답의 `used_units`·`remaining_units`·`expired`는 주문에서 계산한 값이다: 취소됐거나 전액 환불된 주문(`refunds` 합계 = 결제 금액)은 장수를 돌려주고 부분 환불은 돌려주지 않는다 |
| `GET /api/shop/orders/attributed?from&to[&campaign_id&source&limit]` | 유입 출처(`src_source`·`src_medium`·`src_campaign`)가 있는 주문 목록(주문 id·시각·금액·상태·품목 product_id/qty/unit_price·유입 3값, `buyer_id` 등 개인값 없음). `data` 토큰만(admin도 403). `campaign_id`는 `src_campaign`, `source`는 `src_source`와 같은 값만. 취소·전액 환불 주문은 뺀다(쿠폰 사용 장수와 같은 기준). 기간 최대 31일, `limit` 기본 500(최대 1000)이고 넘으면 `truncated`. 주문 쪽은 `POST /api/shop/orders`에 선택 `src_*`를 받고(영문·숫자·`-`·`_`·`.` 64자, 어긋난 값은 422가 아니라 그 값만 비운다, 멱등 비교에 안 넣는다) |
| `GET /api/shop/failed-orders?from&to[&category&limit]` | 실패 주문 목록(결제 `failed` 한 건이 한 줄, `category`는 첫 품목 분류, `reason`은 실패 코드). `data`·`admin` 토큰만. `from`·`to`는 시간대 있는 UTC 시각, `limit` 기본 1000(최대 5000)이고 넘으면 `truncated`. 결제 상태·시각 인덱스로 시각 순으로 읽다 limit에서 멈춘다. DB 읽기는 이벤트 루프 밖(스레드)에서 한다 |
| `GET /api/shop/sales-summary?from&to[&group_by][&period]` | 결제가 끝난 주문의 판매 수량을 기준별로 합친다(읽기 전용, `support`·`data`·`admin` 토큰만). `from`·`to`는 시간대가 있는 시각(없으면 400 `INVALID_QUERY`), 접수 시각이 `from` 이상 `to` 미만이고 기간은 93일까지(넘으면 400 `RANGE_TOO_LONG`, `to`가 `from` 이하면 400 `INVALID_RANGE`). 선택 `period`(`day`·`week`·`month`)를 주면 접수 시각도 그 단위로 나눠 줄마다 `period_start`(구간 시작 날짜 `YYYY-MM-DD`, **한국 시간(KST, UTC+9) 기준**: 하루는 한국 날짜 0시, 주는 한국 월요일 0시, 월은 1일 0시에 시작, 날짜도 한국 날짜)가 붙고 구간 순으로 정렬하며 응답 `data`에 `period`가 실린다(다른 값은 422 `INVALID_PERIOD`, 안 주면 응답 모양은 그대로, 첫·끝 구간은 `from`·`to` 안의 주문만 센 부분 구간일 수 있고 작은 묶음 숨기기는 (구간, 기준) 줄마다 따로 판단한다). `group_by`는 `product`·`age_band`·`category`·`gender`·`occupation`·`genre`를 쉼표로 이어 고르고(기본 `product`, 중복은 하나로) 다른 값·빈 값은 422 `INVALID_GROUP_BY`. 응답 `data`는 `{from, to, group_by, rows, collected_at}`이고 한 줄은 묶은 기준 값(`product_id`·`age_band`·`category_code`·`gender`·`occupation`·`genre_code`)과 `order_count`(주문 수)·`gross_qty`·`net_qty`다. `gross_qty`는 결제가 끝난 주문의 판매 수량(전액 환불된 주문도 센다 — 클레임 비율의 분모), `net_qty`는 그중 전액 환불된 주문을 뺀 수량(`orders/attributed`·쿠폰 귀속 집계와 같은 기준, 취소된 주문은 결제가 끝난 적이 없어 처음부터 빠진다). **작은 묶음 숨기기**: `age_band`·`gender`·`occupation`으로 묶으면 한 줄의 구매자가 5명 미만일 때 `order_count`·`gross_qty`·`net_qty`를 `null`로 숨기고(줄은 남긴다) `suppressed: true`를 붙인다(이 기준이 든 집계의 모든 줄에 `suppressed`가 있고, 아닐 때는 키가 없다). 구매자 수는 주문 수가 아니라 서로 다른 구매자다. 개인이 드러나지 않게 하는 규칙이지만 **한계가 있다**: 같은 시각 범위를 다른 기준으로 나눠 부른 값끼리 빼면 숨긴 숫자를 추정할 수 있다(예: 월 줄에서 그 달의 일 줄들을 빼면 숨긴 하루, 합계 줄에서 보이는 줄들을 빼면 숨긴 줄). 이를 막는 보완 숨기기는 하지 않으므로 부르는 쪽은 서로 다른 단위·기준의 결과를 빼서 쓰지 않는다. `age_band`는 **주문 시점이 아니라 지금의 `buyers.age_band`**로 묶는다(구매자가 나중에 구간을 바꾸면 과거 주문도 새 구간으로 센다). `age_band`가 `null`인 줄은 연령대를 모르는 구매자다. 구매자 ID·이름·결제 수단은 돌려주지 않는다. 주문·품목 합계를 DB에서 한 번에 낸다(합성 주문 20만 건·93일 약 3.3초, 4만 건 약 0.5초, 로컬 측정). 판매 서버는 연결 하나를 잠가 쓰므로 합치는 동안 다른 요청이 기다린다 — 주문이 30만 건쯤이면 93일을 한 번에 읽을 때 몇 초 동안 연결을 잡아 결제도 기다리고 결제 모니터가 지연으로 잡을 수 있다. 부르는 쪽은 기간을 짧게 나눠 읽거나 몇 분 캐시해서 자주 부르지 않는다 |
| `GET /api/shop/sales-export[?cursor&limit]` | 판매 기록 내보내기(계약 `data_access/shop_sales.py`, data 수집기가 매출 분석 표준 저장소로 받아 감). `data`·`admin`·`dashboard-api` 토큰만(`dashboard-api`는 AXcellent 변화 감지기, #1457. 이 경로만 열고 다른 data 읽기 경로는 403). 취소된 주문 줄에는 `cancel_reason_code`(취소 주체 `buyer`·`seller`·`system`)를 싣는다: 구매자 토큰으로 취소하면 무엇을 보내도 `buyer`, `admin`은 보낸 값. 칸이 생기기 전 취소는 남은 `order.cancelled` 이벤트에서 채우고, 이벤트가 지워졌으면 `null`(지어내지 않는다). 취소가 아니면 `null`. `(updated_at, order_id)` 오름차순, `limit` 1~1000(기본 500), 응답의 `next_cursor`를 다음 `cursor`로 넘기면 이어서 받는다(받은 것이 없으면 같은 커서). 상태가 바뀐 주문과 환불이 생긴 주문(환불 때 주문 `updated_at`을 바꾼다)은 다시 나온다. 같은 초에 늦게 저장된 주문을 건너뛰지 않게 `updated_at`이 5초 지난 주문만 준다. 주문마다 상품 줄(분류·쿠폰·포인트 몫)·결제 상태·환불(금액·시각). 이름·연락처·주소 없음, 쓰기 없음. `orders(updated_at, order_id)` 인덱스로 읽고 DB 읽기는 이벤트 루프 밖(스레드)에서 한다 |
| `GET /api/shop/metric-series?metric&from&to[&bucket]` | 지표 시계열(`order_count`·`order_fail_count`·`payment_fail_rate`·`payment_count`와 주문 금액·쿠폰 지표 `order_amount`(결제가 끝난 주문의 `total_amount` 합계, 결제 대기·실패·취소는 빼고 환불은 안 뺀다)·`coupon_order_count`·`coupon_discount_amount`(쿠폰을 쓴 주문 수·할인 합계, 취소·전액 환불 주문은 뺀다), 5분 버킷). 건수·금액은 빈 버킷을 0으로 채우고 비율은 결제 시도가 없는 버킷을 뺀다(값이 없는 것과 0%를 섞지 않는다). 기간은 **24시간까지**(넘으면 422 `RANGE_TOO_LONG`, 더 긴 기간은 하루씩 나눠 부른다. data 커넥터는 이미 하루씩 나눈다). DB 읽기는 이벤트 루프 밖(스레드)에서 한다. `data`·`admin` 토큰만 |
| `GET /api/shop/sellers/{seller_id}/profile` | 판매자 정보(data fraud_score). `joined_at`=`sellers.created_at`, `product_count`=판매 중 상품 수, `listings_last_24h`=`now−24시간 <= created_at <= now`인 상품 수. 사업자 정보가 테이블에 없어 `business_hash`·`business_hash_duplicated`는 null. 없는 판매자는 404 `SELLER_NOT_FOUND`. `data`·`admin` 토큰만 |
| `GET /api/shop/sellers/{seller_id}/prices` | 판매자 판매 중 상품별 가격과 같은 카테고리 판매 중 상품(모든 판매자) 가격의 중앙값(짝수 개면 가운데 두 값 평균을 반올림). 없는 판매자는 404. `data`·`admin` 토큰만 |
| `POST /api/shop/orders/{id}/cancel` | 미결제·결제 실패 주문 취소(결제된 주문의 환불은 SHOP-12) |

- 장애 측정: 결제 시도마다 지연·5xx를 기록하고, **p95 1,000ms 초과 또는 5xx 2% 초과가 1분 지속**되면 `shop.degraded`(`component: payment`, `metric_code`: `p95_latency_ms`·`error_rate_5xx`)를, 정상 표본이 같은 지속 기준만큼 이어지면 `shop.recovered`를 발행한다. 장애 스위치 해제는 복구가 아니다: 켜져 있던 스위치를 끄면 그 스위치 때문에 쌓인 표본만 버리고 "해제됨"(`fault_state`의 `last_release`)을 남기며, 복구 신호는 새 표본으로 판정한다(이미 꺼져 있던 스위치를 끄면 아무것도 바꾸지 않고 응답의 `released`가 false). `shop.recovered`에는 풀린 방법 `recovery`(`fault_released` 장애 중 스위치를 껐음, `metrics_normal` 끄지 않고 지표가 정상)와 해제한 쪽 `released_by`(해제 API를 부른 토큰 이름, `infra`·`admin`)를 싣는다. 기준값은 `SHOP_DEGRADED_P95_MS`·`SHOP_DEGRADED_5XX_RATE`·`SHOP_DEGRADED_SUSTAIN_S`·`SHOP_MONITOR_WINDOW_S`로 바꾸고, 주기 판정 간격은 `SHOP_MONITOR_INTERVAL_S`(0이면 끔)다. 지연은 값만 기록한다(실제로 기다리지 않는다).
- `notice.published`·`coupon.issued`는 shop-api가 아니라 마케팅 에이전트가 `execute()` 뒤에 발행한다(shop-api의 배너·쿠폰 API는 그 결과를 반영만 한다).
- 발행 이벤트(공통 봉투, `source: shop-api`, `context_id: null`, 이벤트 버스는 `events.publish`만 사용): `order.created`, `order.cancelled`, `payment.succeeded`, `payment.failed`, `shop.degraded`, `shop.recovered`. 이벤트는 주문·결제와 같은 트랜잭션에 outbox로 저장하고 저장 뒤 발행한다(최소 한 번 전달, `event_id`는 키에서 만든다).
- 이벤트 `data` 모델은 계약(`contracts`)에 있으면 그것을 쓴다(`payment.failed`). 나머지 모델은 `to:contracts` 요청(#11)으로 추가 중이라, 반영되면 계약 모델로 교체한다.
- OpenAPI: `contracts/openapi/shop-api.json`은 CI가 자동 생성한다.
## 3-1. 회원 로그인·구매자 본인 확인
- 가입·로그인: `POST /api/shop/auth/signup`·`/auth/login`이 로그인 토큰을 준다(DB에는 sha256만 둔다). `POST /auth/logout`은 보낸 토큰을 끊는다. 토큰은 `Authorization: Bearer <토큰>`으로 보낸다.
  - 시도 제한(프로세스 안, `accounts.py` 상수): 로그인은 이메일별 5분 10번·접속 IP별 5분 30번, 가입은 IP별 10분 5번, 비밀번호 확인(`/me/password`·`/me/withdraw`)은 구매자별 5분 10번. 넘으면 429 `RATE_LIMITED`. IP는 연결한 주소만 본다(프록시 헤더는 믿지 않는다). 비밀번호 해시(scrypt)는 스레드에서 돌려 이벤트 루프를 막지 않는다.
  - `GET /api/shop/auth/demo-buyers` (토큰 없음, `SHOP_DEMO_LOGIN=on`일 때만, 꺼져 있으면 404) → 계정이 없는 옛 데모 구매자 `[{buyer_id, display_name, age_band}]` 최대 60명. 시험용 로그인 화면(`/dev/buyers`)이 쓴다.
- 구매자 경로(`POST /orders`, `POST /orders/{id}/payments`, `.../payments/toss/prepare`, `POST /payments/toss/confirm`, `POST /orders/{id}/cancel`, `POST·GET /claims`, `POST·GET /inquiries`, `GET /inquiries/{id}`, `POST /inquiries/{id}/feedback`, `POST·GET /ideas`, 쿠폰을 쓸 때의 `POST /cart/price`, `GET·PATCH /users/{buyer_id}`)는 **로그인 토큰이 구매자를 정한다**. 본문·쿼리의 `buyer_id`는 선택이고, 보내면 토큰의 구매자와 같아야 한다(다르면 403 `BUYER_MISMATCH`). 남의 주문을 결제·취소하려 하면 없는 주문과 같은 404다.
  - `admin` 토큰은 `buyer_id`를 함께 보내 구매자를 대신할 수 있다(가상 주문 스크립트·운영자, `buyer_id`가 없으면 422 `BUYER_REQUIRED`). 다른 서비스 토큰은 403.
  - 토큰이 없으면 401 `LOGIN_REQUIRED`(`legacy`에서는 옛 동작대로 요청의 `buyer_id`). 알 수 없는·끊긴 토큰은 어느 모드에서나 401 `UNAUTHORIZED`.
- 주문 조회 `GET /orders`·`GET /orders/{id}`: 구매자 토큰이면 본인 주문만(남의 주문은 404, 다른 `buyer_id`를 물으면 403), 서비스 토큰·연결 키면 전체, 토큰이 없으면 `SHOP_ORDERS_PUBLIC_READ=on`(기본)일 때 지금처럼 전체(AXcellent 재무·고객지원이 이렇게 읽는다)·`off`면 401. 알 수 없는 토큰은 401.
- `GET /api/shop/users`(목록)는 `admin` 토큰만(`legacy`에서 토큰 없이 부르면 옛 동작).
- 내 정보(구매자 로그인 토큰만, 없으면 401 `LOGIN_REQUIRED`, 서비스 토큰은 403):
  - `GET /api/shop/customers-export?cursor=&limit=` (데이터·관리자 토큰만, `limit` 1~1000 기본 500) → `{data: {customers: [{buyer_id, joined_at, updated_at, attributes, tenant_id}], next_cursor, has_more, collected_at, tenant_id}}` (AXcellent 계약 `shop_customers`). `attributes`는 값이 있는 `age_band`·`gender`·`occupation`만 담는다. `next_cursor`는 늘 마지막 줄의 커서(빈 페이지면 받은 커서)이고 `has_more`가 참인 동안 이어 읽는다. 이름·연락처는 없다. 순서는 변경 순번(change_seq)이라 계약 설명의 updated_at 순서와 같은 뜻(바뀐 차례)이다. `(change_seq, buyer_id)` 순이고(`change_seq`는 가입·프로필 변경 때마다 오르는 번호, 옛 행은 0), 프로필을 고친 구매자는 다음 커서 읽기에 다시 나온다.
  - `GET /api/shop/me` → 사용자 `{buyer_id, display_name, email, age_band, gender, occupation}`.
  - `PATCH /api/shop/me` `{display_name?, age_band?, gender?, occupation?}` → 보낸 칸만 바꾼다(코드값만, null이면 지움, 이름은 지울 수 없다, 칸이 없으면 422). `buyers.updated_at`을 적는다.
  - `POST /api/shop/me/password` `{current, new}` → 현재 비밀번호가 틀리면 401 `INVALID_PASSWORD`, 새 비밀번호는 8~128자(422 `WEAK_PASSWORD`), 비밀번호가 없는 데모 구매자는 409 `NO_PASSWORD`. 바꾸면 **지금 토큰을 뺀 이 구매자의 다른 로그인을 모두 끊는다**.
  - `POST /api/shop/me/withdraw` `{password}` → 회원 탈퇴. 비밀번호가 틀리면 403 `INVALID_PASSWORD`, 데모 구매자는 409 `NO_PASSWORD`. 맞으면 `{withdrawn: true}`이고 계정(이메일·비밀번호 해시·이름)·후기·로그인 토큰을 지우고 연령대·성별·직업군을 비운 뒤 `change_seq`를 올린다(`retention.remove_account`, `purge-buyer`와 같은 함수). 주문·클레임 기록과 구매자 가명 ID는 법정 보존(5년)으로 남는다.
- 구매 후기(`reviews` 표, 후기 ID `rev_0001`~):
  - `POST /api/shop/products/{product_id}/reviews` `{order_id, rating(1~5), body(10~1000자)}` (구매자 토큰) → 201. 본인 주문이고 그 상품이 품목에 있고 `delivered_at`이 있어야 한다(아니면 403 `NOT_REVIEWABLE`). 주문·상품마다 한 번(409 `REVIEW_EXISTS`).
  - `GET /api/shop/products/{product_id}/reviews?page=&size=` (공개, 최신순, `size` 기본 10·최대 50) → `{data: {summary: {count, avg, by_rating: {"1".."5"}}, items: [{review_id, rating, body, created_at, author_label}]}, meta: {total, page, size}}`. `author_label`은 연령대가 있으면 `10대`~`40대`·`50대 이상`, 없으면 `구매자`. 구매자 ID·이름은 싣지 않는다.
  - `GET /api/shop/me/reviews` (구매자 토큰) → 내 후기(`product_id`·`order_id` 포함). `GET /api/shop/me/reviewable` → 아직 후기를 안 쓴 배송 완료 품목 `[{order_id, product_id, title}]`.
  - 보유기간 삭제로 구매자 행이 지워지거나, 회원 탈퇴·삭제 요청 집행 때 그 구매자의 후기도 같이 지운다.

| 환경변수 | 기본값 | 뜻 |
| --- | --- | --- |
| `SHOP_BUYER_AUTH` | `required` | `required`: 구매자 경로는 로그인 토큰(또는 `admin` 대신하기)만. `legacy`: 토큰이 없으면 요청의 `buyer_id`를 믿는다(옛 동작, 시험·전환 기간용). 모르는 값은 `required` |
| `SHOP_BEST_CACHE_S` | `300` | 공개 베스트 결과를 캐시하는 초(0이면 캐시 안 함) |
| `SHOP_ORDERS_PUBLIC_READ` | `on` | `off`면 토큰 없는 주문 조회를 401로 막는다 |
| `SHOP_DEMO_LOGIN` | (꺼짐) | `on`일 때만 비밀번호 없는 데모 로그인(`POST /auth/demo-login`, `POST /sessions`)이 열린다. 배포에는 넣지 않는다 |
| `SHOP_SESSION_DAYS` | 30 | 로그인 토큰 유효 일수(1~3650, 그 밖의 값은 기본값) |
| `SHOP_ID_START` | 1 | 새로 생기는 번호표(주문·결제·환불·반품 등)의 첫 번호. 새 DB로 바꿀 때 예전 DB 번호와 겹치지 않게 크게 준다(예: `1000000` → `ord_1000000`). 이미 있는 번호표는 그대로 이어 가서 기존 DB에는 영향이 없다. 상품 번호는 시드가 정해서 그대로 |
| `SHOP_TRUST_PROXY` | (꺼짐) | `on`이면 로그인·가입 시도 제한이 `X-Forwarded-For`의 마지막 값(게이트웨이가 붙인 손님 주소)을 쓴다. 게이트웨이 뒤에서만 켜고, 그때는 판매 서버 포트를 밖에 열지 않는다(헤더를 꾸밀 수 있다) |

가상 주문 스크립트(`backend/scripts/shop_traffic.py`)는 `required`에서 `admin` 토큰 + `buyer_id` 또는 회원 로그인으로 바꿔야 한다. 가상 회원 하루 흐름(`scripts/gen_synthetic_daily.py`, 4-5)은 처음부터 `admin` 토큰 + `buyer_id`로 부른다. 후기(`POST /products/{id}/reviews`)도 다른 구매자 경로처럼 `buyer_id`를 받는다(로그인 토큰이면 비우거나 같아야 하고, `admin`은 대신하기).

## 4. 의존
`ax-contracts`, `ax-agent-template`. 다른 부서 폴더를 import하지 않는다.
## 4-1. 보유기간 삭제 (retention)
개인정보처리방침 제4조의 보유기간을 코드로 옮긴 것이다(`retention.py`). **삭제는 기본 dry-run(건수만 센다)이고, 실제 삭제(apply)는 사람이 켠다.** 5년은 방침 제4조 기준이며, 모의 거래에도 적용할지(제4조 ⑦)는 미확정이라 설정값이다. 다른 사람의 기록을 지우는 외부 엔드포인트는 없고, 삭제 요청의 집행은 사람 승인 뒤 운영자가 CLI로만 한다(회원 본인의 탈퇴 `POST /api/shop/me/withdraw`만 비밀번호 확인 뒤 같은 계정 삭제를 쓴다).

| 환경변수 | 기본값 | 뜻 |
| --- | --- | --- |
| `SHOP_RETENTION_TRANSACTION_DAYS` | 1825 (5년) | 주문·품목·결제·환불·반품·쿠폰의 보유 일수(기준은 각 행의 `created_at`) |
| `SHOP_RETENTION_OUTBOX_DAYS` | 90 | **발송이 끝난**(`sent_at` 있음) outbox 이벤트의 보유 일수 |
| `SHOP_RETENTION_BUYER_DAYS` | 90 | 구매자 행의 보유 일수(남은 주문·쿠폰이 없을 때만) |
| `SHOP_RETENTION_ENABLED` | 1 | 하루 한 번 도는 백그라운드 작업 켜기/끄기(`0`·`false`·`off`·`no`면 끔) |
| `SHOP_RETENTION_APPLY` | (없음) | `1`일 때만 백그라운드가 실제로 지운다. 아니면 건수만 로그 |

- 일수는 양의 정수(상한 36500)만 받고, 비었거나 정수가 아니거나 0 이하면 기본값이다.
- 만료 기준은 "기준 시각 이전 또는 같음"(`created_at <= now − N일`)이다.
- 백그라운드 작업은 **DB가 파일일 때만**(`SHOP_DB_PATH`) 앱 시작 때 켜지고 하루에 한 번 돈다. `:memory:`면 건너뛴다. 실패해도 앱은 계속 돌고 로그에는 예외 종류만 남는다.

| 대상 | 지우는 조건 | 남기는 것 |
| --- | --- | --- |
| `refunds`·`returns` | `created_at`이 5년 이상 지남 | 5년 안의 행 |
| `payments` | 5년 이상 지났고 남은 환불이 없음 | 환불이 남아 있는 결제 |
| `order_items`·`orders` | 주문이 5년 이상 지났고 남은 결제·환불·반품이 없음(품목은 주문과 함께) | 더 새로운 결제·환불·반품이 붙은 주문 |
| `coupons` | `created_at`이 5년 이상 지남 | 5년 안의 쿠폰 |
| `outbox_events` | 발송 완료(`sent_at` 있음) + 90일 이상 | **발송 안 된 이벤트는 몇 년이 지나도 남김** |
| `buyers` | 90일 이상 지났고 남은 주문·쿠폰이 없음 | 5년 보관 주문·쿠폰이 있는 구매자(외래키) |
| 시드 데모 구매자(`seed.SEED_BUYER_IDS`) | **절대 지우지 않음.** 그 구매자의 주문·쿠폰도 대상에서 제외 | 전부 |

- 지우는 순서는 자식 → 부모(refunds, returns, payments, order_items, orders, coupons, outbox_events, buyers)이고 한 트랜잭션이다.
- 지우기 전에 **일별 집계**만 `retention_daily_stats`(`stat_date`·`kind`·`rows_count`·`amount_sum`·`tenant_id`)에 더한다. 구매자·주문 번호 같은 식별 정보는 넣지 않는다. 테이블은 `CREATE TABLE IF NOT EXISTS`라 기존 DB 파일에도 시작할 때 만들어진다.
- 보고서·로그·CLI 출력에는 건수만 있다(구매자 ID·금액 없음).

CLI(`SHOP_DB_PATH`가 가리키는 파일 DB에서 실행, 기본은 dry-run):
```
python -m ax_shop_api.retention run [--apply]
python -m ax_shop_api.retention purge-buyer <buyer_id> [--apply] [--include-retained]
```
- `run`: 만료분 정리. `--apply`가 없으면 건수만 센다. `--apply`면 만료된 로그인 토큰(`sessions.expires_at`이 지난 것)도 지운다(건수에는 넣지 않는다).
- `purge-buyer`: **정보주체 삭제 요청의 집행부.** AI의 1차 본인 확인과 사람의 최종 판단이 끝난 뒤에만 운영자가 쓴다.
  - 기본: 법정 보존 대상(주문·품목·결제·환불·반품·쿠폰, 5년)을 **남기고**, 그 구매자 관련 **발송 완료 outbox 이벤트**(페이로드의 `buyer_id` 또는 그 구매자 주문 번호로 찾음)와, 남은 주문·쿠폰이 없을 때의 구매자 행만 지운다. 발송 안 된 이벤트는 남긴다.
  - `--include-retained`: 그 구매자의 주문·품목·결제·환불·반품·쿠폰까지 지운다(프로젝트 종료 또는 법적 보존 비적용이 확정됐을 때만).
  - 어느 쪽이든 **계정 쪽 기록은 늘 지운다**: 후기·로그인 토큰·계정을 지우고, 구매자 행이 남더라도 연령대·성별·직업군을 비우고 `change_seq`를 올린다(고객 내보내기에 다시 나온다). 건수는 `reviews`·`sessions`·`accounts` 키로 따로 보인다(일별 집계는 남기지 않는다).
  - 시드 구매자는 거부(종료 코드 2), 없는 구매자는 종료 코드 1.
- 종료 코드 2: `:memory:` DB에서는 실행하지 않는다.
## 4-2. 토스페이먼츠 테스트 결제 (`toss.py`)
사람이 쇼핑몰 화면에서 결제할 때만 토스 테스트 결제를 쓴다. 자동 트래픽(`scripts/shop_traffic.py`)과 `POST /orders/{id}/payments`는 그대로 가짜 결제다. 화면(shop-web)은 다른 레포다.

| 환경변수 | 기본값 | 뜻 |
| --- | --- | --- |
| `PAYMENT_PROVIDER` | `fake` | `fake` 또는 `toss_test` |
| `TOSS_SECRET_KEY` | (없음) | 토스 **테스트** 시크릿 키. `backend/.env`에만 둔다(`run_all.sh`가 넘긴다). 커밋·출력 금지 |

- `toss_test`인데 키가 없거나 `test_`로 시작하지 않으면(라이브 키 `live_…` 포함) **가짜 결제로 동작**하고 시작할 때 경고 한 줄을 남긴다. 키 값은 응답·로그·경고 어디에도 나가지 않는다(앞 몇 글자도).
- 흐름: 화면이 `payment-config`로 방식을 보고 → `prepare`로 `toss_order_id`·금액·주문명을 받아 토스 결제위젯을 연다 → 위젯 성공 리다이렉트의 `paymentKey`·`orderId`·`amount`로 `confirm`을 부른다 → 서버가 금액을 시도 금액과 비교한 뒤 토스 승인 API(`POST https://api.tosspayments.com/v1/payments/confirm`, Basic 인증 `시크릿키:`, `Idempotency-Key` = `toss_order_id`, 타임아웃 10초)를 부른다.
- 결과 기록은 가짜 결제와 같은 함수(`Shop._record_payment`)다: `payments` 행, 주문 상태(`paid`/`payment_failed`), `payment.succeeded`/`payment.failed` 이벤트, 결제 모니터 표본.
  - 200 → 성공. 토스 `method`를 `method_code`로: 카드 `card`, 계좌이체 `transfer`, 간편결제 `easy_pay`, 휴대폰 `phone`, 그 밖 `other`.
  - 4xx → `PG_DECLINED`(5xx로 세지 않음), 토스 `code`는 `failure_detail`.
  - 5xx·타임아웃·연결 실패 → `PG_UNAVAILABLE`(5xx로 셈), `failure_detail`은 토스 `code` 또는 `TIMEOUT`·`CONNECT_ERROR`.
  - 걸린 시간은 실제로 재서 지연 표본(`latency_ms`)으로 쓴다.
- 장애 주입(SHOP-06): `failure`면 **토스를 부르지 않고** `PG_UNAVAILABLE`(5xx, `failure_detail` `FAULT_INJECTED`)로 실패시켜 `shop.degraded`(5xx 비율)에 들어간다. `latency`는 가짜 결제처럼 값으로만 더한다(실제로 기다리지 않음).
- 같은 시도를 동시에 두 번 승인해도 토스는 한 번만 부른다(프로세스 안 잠금 + 토스 `Idempotency-Key`).
- 표: `payment_attempts`(새 표: `toss_order_id`·`order_id`·`amount`·`status` `prepared`/`confirmed`/`failed`·`payment_id`·`created_at`·`updated_at`), `payments`에 `provider`(`fake`·`toss_test`, 옛 행은 `fake`)·`pg_payment_key`·`failure_detail` 칸 추가(시작할 때 `ADD COLUMN`).

## 4-3. 실제 책 상품 넣기
도서관 정보나루(data4library.kr) Open API의 인기대출도서·도서 상세로 장르별 실제 책(약 30권씩, 13개 도서 장르)을 받아 상품으로 넣는다. 문구 45개(필기구·노트·다꾸 15개씩)는 공개 자료가 없어 일반 이름으로 만든 데모 상품이다.
- 키: `backend/.env`에 `DATA4LIBRARY_API_KEY=...`(커밋 금지). 스크립트는 그 한 줄만 읽고 키를 출력·저장하지 않는다.
- 목록 다시 받기(가끔, 사람이): `cd backend && uv run python scripts/seed_books.py` → `shop-api/src/ax_shop_api/catalog/books.json`을 다시 쓴다. 1초에 한 번 이하로 부르고 전체 호출은 `--max-calls`(기본 600)까지, 응답은 `backend/.cache/data4library/`에 캐시해 이어서 한다. KDC → 장르 표는 스크립트 안에 있다(분류 못 하는 책은 뺀다).
- DB에 넣기: `SHOP_DB_PATH=... uv run python -m ax_shop_api.load_catalog [--dry-run]`. 같은 ISBN의 책·같은 이름의 문구는 건너뛰어 여러 번 돌려도 같고, 시드 상품 prd_0001~0008과 `seed.py` 기본 동작은 그대로다. 한 트랜잭션이지만 서버를 멈춘 때(또는 주문이 없는 조용한 때) 돌린다. `books.json`이 없으면 책은 건너뛰고 문구만 넣는다.
- 출처: 책 제목·저자·출판사·분류·소개·표지는 **도서관 정보나루(국립중앙도서관)**에서 왔다(쇼핑몰 하단과 책 상세의 표지 아래에 출처 표시). **가격·재고는 API에 없어 ISBN으로 정한 데모 값**이다.
- 표지(`cover_url`): 상품의 선택 칸(nullable TEXT, 옛 DB는 시작할 때 칸이 더해진다)이고 상품 API 응답(`GET /api/shop/products`·`/products/{id}`·`/products/best`)에 늘 실린다(없으면 `null`). 정보나루 응답의 `bookImageURL` **주소만** 적는다. 쓸 때 서버가 확인한다: `http://`·`https://`로 시작하고 호스트가 있으며 500자 이하, 공백·제어 문자·따옴표·꺾쇠·사용자 정보(`@`) 없음. 관리 API(`POST`·`PATCH /api/shop/products`)는 어긋나면 422, `load_catalog`·스크립트는 어긋난 주소를 버린다(표지 없음). http를 https로 바꾸지 않는다(그림 서버가 https를 받는지 모르므로 받은 그대로).
  - **보여 주기만(핫링크) 하는 까닭**: 정보나루 이용 조건상 표지 그림은 비영리 서비스 화면에 **원래 주소로 보여 주는 것만** 된다. 그림 파일을 내려받기·저장·재배포하거나 AI에 넣으면 안 되고, 도서 정보에는 출처를 밝혀야 한다. 그래서 서버는 그림을 받지도, 프록시·캐시하지도 않고 주소만 돌려준다. 웹(`Cover`)이 `<img src=cover_url loading="lazy" referrerPolicy="no-referrer">`로 바로 불러오고, 실패하면 `public/covers/<id>` 사진 → 색 제목 카드 순서로 돌아간다. 이 주소로 그림을 받아 학습·분석 데이터에 넣지 않는다.
  - 새로 받기: `seed_books.py`가 다음 실행부터 `books.json`의 책마다 `cover_url`을 적는다(주소가 없거나 어긋나면 칸 없음).
  - 이미 넣은 상품 채우기: `cd backend && SHOP_DB_PATH=... uv run python scripts/fill_cover_urls.py --dry-run`으로 몇 개 찾는지 본 뒤 `--dry-run`을 빼고 돌린다. ISBN이 있고 `cover_url`이 빈 상품마다 도서 상세(`srchDtlList`)를 불러 `products.cover_url` 한 칸만 채운다(`updated_at` 등 다른 칸은 그대로, 한 트랜잭션). `--books-json PATH`면 DB 대신 그 파일의 책마다 `cover_url` 한 칸만 더한다. `--dry-run`은 DB를 읽기 전용으로 열고 캐시도 쓰지 않는다. 키는 `seed_books.py`와 같이 찾고(`DATA4LIBRARY_API_KEY` → `--env-file` → `backend/.env`) 출력·저장하지 않는다. 1초에 한 번 이하(`--min-interval`), 전체 호출 `--max-calls`(기본 400), 응답 캐시는 `seed_books.py`와 같은 `backend/.cache/data4library/`를 쓴다. `--overwrite`면 이미 있는 주소도 다시 받는다. 호출 상한·키 거부로 멈추면 그때까지 찾은 것만 쓰고 2로 끝난다.

## 4-4. 가상 회원·주문 넣기
AXcellent 1단계 연결 시험용 데이터다(엄격한 AI 평가용이 아니다). 페르소나 15개(공식 통계 기반)로 가상 회원(기본 5만 명, 10%는 기간 중 가입)을 만들고, 회원마다 구매 습관대로 90일 동안 산 결과를 DB에 넣는다. 하루 주문 수는 정해 두지 않고 회원 각자의 주문(요일·시간대·월·월급날 26일·추석 주·AI 도서 트렌드)이 더해진 결과다(기본 5만 명이면 하루 약 80건, 90일 약 7,000건).
- 실행: `cd backend && SHOP_DB_PATH=... uv run python scripts/gen_synthetic.py --dry-run`으로 요약을 먼저 보고, `--dry-run`을 빼고 넣는다. 옵션: `--members`(기본 50000), `--seed`(기본 고정 값, 같은 DB·같은 시드면 같은 데이터), `--end`(기본 어제, 한국 시간), `--trend-start`(기본 끝 날짜 − 38일), `--out`(기본 `~/catbooks-synthetic-out`, 저장소 밖), `--replace`. DB가 `:memory:`면 `--dry-run`만 된다.
- 넣는 동안: `shop_traffic`·매일 모드(4-5)와 AXcellent의 읽어 가기(sales-export·customers-export·문의함·클레임함)를 멈추고, 되도록 shop-api도 멈춘다. 한 트랜잭션이 계산부터 쓰기까지 DB 쓰기 잠금을 쥔다(5만 명 기준 약 5~10초, 그동안 주문·가입이 막힌다).
- 넣는 것: 회원(`buy_100001`~, 이메일 `@synthetic.catbooks.invalid`, 로그인할 수 없는 비밀번호 해시 하나)·주문·품목·결제(일부 거절·결제 전 취소)·발송·도착 시각·클레임(배송 완료 주문만, 모두 `received`)·문의(`[종류] …` 제목, 사진 없음)·후기. 상품은 DB에서 판매 중인 것을 장르(`genre_code`)로 고르고 재고를 줄인다(재고가 모자라면 그 줄은 빼고 품절로 센다). 장르가 붙은 책이 거의 없으면 경고하고 장르 없는 책으로 대신 채운다(그러면 장르별 결과가 보이지 않으니 `load_catalog`를 먼저 돌린다).
- 넣지 않는 것: outbox 이벤트(서비스 경로를 쓰지 않고 행을 직접 넣는다. 넣기 전후 건수가 다르면 되돌린다), 환불(고객지원 판정 뒤 정상 경로로 생긴다), 결제된 주문의 취소, 마지막 1시간 안의 기록(결제 감시가 반응하지 않게).
- 순서: AXcellent의 판매 기록 수집(sales-export)을 켜기 **전에** 넣는다. 이미 수집을 시작했다면 넣은 뒤 AXcellent 쪽 데이터를 초기화한다(과거 시각의 주문이 수집 커서 뒤에 들어간다). 회원은 `change_seq`가 새로 올라 고객 내보내기가 다시 읽는다.
- 다시 넣기: 가상 회원이 있으면 멈춘다. `--replace`는 가상 회원 구간의 행과 딸린 행(주문·결제·클레임·문의·후기 등)만 한 트랜잭션에 지우고 재고를 되돌린 뒤 다시 넣는다. 실제 가입자는 이 구간 아래 번호를 받아(`synthetic_ids`) 겹치지 않고, 그 주문·클레임·후기는 건드리지 않는다. 재고 되돌리기는 더하기다(가상 주문이 가져간 수량을 지금 재고에 더한다. 그 사이 사람이 재고를 고쳤으면 그 값에 더해진다). `--replace`를 한 뒤에는 AXcellent 쪽 데이터를 꼭 초기화한다(지운 주문·회원이 AXcellent에 남는다). 회원의 `change_seq`는 지우기 전 최댓값보다 크게 다시 매긴다.
- 그 밖: 끝 날짜 직전 주문은 결제됨·미도착으로 남는다(정답지 `totals.paid_not_delivered_at_cutoff`). 문의 중 개인정보 삭제 요청 5~7건은 고객지원에서 사람에게 넘어간다. 기간 안에 가상이 아닌 주문·클레임이 있으면 경고하고 정답지 `existing_non_synthetic`에 적는다(정답지 숫자에는 들어가지 않는다). 장르 없는 책·실제 클레임이 있는 책에는 정답용 클레임을 심지 않는다. `shop_traffic`은 `GET /api/shop/users?real_only=true`로 시드·실제 가입자만 골라 주문한다(가상 회원을 쓰는 서버에서는 `shop_traffic`을 끄고 4-5의 매일 모드를 쓴다).
- 정답지: `answer_key.json`(일·주·장르 합계, 트렌드 배수, 40~50대 남성 몫, 심어 둔 클레임 책·작은 묶음 등)과 `member_personas.csv`는 `--out`에만 쓴다. 매일 모드가 이어 쓰는 값(장르별 인기 순위·상품별 클레임·심은 책)은 `backfill_extra.json`에 따로 쓴다(정답지·DB 내용은 그대로). 커밋 전에 쓰고, 쓰다가 실패하면 DB도 되돌린다. CRX-001 판정은 끝 날짜 기준이다. **로컬 전용**이고 저장소·DB에 넣지 않는다(저장소 안에 둘 때를 위해 `synthetic-out/`은 `.gitignore`).

## 4-5. 가상 회원 하루 흐름 (매일 모드, `shop_traffic` 대신)
되채우기(4-4)가 넣은 가상 회원이 오늘도 사게 하는 오래 도는 프로세스다. `shop_traffic`의 무작위 주문을 대신한다.
- 실행: `cd backend && SHOP_ADMIN_TOKEN=... uv run python scripts/gen_synthetic_daily.py`(같은 것: `scripts/gen_synthetic.py --daily`). 옵션: `--interval-s`(기본 600, 몇 초마다 그때까지 된 몫을 넣을지), `--max-per-tick`(기본 10, 한 번에 새로 넣는 주문 상한), `--seed`(하루 계획의 시드, 기본은 되채우기 시드), `--out`(기본 `~/catbooks-synthetic-out`, 되채우기 정답지가 있는 폴더), `--url`(기본 `SHOP_API_URL` 또는 `http://127.0.0.1:8010`), `--once`(한 번만 돌고 끝, cron·시험용), `--restock-lead-h`(기본 24, 음수면 재고를 채우지 않는다), `--no-deliver-real`(사람 주문의 발송·도착 표시를 끈다). 토큰은 `SHOP_ADMIN_TOKEN`, 없으면 `AX_AGENT_TOKENS`의 `admin` 값(또는 `admin` 서비스의 `write` 연결 키)이다. 값은 로그에 남기지 않는다.
- 멈추는 경우: 관리자 토큰이 없거나(1), `--out/answer_key.json`(되채우기 정답지)이 없거나, 판매 서버에 가상 회원이 없거나, 정답지로 다시 만든 회원과 DB 회원의 연령대·성별이 다르면(2) 시작하지 않는다. 맞춰 보는 회원 중 삭제 요청으로 지워졌거나(404) 속성이 비워진 회원은 건너뛰고, 한 명이라도 맞으면 된다. 같은 `--out`에 이미 다른 매일 모드가 돌면(운영체제 파일 잠금 `--out/daily.lock`) 멈춘다(3).
- 하루 계획: 한국 시간으로 날이 바뀐 뒤 첫 차례에 그날 계획을 세운다. 되채우기와 같은 회원(정답지 설정·시드로 다시 만든다)이 같은 식으로 산다: 회원마다 포아송 주문 수(월·요일·월급날 26일·추석 주 효과, 주말형 10대), 시간대, 장르 가중치, AI·IT 트렌드 반응. 트렌드 곡선(`trend_multiple`)은 기간 뒤에도 이어져 최고점(×4.1) 뒤 2주 반감으로 평소(×1)로 돌아온다. 결제 전 취소 1.8%·첫 결제 거절 2.5%(뒤에 다시 결제·취소·그대로)도 같은 비율이다. 같은 (시드, 날짜, 상품 목록)이면 같은 계획이다. 되채우기가 어제까지인 첫날 중간에 켜면 이미 지난 시각의 몫을 버리지 않고 남은 하루(길어도 12시간)에 고르게 나눠 넣는다(`deferred`). 그날 합계는 맞고 첫날의 시간대 모양만 평평해진다(가짜 매출 하락을 막는다). 그 밖의 날에 상태 파일 없이 하루 중간에 켜면 지난 몫은 넣지 않는다(`before_start`).
- 조금씩 넣기: 차례마다 계획 시각이 지난 주문만 넣는다(기본 10분마다 0~2건쯤). 꺼졌다 켜지면 남은 몫을 이어 넣되 한 번에 `--max-per-tick`건까지만, 계획 시각(첫날 나눠 넣는 몫은 새 시각)보다 6시간 넘게 늦으면 넣지 않고 놓친 것(`missed`)으로 센다. 보내던 중(`placing`)이던 주문도 같고, 결제 단계가 남은 주문(`open`)은 남은 단계를 버린다(`abandoned`, 결제 대기로 남는다).
- 판매 서버 오류: 401·403·408·429·5xx와 연결 오류는 끝난 것으로 적지 않고 다음 차례에 다시 한다(주문·결제·취소·결제 실패 문의·발송·도착·클레임·후기·문의·재고 보충 모두). 404·409·422만 서버가 거부한 것으로 적는다(주문은 `dropped`, 발송·도착은 다시 시도하지 않음).
- 사람 주문과 같은 길: 주문·결제·취소·클레임·문의·후기를 공개 API로 넣는다. 그래서 outbox 이벤트(`order.created`·`payment.*`·`order.cancelled`)는 서버의 기존 코드가 주문·결제와 같은 트랜잭션에 쓰고, 서버의 발송기(요청마다 `flush`, 주기 루프의 `drain`)가 AXcellent로 보낸다. outbox 행을 따로 쓰지 않는다.
- 왜 HTTP인가(같은 프로세스에서 서비스 코드를 부르지 않는 까닭): ① 가짜 결제사·장애 주입·결제 설정 버전·결제 감시(`Monitor`)는 shop-api 프로세스 메모리에 있다. 다른 프로세스에서 `Shop`을 만들면 outbox는 같은 코드로 쓰이지만 장애를 켜도 가상 결제는 실패하지 않고 감시가 표본을 못 봐 `shop.degraded` 시연이 가상 주문으로 되지 않는다. HTTP면 옛 `shop_traffic`처럼 장애가 가상 결제를 실패시킨다. ② `Db`는 연결 하나를 프로세스 안 잠금으로 감싸고 WAL·busy timeout을 따로 두지 않는다(파이썬 기본 5초). 다른 프로세스가 같은 파일에 쓰면 짧은 트랜잭션이라도 서버 요청이 `database is locked`를 만날 수 있다. ③ 다른 프로세스가 쓴 미발행은 주기 루프의 `drain`만 보내 요청 경로처럼 바로 나가지 않는다. 매일 모드는 DB 파일을 열지 않는다.
- 본인 확인: `SHOP_BUYER_AUTH=required`에서도 `admin` 토큰이 `buyer_id`를 대신한다(3-1). 가상 회원 비밀번호는 아무도 모르는 값이라 로그인하지 않는다.
- 상품 고르기: 되채우기 `Shelf`와 같은 가중치 모양(재고 비례·순위·트렌드 때 AI 제목 쏠림)이지만 매일 모드만 두 가지가 다르다(되채우기 결과는 바이트까지 그대로). ① 장르 안 인기 순위는 되채우기 순위(되채우기가 `--out/backfill_extra.json`에 적는다)를 그대로 잇고, 그때 없던 상품은 시드·상품 번호 해시로 정한 자리에 끼운다. 상품이 늘거나 빠져도 남은 상품끼리의 순서가 바뀌지 않는다(`backfill_extra.json`이 없는 옛 되채우기는 모두 해시 순위). ② 재고가 0인 상품은 계획·바꾸기에서 고르지 않는다.
- 유입 출처: 매일 모드가 넣는 주문마다 주문 API의 `src_source`·`src_medium`·`src_campaign`을 채운다. 평소 몫은 데이터팀 가정 비율 그대로 직접 `direct/none` 40%·네이버 검색 `naver/search` 25%·유튜브 `youtube/video` 15%·인스타그램 `instagram/social` 10%·기타 `other/referral` 10%다. AI·IT 트렌드 때는 유튜브 가중치에 `1 + 0.3 × (트렌드 배수 − 1)`(`TREND_SOURCE_LIFT` = 0.3, 배수는 그날 `trend_multiple`, 최고점 ×4.1)을 곱하고 나머지와 합이 1이 되게 다시 나눈다(최고점이면 유튜브 약 25%, 나머지는 서로의 비율을 지킨 채 줄어든다). `src_campaign`은 트렌드 단계가 `rising`·`peak`·`decay`인 날의 유튜브·인스타그램 주문에만 `ai_it_trend`로 붙고 나머지는 비운다. 페르소나별 기울기는 두지 않는다(그날 합계가 위 비율에 그대로 수렴해 정답지로 쓰기 쉽다). 출처는 (시드, 날짜, 순번)의 따로 된 난수열로 정해 계획을 세울 때 상태 파일의 줄(`src`)에 적으므로 장바구니·결제 계획은 바뀌지 않고, 다시 켜거나 다시 보내도 같은 값을 보낸다(출처 없이 세운 옛 상태 파일의 줄은 넣기 직전에 같은 규칙으로 정해 먼저 적는다). 되채우기 주문에는 출처가 없다(되채우기 결과는 바이트까지 그대로).
- 품절: 차례마다 상품 목록(재고)을 읽고, 재고가 모자란 줄은 같은 장르의 다른 상품으로 바꾸거나(세 번까지) 뺀다. 다 빠지면 그 주문은 넣지 않는다(`dropped`). 그 사이 사람이 사서 409 `OUT_OF_STOCK`이면 한 번 다시 고른다. 재고는 서버가 0 아래로 내리지 않는다.
- 재고 보충: shop_traffic `StockWatch`와 같은 기준·양이다. 판매 중 상품의 재고가 `SHOP_TRAFFIC_LOW_STOCK`(기본 10) 아래로 내려간 것을 처음 본 뒤 `--restock-lead-h`(기본 24)시간이 지나면 관리 API(`PATCH /products/{id}`)로 `SHOP_TRAFFIC_REFILL_TO`(기본 200)개로 채운다(입고에 걸리는 시간 흉내라 트렌드 책 품절·재입고 문의가 잠깐 보인다). 본 시각은 `--out/daily_stock.json`.
- 그 뒤의 일: 차례마다 최근 30일 주문(결제→도착 최대 약 6일 + 추석 연휴 밀림 + 후기 14일보다 길다)을 읽어 결제된 가상 주문(되채우기의 미도착 주문 포함)을 결제 12~30시간 뒤 발송, 20~44시간(늦은 배송은 60~120시간, 트렌드 최고점에서 늘어난다) 뒤 도착으로 관리 API에 적는다(시각이 지난 것만, 되채우기 끝보다 앞 시각으로는 적지 않는다). 매일 모드가 도착시킨 주문에 클레임(3%)·후기(품목의 7%)를, 매일 모드가 넣은 주문에 문의(배송·영수증·삭제 요청)를 되채우기와 같은 문장·비율로 낸다. 결제 장애로(계획한 거절이 아닌데) 실패한 회원의 30%는 결제 실패 문의를 남긴다. 계획은 주문 번호로 정해 늘 같고, 한 일은 `--out/daily_followups.json`에 적어 두 번 내지 않는다. 사람(가상이 아닌) 주문에는 옛 `shop_traffic`과 같은 시각(결제 12~30시간 뒤 발송, 그 뒤 24~48시간 뒤 도착, 주문 번호로 정한 값)으로 발송·도착만 적는다(판매자 정산이 보류되지 않게, `--no-deliver-real`로 끈다). 사람 주문에 클레임·후기·문의를 내지 않는다.
- CRX-001 막기: 되채우기 `_CrxGuard`와 같은 생각이다. 클레임 기록(상품·시각·수량·사유)을 `daily_followups.json`에 최근 30일만 두고(처음에는 `backfill_extra.json`의 되채우기 클레임으로 채운다), 판매 수는 최근 30일 결제 수량(사람 주문 포함)으로 센다. 심지 않은 책(되채우기가 "걸려야 함"으로 심은 책만 뺀다. "걸리면 안 되는" 책은 막는다)이 기준(판매 ≥5·클레임 수량/판매 ≥10%·클레임 ≥2, 또는 불량 클레임 ≥3)에 걸리게 될 클레임은 단순 변심으로 바꾸고, 그래도 걸리면 내지 않는다(`claim_changed_crx`·`claim_skipped_crx`로 센다). 사람 클레임은 세지 않는다(API로 읽지 않는다).
- 다시 켜도 안전: 날마다 계획·진행을 `--out/daily_state/<날짜>.json`에 적는다(다른 이름에 쓰고 fsync한 뒤 바꿔 끼운다). 상태 파일이 깨져 있으면 `.corrupt-<시각>`으로 옮기고 로그를 남긴 뒤 새로 이어 간다(그날 계획을 다시 세워도 멱등 키라 이미 넣은 주문을 두 번 넣지 않는다. 다른 줄로 다시 고르게 되면 서버가 409로 막아 `dropped`로 센다). 주문은 `synthetic-daily:<날짜>:<순번>` 멱등 키로 보내고 보낼 줄을 먼저 적어 두므로, 보낸 뒤 적기 전에 꺼져도 다시 보내면 서버가 처음 주문을 돌려준다. 결제는 서버의 주문 상태(결제 수)를 보고 다음 단계를 정해 두 번 하지 않는다. 두 번째 프로세스는 잠금 파일로 멈춘다.
- 정답지: 하루가 끝나면(다음 날 첫 차례, 거절 뒤 다시 결제처럼 남은 일이 있으면 끝난 뒤) `--out/daily_answer_key.jsonl`에 그날 한 줄을 더한다: 계획(주문 수, 페르소나별, 장르별 수량, 결제 전 취소, 첫 결제 거절, 출처별 수 `by_source`·몫 `source_shares`·캠페인별 수 `by_campaign`), 그날의 가정 출처 몫(`source_mix`), 트렌드 단계(`before`·`rising`·`peak`·`decay`·`baseline`)·배수·날 효과, 실제(넣은 수, 상태별, 장르별 수량, 출처별 수·몫·캠페인별 수, 품절로 뺀·바꾼 줄, 결제 실패 문의), 그날 한 발송·도착·클레임·후기·문의 수. 날짜마다 한 줄이고(이미 적힌 날은 다시 적지 않는다), 쓰다 끊긴 줄은 읽을 때 건너뛰고 그 뒤에 이어 붙이지 않는다. 로컬 전용이고 저장소·DB에 넣지 않는다.
- 하지 않는 것: 새 가상 회원 가입, 환불(고객지원 판정 뒤 정상 경로), 결제된 주문 취소.
- 서버에서 서비스로 돌리기(예: systemd, 경로·사용자는 서버에 맞춘다. 토큰은 저장소 밖 환경 파일에만 둔다):

  ```ini
  [Unit]
  Description=Catbooks synthetic daily traffic
  After=network-online.target

  [Service]
  WorkingDirectory=/srv/catbooks/backend
  EnvironmentFile=/srv/catbooks/backend/shop-api/.env
  ExecStart=/usr/local/bin/uv run python scripts/gen_synthetic_daily.py --interval-s 600
  Restart=always
  RestartSec=30

  [Install]
  WantedBy=multi-user.target
  ```

  `shop_traffic`은 완전히 끈다(`run_all.sh`의 `SHOP_TRAFFIC`를 켜지 않고 `SHOP_TRAFFIC_ORDERS=0` 배송 표시 모드도 띄우지 않는다). 매일 모드가 재고 보충과 사람 주문의 발송·도착 표시까지 맡는다. 둘이 함께 돌면 시드·실제 가입자 주문이 섞이고 가상 주문의 늦은 배송을 shop_traffic이 먼저 적을 수 있다.
- 서버 순서: 새 DB(시드) → `load_catalog`(실제 책) → `scripts/clean_seed.py`(4-6) → `gen_synthetic.py`(되채우기) → 매일 모드.

## 4-6. 시드 데모 지우기 (`scripts/clean_seed.py`)
실제 책·가상 회원으로 바꾼 서버에서 시드 상품 prd_0001~0008과 시드 구매자 buy_0001~0030을 지운다.
- 실행: `cd backend && SHOP_DB_PATH=... uv run python scripts/clean_seed.py --dry-run`으로 건수를 본 뒤 `--dry-run`을 빼고 지운다(`--db`로 파일을 줄 수도 있다). 한 트랜잭션이고(`--dry-run`은 지워 본 뒤 되돌린다), DB가 `:memory:`면 멈춘다. 시드가 아닌 판매 중 상품이 하나도 없으면(아직 `load_catalog`를 안 돌렸으면) 팔 상품이 없는 쇼핑몰이 되므로 멈춘다(`--force`로 넘긴다). 되도록 shop-api를 멈추고 돌린다. 순서: `load_catalog` → `clean_seed` → 되채우기 → 매일 모드.
- 지우는 것: 시드 구매자와 그 주문(시험 주문 `ord_1000000` 등 포함)·품목·결제·환불·반품(+`return_sync`)·토스 결제 시도·가짜 결제사 기록·후기·클레임·문의·아이디어·계정·로그인 토큰·쿠폰, 그리고 시드 상품. 지우는 주문 중 취소되지 않은 주문이 가져간 재고는 남는 상품에 되돌린다.
- 남기는 것: 판매자 sel_0001~0003(실제 책이 붙어 있고, 판매자가 있으면 `seed()`가 다시 넣지 않는다), 가상 회원·실제 가입자의 모든 행, outbox 이벤트(보유기간 삭제가 치운다). 가상·실제 주문이나 후기가 가리키는 시드 상품은 지우지 않고 판매 중지(`active = 0`)만 한다. 그래서 되채우기 **전에** 돌리는 것이 깔끔하다.
- 지운 뒤: 새 가입자는 시드 번호를 다시 받지 않는다(`buy_0031`부터, `accounts._next_buyer_id`). 보유기간 삭제의 시드 예외(`seed.SEED_BUYER_IDS`)는 없는 ID를 가리킬 뿐이라 그대로 둔다. 데모 로그인(`SHOP_DEMO_LOGIN=on`)의 구매자 목록에는 시드가 없고 `buy_0001` 로그인은 404다. `shop_traffic`은 고를 구매자가 없으면 1분마다 대기 로그만 남긴다. AXcellent 쪽에 이미 읽어 간 시드 구매자·주문은 남으니 필요하면 AXcellent 쪽 데이터를 초기화한다.

## 4-7. 덧붙임 정답지 (`scripts/synthetic_key_extra.py`)
데이터팀이 따로 요청한 정답(장르 묶음 구매·재구매 회원의 이탈)을 판매 DB에서 다시 세어 `--out/answer_key_extra.json`에 쓴다. 되채우기 정답지(`answer_key.json`)·`member_personas.csv`·DB는 바꾸지 않는다(DB는 읽기 전용으로 연다).
- 실행: `cd backend && SHOP_DB_PATH=... uv run python scripts/synthetic_key_extra.py --dry-run`으로 요약을 보고, `--dry-run`을 빼고 쓴다. 옵션: `--db`(기본 `SHOP_DB_PATH`), `--out`(기본 `~/catbooks-synthetic-out`, 되채우기 정답지가 있는 폴더. 없으면 멈춘다), `--as-of`(아래 "지금" 기준 시각, ISO `...Z`), `--min-buyers`(기본 20, lift 상위에 넣는 쌍의 최소 회원 수). 언제 다시 돌려도 되고, 같은 DB면 같은 결과다(실행 시각을 쓰지 않는다).
- 대상: 가상 회원(`synthetic_ids.is_synthetic`)의 결제된 주문(성공한 결제가 있는 주문. 뒤의 환불·클레임과 상관없이 산 것으로 센다). 사람 주문은 세지 않는다.
- 묶음(`bundles`): 되채우기 기간(한국 시간 `window_start` 0시 ~ `cutoff_utc`) 안에 같은 회원이 산 장르의 쌍이다(같은 주문이 아니어도 된다). N = 기간 안에 결제한 가상 회원 수, P(A) = A 장르를 산 회원 / N, `support` = P(A∧B), `lift` = P(A∧B) / (P(A)·P(B)), `confidence_a_to_b` = P(B|A). 모든 쌍(`pairs`)과 lift 상위 10(`top_by_lift`, 회원 `--min-buyers`명 이상), support 상위 10(`top_by_support`)을 적는다. 장르 없는 상품의 줄은 빼고 센다(`untagged_order_genres_skipped`).
- 재구매·이탈(`repeat_churn`): 기준 시각까지 결제 주문이 2건 이상인 회원 중 기준 시각 전 60일 안에 결제 주문이 없는 회원의 수·몫(`churn_share`). 기준은 되채우기 끝(`window_end`, `cutoff_utc`)과, 그 뒤 가상 주문(매일 모드)이 있으면 "지금"(`now`, 기본은 DB의 마지막 가상 결제 주문 시각, `--as-of`로 바꾼다)이다. 90일 되채우기만 있으면 끝 기준 이탈 회원은 기간 첫 30일에만 산 재구매 회원이다.
- 정답지는 **로컬 전용**이다(저장소·DB에 넣지 않는다).

## 5. 하지 않는 것
- 판단·분석 문장을 만들지 않는다 (이벤트에는 사실만 담는다)
- 다른 서비스의 DB를 읽지 않는다. 판매 사이트 DB를 다른 서비스가 직접 읽게 하지 않는다 (API로만)
- 별도 HTTP 이벤트 엔드포인트를 만들지 않는다 (`events.publish`만)
- 결제된 주문을 환불 API 없이 취소하지 않는다
- 개인정보를 저장하지 않는다 (구매자는 가명 ID만)
## 6. 다음 단계
- 토스 결제 환불·취소 API 연동. 지금 환불(`POST /payments/{id}/refund`)은 토스 결제여도 장부 기록만 한다.
- 토스 웹훅. 서버(251)가 사내망이라 토스가 부를 수 없어 받지 않는다(승인 결과는 `confirm` 응답으로만 안다).
- 보유기간 삭제(retention)가 `payment_attempts`를 아직 지우지 않는다(금액·주문 번호만 있고 개인정보는 없다).
- 시험 줄 3 (개발 AI 흐름 시험)
