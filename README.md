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

## 규칙

- 공개 레포다. 실제 내부 주소·토큰·`.env`·`shop.db`를 커밋하지 않는다.
- `dev`·`main`에 직접 push하지 않고 PR로만 머지한다.
