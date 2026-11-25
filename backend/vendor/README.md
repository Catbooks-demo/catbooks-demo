# vendor

AXcellent 공통 패키지 사본이다. `backend/shared/contracts`(→ `ax-contracts`, Axcellent-bs dev `f6f024c2`)와
`backend/shared/agent-template`(→ `ax-agent-template`, dev `3cf7e758`)에서 `src/`·`pyproject.toml`·`README.md`만 복사했다.
단, `ax_contracts/data_access/shop_sales.py`·`shop_buyer_summaries.py` 두 파일은 dev `0549db24`에서 다시 복사했다
(판매 기록 범위 조회·구매자 요약, 로드맵 3단계 3-3).
`ax-agent-template`은 그 뒤 AXcellent 안의 다른 패키지(`ax-secret-store`)를 쓰게 되어 이 레포만으로는 설치되지 않아서 옛 사본을 둔다.

shop-api가 아직 이 두 패키지를 import해서(이벤트 봉투·스키마, A2A 호출, 이벤트 발행) 임시로 둔다.
판매 서버 → AXcellent 직접 호출(A2A 5개)과 Redis 직접 발행을 "판매 서버에 저장 → AXcellent가 읽어 가기",
"알림 받는 주소(웹훅 + 회사 키)"로 바꾸면 `ax-agent-template`은 빼고, `ax-contracts`는 판매 서버가 쓰는
스키마만 남긴다.

여기서 직접 고치지 않는다. 바꿀 일이 있으면 Axcellent-bs에서 고친 뒤 다시 복사한다.
