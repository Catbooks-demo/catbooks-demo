# 캣북스 실제 서점 만들기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 캣북스 쇼핑몰에 진짜 가입·로그인, 마이페이지, 서점식 첫 화면·목록·상품 상세, 구매 후기를 만들고, 구매자용 경로를 본인 확인으로 막되 AXcellent가 읽는 경로는 그대로 둔다.

**Architecture:** 판매 서버(FastAPI + SQLite)에 회원·세션 저장소(`accounts.py`)와 후기(`reviews.py`)를 더하고, `main.py`에 구매자 확인 의존성 `_buyer()`를 넣어 구매자용 경로에 붙인다. 화면(React + Vite + Tailwind)은 로그인 토큰을 `Authorization: Bearer`로 보내고, 지금 디자인 부품(`bits.tsx`·`Cover`·`btn-*`·`field`)으로 새 화면을 만든다.

**Tech Stack:** Python 3 / FastAPI / pydantic / SQLite / pytest / ruff (backend), React 19 / react-router 7 / Vite / Tailwind 3 / TypeScript (frontend)

**Spec:** `docs/superpowers/specs/2026-11-16-catbooks-real-store-design.md`

## Global Constraints

- 저장소: `Catbooks-demo/catbooks-demo`, 브랜치는 `dev`에서 따고 PR로만 머지한다. 작업마다 PR 하나(작게).
- 판매 서버 응답은 칸·선택값을 **추가만** 한다. 응답 모양이 바뀌면 `cd backend && uv run python -m ax_shop_api.export_openapi contracts/openapi/shop-api.json`으로 고정본을 갱신한다(CI가 비교한다).
- AXcellent가 쓰는 관리자·서비스 경로(`/api/shop/admin/*`, `failed-orders`, `metric-series`, `sales-summary`, `sales-export`, `coupons`, `sellers/*`, `orders/attributed`)의 지금 동작은 바꾸지 않는다.
- `GET /api/shop/products`를 쿼리 없이 부르면 지금과 같은 **전체 목록 배열**을 준다(데이터분석·재무가 이렇게 읽는다).
- 새 의존성을 추가하지 않는다. 비밀번호는 표준 `hashlib.scrypt`.
- 비밀번호·로그인 토큰 원문은 DB·로그·응답(발급 순간 제외)에 남기지 않는다.
- 코드값: 연령대 `10s`·`20s`·`30s`·`40s`·`50s_plus` / 성별 `female`·`male`·`no_answer` / 직업군 `student`·`employee`·`self_employed`·`other`·`no_answer` / 장르 `novel`·`essay`·`business`·`self_help`·`humanities`·`society`·`history`·`science`·`it`·`language`·`kids`·`comics`·`hobby`·`stationery`. 모두 선택 칸이고 비우면 `null`(모름), 채워 넣지 않는다.
- 문의·클레임·아이디어 항목에는 `gender`·직업군을 싣지 않는다(`age_band`만 지금처럼).
- 화면 문구는 해요체, 디자인은 기존 토큰(`brand`·`ink`·`surface`·`accent`)과 `bits.tsx` 부품을 쓴다. 외부 스크립트(CDN) 추가 금지.
- 성별 칸(`buyers.gender`, `PATCH /users/{id}`의 `gender`, `sales-summary`의 `gender` 묶음, 5명 미만 숨김)은 병환 PR #15가 만든다. 이 계획은 그것이 머지됐다고 보고 쓴다. 안 됐으면 Task 1 전에 #15 머지를 기다린다.

## Review Focus

1. **같은 이메일을 대소문자·앞뒤 공백만 다르게 두 번 가입** → 두 번째는 409여야 한다(Task 1).
2. **새 회원 `buyer_id`가 시드 구매자(`buy_0001`~`buy_0030`)나 기존 구매자와 겹침** → 새 ID는 기존 최대 번호 다음이어야 한다(Task 1).
3. **보유기간 삭제(`retention`)가 주문 없는 새 회원을 지움** → 계정이 있는 구매자는 지우지 않아야 한다(Task 1).
4. **배포 환경에서 `SHOP_BUYER_AUTH`·`SHOP_DEMO_LOGIN`을 안 넣음** → 기본값은 각각 `required`·꺼짐이라 남의 이름으로 주문할 수 없어야 한다(Task 2).
5. **AXcellent가 토큰 없이 `GET /api/shop/orders`·`/orders/{id}`·`/products`를 부름** → 지금처럼 200이어야 한다(Task 2, Task 4).

