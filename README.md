# catbooks-demo

캣북스(데모 고객사) 쇼핑몰. 판매 서버(`backend/shop-api`)와 쇼핑몰 화면(`frontend/shop-web`), 한 포트 게이트웨이(`frontend/nginx`),
시연용 장애 스위치·가상 주문 스크립트를 담는다.

AXcellent와의 경계: AXcellent는 고객사가 열어 준 API로만 연결한다. 읽기가 기본이고, 쓰기는 안전 관문을 거친 것만
(환불, 클레임 결과, 쿠폰 발급, 배너 게시·종료) 받는다.

출처: Axcellent-bs dev `d052b6a4`, ax-company-hq/ax-frontend dev `57b30f3`에서 가져왔다. `backend/shop-api`만 이력을 살렸다.

| 폴더 | 내용 |
| --- | --- |
| `backend/shop-api/` | 판매 서버 (FastAPI, 8010) |
| `backend/contracts/openapi/shop-api.json` | 판매 서버 OpenAPI 고정본. CI가 코드와 같은지 검사한다 |
| `backend/vendor/` | AXcellent 공통 패키지 임시 사본 ([vendor/README.md](backend/vendor/README.md)) |
| `backend/scripts/` | `shop_traffic.py`(가상 주문·배송 표시·재고 보충), `shop_fault.sh`(결제 장애 넣기·빼기) |
| `backend/infra/fault-injection/` | 결제 장애 주입·해제 스크립트(표준 라이브러리만) |
| `frontend/shop-web/` | 쇼핑몰 화면 (React·Vite, 3000) |
| `frontend/nginx/` | 게이트웨이: `/` 쇼핑몰, `/shop-api/` 판매 서버 |

## 실행

```bash
# 판매 서버
cd backend
uv sync
uv run uvicorn ax_shop_api.main:app --port 8010
uv run pytest -q

# 쇼핑몰 화면
cd frontend
cp .env.example .env
npm install
npm run dev:shop     # http://localhost:3000
```

장애 넣기: `SHOP_ADMIN_TOKEN=<관리자 토큰> bash backend/scripts/shop_fault.sh failure` (해제는 `clear`).

## 연결 키 (AXcellent가 판매 서버를 부를 때)

판매 서버가 AXcellent 설치·서비스마다 연결 키를 발급한다. AXcellent는 `Authorization: Bearer <키>`로 부른다.

```
SHOP_CLIENT_KEYS="<설치>/<서비스>:<키>:<read|write>,..."
# 예: ax-251/inquiry:<키>:write,ax-251/claims:<키>:write,ax-local/inquiry:<키>:read
```

- 서비스 이름은 AXcellent 서비스 이름(`inquiry`·`claims`·`reviews`·`marketing`·`infra`·`data`·`finance`·`orchestrator`)이다.
  API별 허용 목록은 부서 이름으로 되어 있고, 서비스는 원래 부서로도 인정한다(`inquiry` → `support`).
- `write`는 주 담당 설치(251) 한 곳만 준다. 다른 설치(로컬 등)는 `read`로 준다. `read` 키는 GET·HEAD만 된다.
- 키는 24자 이상. 만들기: `python -c "import secrets; print('sk_' + secrets.token_urlsafe(32))"`.
  `.env`에만 두고 채팅·PR에 올리지 않는다.
- 전환 기간에는 AXcellent 에이전트 토큰도 받는다. 모두 연결 키로 옮기면 `SHOP_AGENT_TOKENS=off`로 끈다.

## AXcellent로 이벤트 보내기

판매 서버 이벤트(주문·결제·반품·장애 `shop.degraded`/`shop.recovered` 등)는 outbox에 저장한 뒤 AXcellent의
알림 받는 주소(웹훅)로 보낸다. 보내지 못하면 남겨 두고 다음에 순서대로 다시 보낸다.

| 환경 변수 | 뜻 |
| --- | --- |
| `AX_EVENTS_WEBHOOK_URL` | AXcellent 알림 받는 주소 (`.../api/dashboard/events`). 비우면 지금처럼 Redis 이벤트 버스에 직접 넣는다(전환 전까지만) |
| `AX_COMPANY_KEY` | AXcellent가 발급한 회사 키. `Authorization: Bearer`로 보낸다. `.env`에만 둔다 |

## 규칙

- 공개 레포다. 실제 내부 주소·토큰·`.env`·`shop.db`를 커밋하지 않는다.
- `dev`·`main`에 직접 push하지 않고 PR로만 머지한다.