---

### Task 1: 회원·세션 저장소와 가입·로그인 API

**Files:**
- Create: `backend/shop-api/src/ax_shop_api/accounts.py`
- Modify: `backend/shop-api/src/ax_shop_api/db.py` (SCHEMA에 `accounts`·`sessions` 표, `_ADDED_COLUMNS["buyers"]`에 `job_group`·`updated_at`)
- Modify: `backend/shop-api/src/ax_shop_api/main.py` (경로 등록, 입력 모델)
- Modify: `backend/shop-api/src/ax_shop_api/retention.py` (계정 있는 구매자 제외)
- Test: `backend/shop-api/tests/test_accounts.py`

**Interfaces:**
- Produces:
  - `accounts.AGE_BANDS`, `accounts.GENDERS`, `accounts.JOB_GROUPS: tuple[str, ...]`
  - `hash_password(password: str) -> str` / `verify_password(password: str, stored: str) -> bool` (형식 `scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>`, n=2**14, r=8, p=1, 솔트 16바이트, `hmac.compare_digest`)
  - `class Accounts(db: Db)`:
    - `signup(email: str, password: str, display_name: str, age_band: str|None, gender: str|None, job_group: str|None) -> tuple[str, dict]` (토큰, 사용자)
    - `login(email: str, password: str) -> tuple[str, dict]`
    - `demo_login(buyer_id: str) -> tuple[str, dict]` (비밀번호 없는 옛 구매자에게 세션 발급)
    - `logout(token: str) -> None`
    - `resolve(token: str) -> str | None` (구매자 ID, 만료·없음이면 None)
    - `profile(buyer_id: str) -> dict` / `update_profile(buyer_id: str, fields: dict) -> dict` / `change_password(buyer_id: str, current: str, new: str) -> None`
  - 사용자 dict: `{buyer_id, display_name, email, age_band, gender, job_group}` (옛 데모 계정은 `email=None`, `display_name="데모 구매자 NNNN"`)
  - 경로: `POST /api/shop/auth/signup`(201) · `POST /api/shop/auth/login` · `POST /api/shop/auth/logout` · `GET /api/shop/auth/config` → `{demo_login: bool}`
  - 응답 `{data: {token: str, user: {...}}}`, 오류 코드 `EMAIL_TAKEN`(409), `INVALID_LOGIN`(401, 이메일·비밀번호 어느 쪽이 틀렸는지 말하지 않음), `WEAK_PASSWORD`(422)

- [ ] **Step 1: Write the failing tests** (`tests/test_accounts.py`, 기존 시험처럼 `TestClient(create_app(Db(":memory:")))`)

```python
def test_signup_returns_a_token_and_a_new_buyer_after_the_seed_buyers(client):
    res = client.post("/api/shop/auth/signup", json=SIGNUP)
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["token"] and data["user"]["buyer_id"] == "buy_0031"
    assert data["user"]["email"] == "neko@example.com" and data["user"]["job_group"] is None

def test_email_is_unique_regardless_of_case_and_spaces(client):  # Review Focus 1
    client.post("/api/shop/auth/signup", json=SIGNUP)
    again = client.post("/api/shop/auth/signup", json=SIGNUP | {"email": "  NEKO@Example.com "})
    assert again.status_code == 409 and again.json()["error"]["code"] == "EMAIL_TAKEN"

def test_new_buyer_ids_skip_existing_numbers(client, db):  # Review Focus 2
    with db.tx() as c:
        c.execute("INSERT INTO buyers (buyer_id, tenant_id, created_at) VALUES ('buy_0100','shop-demo','2026-11-01T00:00:00Z')")
    assert _signup(client, "a@example.com")["buyer_id"] == "buy_0101"

def test_login_wrong_password_and_unknown_email_look_the_same(client): ...  # 둘 다 401 INVALID_LOGIN
def test_password_is_stored_hashed_and_token_is_stored_hashed(client, db): ...  # accounts·sessions 행에 원문이 없다
def test_logout_revokes_the_token(client): ...  # 로그아웃 뒤 resolve가 None, GET /api/shop/me 401(Task 2에서 경로가 생기면 그 시험으로 옮긴다: 여기선 Accounts.resolve로 확인)
def test_password_rules(client): ...  # 8자 미만·129자 이상 → 422 WEAK_PASSWORD
def test_optional_attributes_accept_only_codes(client): ...  # job_group="학생" → 422, "student" → 저장
def test_demo_login_is_off_by_default(client): ...  # GET /auth/config → demo_login False, SHOP_DEMO_LOGIN=on이면 True
def test_retention_keeps_buyers_with_an_account(db): ...  # Review Focus 3: 주문 없는 오래된 회원은 retention 뒤에도 남는다
```
`SIGNUP = {"email": "neko@example.com", "password": "correct-horse-1", "display_name": "고양이", "agree_terms": True, "agree_privacy": True}`

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && uv run pytest -q shop-api/tests/test_accounts.py`
Expected: FAIL (404 / ImportError)

- [ ] **Step 3: Implement**
  - `db.py` SCHEMA: `accounts(buyer_id TEXT PRIMARY KEY REFERENCES buyers(buyer_id), email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, display_name TEXT NOT NULL, agreed_at TEXT NOT NULL, created_at TEXT NOT NULL)`, `sessions(token_hash TEXT PRIMARY KEY, buyer_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL)` + `idx_sessions_buyer`. `_ADDED_COLUMNS["buyers"]`에 `("job_group","TEXT")`, `("updated_at","TEXT")`.
  - 이메일은 `strip().lower()` 후 저장·비교. 토큰은 `secrets.token_urlsafe(32)`, DB에는 `sha256` 16진. 세션 유효 30일(`SHOP_SESSION_DAYS`).
  - 새 `buyer_id`: 같은 트랜잭션에서 `inquiries.BUYER_ID` 형식인 기존 ID의 최대 번호 + 1 (`buy_%04d`). `counters`의 `buyer`는 쓰지 않는다.
  - 가입 입력: `agree_terms`·`agree_privacy`가 `True`가 아니면 422. `display_name` 1~20자.
  - `demo_login`은 `SHOP_DEMO_LOGIN`이 `on`일 때만 경로가 동작(아니면 404). 계정 없는 구매자만 받는다.
  - `retention.py`의 구매자 삭제 조건에 `AND NOT EXISTS (SELECT 1 FROM accounts a WHERE a.buyer_id = t.buyer_id)`를 더하고, 구매자 삭제 단계에서 `sessions`도 같이 지운다.

- [ ] **Step 4: Run tests and lint**

Run: `cd backend && uv run pytest -q shop-api/tests && uv run ruff check shop-api && uv run ruff format --check shop-api`
Expected: 전부 PASS

- [ ] **Step 5: OpenAPI 고정본 갱신, commit, PR**

```bash
cd backend && uv run python -m ax_shop_api.export_openapi contracts/openapi/shop-api.json
git add -A && git commit -m "feat(shop-api): 이메일·비밀번호 가입·로그인과 로그인 토큰(세션)"
```

---

### Task 2: 구매자 경로에 본인 확인 붙이기

**Files:**
- Modify: `backend/shop-api/src/ax_shop_api/main.py`
- Create: `backend/shop-api/tests/conftest.py`
- Test: `backend/shop-api/tests/test_buyer_auth.py`

**Interfaces:**
- Consumes: `Accounts.resolve`, `Accounts.profile`, `update_profile`, `change_password`, `demo_login` (Task 1)
- Produces:
  - `_buyer(request) -> str` 의존성: 구매자 ID를 돌려준다. 규칙은 아래 표.
  - `GET /api/shop/me` → 사용자 dict / `PATCH /api/shop/me` `{display_name?, age_band?, gender?, job_group?}` / `POST /api/shop/me/password` `{current, new}`
  - 환경변수 `SHOP_BUYER_AUTH` = `required`(기본) | `legacy`, `SHOP_ORDERS_PUBLIC_READ` = `on`(기본) | `off`

| 요청 | `required` | `legacy` |
|---|---|---|
| 구매자 로그인 토큰 | 토큰의 구매자. 본문·쿼리 `buyer_id`가 다르면 403 `BUYER_MISMATCH` | 같음 |
| `admin` 토큰 + `buyer_id` | 그 `buyer_id` | 같음 |
| 토큰 없음 + `buyer_id` | 401 `LOGIN_REQUIRED` | 그 `buyer_id`(지금 동작) |
| 알 수 없는 토큰 | 401 `UNAUTHORIZED` | 401 |

- 적용 경로: `POST /orders`, `POST /orders/{id}/payments`, `.../toss/prepare`, `POST /payments/toss/confirm`, `POST /orders/{id}/cancel`, `POST·GET /claims`, `POST·GET /inquiries`, `GET /inquiries/{id}`, `POST /inquiries/{id}/feedback`, `POST·GET /ideas`, `POST /cart/price`(쿠폰을 쓸 때), `GET·PATCH /users/{buyer_id}`(본인 또는 admin).
- `GET /api/shop/users`(목록)는 `admin` 토큰만. `POST /api/shop/sessions`는 `SHOP_DEMO_LOGIN=on`일 때만 동작하고 `{user, token}`을 준다(`demo_login`).
- 주문 조회 `GET /orders`·`GET /orders/{id}`: 구매자 토큰이면 본인 것만(남의 주문은 404), 서비스 토큰·연결 키면 전체, 토큰이 없으면 `SHOP_ORDERS_PUBLIC_READ=on`일 때 지금처럼 전체·`off`면 401, 알 수 없는 토큰은 401.

- [ ] **Step 1: `tests/conftest.py`** — autouse 픽스처로 `SHOP_BUYER_AUTH=legacy`를 넣는다(기존 시험은 구매자 흐름의 업무 규칙을 보는 것이라 그대로 둔다). 새 본인 확인 시험만 `required`로 바꾼다.

- [ ] **Step 2: Write the failing tests** (`tests/test_buyer_auth.py`, 각 시험에서 `monkeypatch.setenv("SHOP_BUYER_AUTH", "required")`)

```python
def test_buyer_routes_need_a_login_by_default(client): ...  # 토큰 없이 POST /orders {buyer_id} → 401 LOGIN_REQUIRED
def test_the_token_decides_the_buyer(client): ...  # 회원 A 토큰 + body buyer_id=B → 403 BUYER_MISMATCH, buyer_id 없이 → A로 주문
def test_admin_can_act_for_a_buyer(client): ...  # mock:admin + buyer_id → 201
def test_my_inquiries_and_claims_are_only_mine(client): ...  # A 토큰으로 GET /inquiries?buyer_id=B → 403
def test_order_reads_stay_open_for_axcellent(client): ...  # Review Focus 5: 토큰 없이 GET /orders·/orders/{id} → 200 (기본)
def test_order_reads_can_be_closed(client, monkeypatch): ...  # SHOP_ORDERS_PUBLIC_READ=off → 토큰 없이 401, mock:finance → 200
def test_a_buyer_sees_only_own_orders(client): ...  # A 토큰 GET /orders → A 주문만, GET /orders/{B 주문} → 404
def test_unknown_tokens_are_rejected_even_on_open_reads(client): ...  # Bearer 아무거나 → 401
def test_user_list_is_admin_only(client): ...  # 토큰 없이 GET /users → 401, mock:admin → 200
def test_me_reads_and_updates_profile(client): ...  # PATCH /me {job_group:"employee"} → 저장, buyers.updated_at 갱신
def test_change_password_needs_the_current_one(client): ...  # 틀린 current → 401, 맞으면 새 비밀번호로 로그인됨
def test_demo_login_gives_a_token_only_when_enabled(client, monkeypatch): ...  # Review Focus 4: 기본 404, on이면 토큰
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd backend && uv run pytest -q shop-api/tests/test_buyer_auth.py`
Expected: FAIL

- [ ] **Step 4: Implement `_buyer`와 주문 조회 규칙, `/me` 경로** — 토큰 판별 순서: 서비스 토큰·연결 키(`_caller`와 같은 검증, `admin`만 대신하기 허용) → 구매자 세션(`Accounts.resolve`) → 401. 구매자 ID가 쿼리·본문에 있는 경로는 `_buyer` 결과와 비교하고, 없으면 `_buyer` 결과를 쓴다. 기존 입력 모델의 `buyer_id: str`은 `str | None = None`으로 바꾸되 `legacy`에서 비면 422.

- [ ] **Step 5: Run all backend tests, lint, OpenAPI, commit, PR**

Run: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: 전부 PASS

```bash
uv run python -m ax_shop_api.export_openapi contracts/openapi/shop-api.json
git add -A && git commit -m "feat(shop-api): 구매자 경로는 로그인 토큰으로 본인을 확인한다(관리자는 대신 가능, 주문 조회는 당분간 열어 둠)"
```
PR 본문에 운영 설정(`SHOP_BUYER_AUTH`, `SHOP_ORDERS_PUBLIC_READ`, `SHOP_DEMO_LOGIN`)과 "가상 주문 스크립트는 admin 토큰 + buyer_id 또는 회원 로그인으로 바꿔야 한다"를 적는다.

---

### Task 3: 데이터 권한 전용 고객 내보내기

**Files:**
- Modify: `backend/shop-api/src/ax_shop_api/lookups.py`, `main.py`
- Test: `backend/shop-api/tests/test_customer_export.py`

**Interfaces:**
- Consumes: `buyers.updated_at`, `buyers.job_group` (Task 1), `buyers.gender` (#15)
- Produces: `GET /api/shop/customers/export?cursor=&limit=` (권한 `DATA_READERS`, `limit` 1~5000 기본 1000) → `{data: [{buyer_id, age_band, gender, job_group, created_at, updated_at}], meta: {next_cursor}}`. 정렬 `(COALESCE(updated_at, created_at), buyer_id)`, 커서는 마지막 줄의 그 두 값을 이은 불투명 문자열.

- [ ] **Step 1: Write the failing tests**

```python
def test_export_has_no_names_or_contacts(client): ...  # 줄의 키가 위 6개뿐, 회원 email이 응답 어디에도 없다
def test_export_pages_by_cursor_and_returns_changes(client): ...  # limit=10으로 끝까지 → 31명, 회원이 PATCH /me 하면 다음 커서 읽기에 그 회원이 다시 나온다
def test_export_is_data_or_admin_only(client): ...  # 토큰 없음 401, mock:support 403, mock:data 200
```

- [ ] **Step 2: Run → FAIL, Step 3: Implement, Step 4: Run → PASS** (`cd backend && uv run pytest -q shop-api/tests/test_customer_export.py`)

- [ ] **Step 5: OpenAPI 갱신, commit, PR** — `feat(shop-api): 데이터 권한 전용 고객 내보내기(가명 ID와 연령대·성별·직업군)`

---

### Task 4: 상품 장르·소개, 목록 검색·정렬, 베스트

**Files:**
- Create: `backend/shop-api/src/ax_shop_api/genres.py`
- Modify: `db.py` (`_ADDED_COLUMNS["products"]`에 `genre`·`description`), `service.py` (`list_products`, `_product`, `create_product`, `update_product`, `register_seller`), `main.py` (`BookInfoIn`에 `genre`·`description`, 목록 쿼리, 베스트 경로)
- Test: `backend/shop-api/tests/test_product_catalog.py`

**Interfaces:**
- Produces:
  - `genres.GENRES: dict[str, str]` (코드 → 이름, Global Constraints의 14개 순서대로: 소설, 시·에세이, 경제경영, 자기계발, 인문, 사회·정치, 역사, 과학, IT·컴퓨터, 외국어, 어린이, 만화, 여행·취미, 문구)
  - `GET /api/shop/genres` → `[{genre, name, product_count}]`
  - `GET /api/shop/products?q=&genre=&sort=&page=&size=`: `page`를 주면 `{data: [...], meta: {total, page, size}}`(size 1~60 기본 24), 안 주면 **지금처럼 배열 전체**. `q`는 이름·저자·출판사 부분 일치(대소문자 무시), `sort` = `popular`(최근 30일 결제 수량) | `new`(`created_at` 최신) | `price_asc` | `price_desc`, 기본 `popular`.
  - `GET /api/shop/products/best?genre=&days=30&limit=10` (토큰 없음, days 1~93, limit 1~50) → 상품 배열 + 각 `sold_qty`. 결과를 `(genre, days, limit)`별로 300초 캐시(`SHOP_BEST_CACHE_S`).
  - 상품 응답에 `genre`·`description` 추가(없으면 null). `description` 최대 4000자.

- [ ] **Step 1: Write the failing tests**

```python
def test_product_list_without_params_is_unchanged(client): ...  # Review Focus 5: 배열, 시드 8개, 각 항목에 기존 키 그대로
def test_genre_accepts_only_codes(client): ...  # genre="소설" → 422, "novel" → 저장, PATCH null → 지움
def test_search_matches_name_author_publisher(client): ...  # author="한강" 상품을 q="한강"으로 찾음, q="HAN" 대소문자 무시
def test_paging_and_sorting(client): ...  # page=1&size=3 → 3개와 total, sort=price_asc 순서
def test_best_counts_paid_quantity_and_is_public(client): ...  # 결제된 주문만, 토큰 없이 200, genre로 거름
def test_genres_list_counts_active_products(client): ...
```

- [ ] **Step 2: Run → FAIL, Step 3: Implement, Step 4: Run all backend tests → PASS**

- [ ] **Step 5: OpenAPI 갱신, commit, PR** — `feat(shop-api): 상품 장르·소개와 목록 검색·정렬·페이지, 공개 베스트`

---

### Task 5: 구매 후기

**Files:**
- Create: `backend/shop-api/src/ax_shop_api/reviews.py`
- Modify: `db.py` (SCHEMA `reviews`), `main.py`
- Test: `backend/shop-api/tests/test_reviews.py`

**Interfaces:**
- Consumes: `_buyer` (Task 2)
- Produces:
  - 표 `reviews(review_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, product_id TEXT NOT NULL, buyer_id TEXT NOT NULL, rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5), body TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(order_id, product_id))`, ID는 `db.next_id(conn, "review", "rev_")`
  - `POST /api/shop/products/{product_id}/reviews` `{order_id, rating, body}` (구매자 토큰) → 201. 조건: 본인 주문, 그 상품이 주문 품목에 있음, `delivered_at`이 있음. 아니면 403 `NOT_REVIEWABLE`, 이미 썼으면 409 `REVIEW_EXISTS`. `body` 10~1000자.
  - `GET /api/shop/products/{product_id}/reviews?page=&size=` (공개) → `{data: {summary: {count, avg, by_rating: {"1":n..."5":n}}, items: [{review_id, rating, body, created_at, author_label}]}, meta: {total, page, size}}`. `author_label`은 연령대가 있으면 `"20대"`·`"50대 이상"`, 없으면 `"구매자"`. 구매자 ID·이름은 싣지 않는다.
  - `GET /api/shop/me/reviews` (구매자 토큰) → 내 후기 + `product_id`·`order_id`
  - `GET /api/shop/me/reviewable` (구매자 토큰) → 후기를 쓸 수 있는 `[{order_id, product_id, title}]`

- [ ] **Step 1: Write the failing tests**

```python
def test_only_delivered_buyers_can_review_once(client): ...  # 배송 전 403, mark_delivered 뒤 201, 두 번째 409
def test_others_cannot_review_my_order(client): ...  # B 토큰 + A 주문 → 403
def test_public_list_hides_identity(client): ...  # author_label "30대"/"구매자", 응답에 buyer_id·display_name 없음
def test_summary_counts_and_average(client): ...
def test_rating_and_body_limits(client): ...  # rating 0·6 → 422, body 9자 → 422
```

- [ ] **Step 2: Run → FAIL, Step 3: Implement, Step 4: Run all backend tests → PASS**

- [ ] **Step 5: OpenAPI 갱신, commit, PR** — `feat(shop-api): 배송 완료 구매자의 구매 후기`

---

### Task 6: 화면 — 로그인 토큰, 가입·로그인, 시험용 구매자 고르기

**Files:**
- Modify: `frontend/shop-web/src/lib/api.ts` (토큰 저장·헤더, 새 호출), `lib/auth.tsx`, `lib/types.ts`, `components/shop/ShopShell.tsx` (머리글 링크), `App.tsx` (경로)
- Create: `frontend/shop-web/src/pages/Signup.tsx`, `pages/DevBuyers.tsx`
- Modify: `frontend/shop-web/src/pages/Login.tsx` (이메일·비밀번호 로그인으로 바꿈, 지금 구매자 고르기는 `DevBuyers.tsx`로 옮김)

**Interfaces:**
- Consumes: Task 1·2 경로
- Produces:
  - `api.ts`: `storedToken(): string | null`, `storeToken(t: string | null)` (localStorage 키 `cb.session`), `request`가 토큰이 있으면 `Authorization: Bearer`를 붙인다. 401 `UNAUTHORIZED`·`LOGIN_REQUIRED`를 받으면 토큰을 지운다. 새 호출: `signup`, `login`, `logout`, `me`, `updateMe`, `changePassword`, `authConfig`, `demoLogin`.
  - `auth.tsx`: `useAuth()` → `{ user: Me | null, ready: boolean, login(email, pw), signup(input), logout(), demoLogin(buyerId), refresh() }`, `Me = {buyer_id, display_name, email, age_band, gender, job_group}`. 기존 사용처의 `user.id`는 `user.buyer_id`로 바꾼다.
  - 경로: `/login`(로그인), `/signup`(가입), `/dev/buyers`(시험용, `authConfig().demo_login`이 false면 "꺼져 있어요" 안내)
- 가입 화면: 이메일, 비밀번호(8자 이상), 표시 이름, 선택(연령대·성별·직업군: `AGE_BANDS`처럼 `types.ts`에 `GENDERS`·`JOB_GROUPS` 라벨 표), 필수 동의 2개(이용약관·개인정보처리방침 링크), 목적 문구 "연령대·성별·직업군은 추천과 이벤트 분석에 써요. 고르지 않아도 가입할 수 있어요."

- [ ] **Step 1: 구현**
- [ ] **Step 2: Run** `cd frontend && npm run -s typecheck -w shop-web && npm run -s build -w shop-web` → 통과
- [ ] **Step 3: 로컬 확인** — 판매 서버(`SHOP_DEMO_LOGIN=on`)와 화면을 띄워 가입 → 로그아웃 → 로그인 → 주문 → `/dev/buyers`로 옛 계정 로그인을 손으로 해 본다. 콘솔 오류 없음.
- [ ] **Step 4: Commit, PR** — `feat(shop-web): 이메일 가입·로그인과 로그인 토큰, 시험용 구매자 고르기는 /dev/buyers로`

---

### Task 7: 화면 — 마이페이지

**Files:**
- Create: `frontend/shop-web/src/pages/my/MyLayout.tsx` (왼쪽 메뉴 + 위쪽 요약), `pages/my/MyHome.tsx`, `MyClaims.tsx`, `MyInquiries.tsx`, `MyIdeas.tsx`, `MyReviews.tsx`, `MyProfile.tsx`, `MyPassword.tsx`
- Modify: `pages/Orders.tsx`·`pages/OrderDetail.tsx`를 마이페이지 안으로(경로 `/my/orders`, `/my/orders/:id`), `App.tsx`(`/orders`·`/orders/:id`는 `/my/...`로 이동), `ShopShell.tsx`(머리글 "마이페이지"), `components/shop/AgeBandPicker.tsx`는 `MyProfile.tsx`가 쓰는 프로필 폼으로 대체하고 지운다

**Interfaces:**
- Consumes: `useAuth`, `api.me/updateMe/changePassword` (Task 6), 기존 `api.orders`·`myClaims`·`myInquiries`·`myIdeas`, `api.myReviews`·`reviewable` (Task 5 경로, 이 작업에서 `api.ts`에 추가)
- 메뉴(디자인 문서 5.3): 쇼핑(주문·배송 조회, 취소·반품·교환, 장바구니 링크) / 활동(1:1 문의 내역, 고객의 소리, 내가 쓴 후기) / 내 정보(회원정보 수정, 비밀번호 변경, 로그아웃). 요약: 최근 3개월 주문 수, 진행 중 클레임 수, 답변 대기 문의 수. 적립금 자리는 `NotReady`.
- 주문 목록 각 줄: 상태(결제 → 배송 중 → 배송 완료), "후기 쓰기"(작성 가능 품목이 있으면), 기존 반품 신청.
- 로그인 안 했으면 `/login?next=<지금 경로>`로 보낸다. `Support.tsx`의 "문의 내역 조회" `NotReady`는 `/my/inquiries` 링크로 바꾼다.

- [ ] **Step 1: 구현**
- [ ] **Step 2: Run** typecheck·build → 통과
- [ ] **Step 3: 로컬 확인** — 회원으로 주문·문의·의견을 하나씩 남기고 마이페이지 각 메뉴에 보이는지, 다른 회원으로 로그인하면 안 보이는지 확인
- [ ] **Step 4: Commit, PR** — `feat(shop-web): 마이페이지(주문·클레임·문의·의견·후기·회원정보)`

---

### Task 8: 화면 — 첫 화면(C안)과 목록·검색

**Files:**
- Modify: `frontend/shop-web/src/pages/Home.tsx`, `pages/Products.tsx`, `components/shop/ShopShell.tsx`(검색창, 분야 메뉴에 장르), `lib/api.ts`(`genres`, `productsPage`, `best`), `lib/types.ts`(`Product`에 `isbn13`·`author`·`publisher`·`genre`·`description`)
- Create: `frontend/shop-web/src/components/shop/ProductCard.tsx`, `components/shop/ProductRow.tsx`(표지 줄)

**Interfaces:**
- Consumes: Task 4 경로
- 첫 화면: 작은 이벤트 배너(지금 `NoticeBanners`), 베스트 TOP 5(`best?limit=5`)와 새로 나온 책(`products?page=1&size=6&sort=new`)을 나란히, 아래에 장르별 줄(`best?genre=<코드>&limit=6`, 상품이 없는 장르 줄은 숨김).
- 목록: URL 쿼리 `q`, `genre`, `sort`, `page`를 그대로 서버에 넘긴다. 왼쪽(모바일은 위) 장르 메뉴, 정렬 선택, 페이지 넘김, 결과 0건 안내.
- 머리글 검색창: 입력 후 Enter → `/products?q=...`.

- [ ] **Step 1: 구현**
- [ ] **Step 2: Run** typecheck·build → 통과
- [ ] **Step 3: 로컬 확인** — 상품에 장르·저자를 몇 개 넣고(관리자 토큰으로 PATCH) 첫 화면 줄과 검색·정렬·페이지가 동작하는지, 장르 없는 지금 시드에서 빈 줄이 숨는지 확인
- [ ] **Step 4: Commit, PR** — `feat(shop-web): 서점식 첫 화면과 상품 목록 검색·정렬·장르`

---

### Task 9: 화면 — 상품 상세와 후기 쓰기

**Files:**
- Modify: `frontend/shop-web/src/pages/ProductDetail.tsx`, `pages/my/MyReviews.tsx`(작성 가능 목록에서 쓰기), `lib/api.ts`(`reviews`, `writeReview`)
- Create: `frontend/shop-web/src/components/shop/ReviewList.tsx`, `components/shop/ReviewForm.tsx`

**Interfaces:**
- Consumes: Task 4 상품 칸, Task 5 경로, Task 8 `ProductRow`
- 상세 위쪽: 표지 / 장르·제목·저자·출판사·ISBN·판매자 / 별점 평균과 후기 수 / 가격·배송 안내 / 수량·장바구니·바로구매(지금 동작 그대로). 비어 있는 책 정보 칸은 줄째 숨긴다.
- 아래 탭: 책 소개(`description`, 없으면 "소개를 준비하고 있어요"), 구매 후기(`ReviewList`: 별점 분포 막대, 목록, 페이지), 교환·반품 안내(고정 문구, `content/legal/buyer-terms.md`의 해당 조항 요약과 링크). 맨 아래 이 분야 베스트(`best?genre=`, 장르 없으면 숨김).
- 후기 쓰기: 마이페이지 주문 줄·`MyReviews`의 "후기 쓰기"에서 `ReviewForm`(별점 1~5, 10~1000자). 상세 화면에서는 "구매한 분만 쓸 수 있어요" 안내만.

- [ ] **Step 1: 구현**
- [ ] **Step 2: Run** typecheck·build → 통과
- [ ] **Step 3: 로컬 확인** — 주문 → 관리자 배송 완료 처리 → 후기 쓰기 → 상세에 "20대"처럼 보이는지, 두 번째 쓰기가 막히는지 확인
- [ ] **Step 4: Commit, PR** — `feat(shop-web): 상품 상세(책 정보·소개·후기)와 후기 쓰기`

---

## 마무리 (모든 작업 뒤)

- [ ] `README.md`(루트)·`backend/shop-api/README.md`에 새 환경변수(`SHOP_BUYER_AUTH`, `SHOP_ORDERS_PUBLIC_READ`, `SHOP_DEMO_LOGIN`, `SHOP_SESSION_DAYS`, `SHOP_BEST_CACHE_S`)와 새 경로를 적는다(Task 2·4 PR에 같이 넣어도 된다).
- [ ] #소통에 알림: 가상 주문 스크립트 변경 필요(예진), 재무·고객지원 서비스 토큰 붙이기 부탁(태윤·병환), 고객 내보내기 경로(재현).
