"""판매 서버 시험 공통 설정.

기존 시험은 구매자 흐름의 업무 규칙(주문·문의·클레임 등)을 보는 것이라 본인 확인을 옛 동작
(`SHOP_BUYER_AUTH=legacy`: 토큰이 없으면 요청의 `buyer_id`를 믿는다)으로 둔다.
본인 확인 시험(`test_buyer_auth.py`)은 각 시험에서 `required`로 바꾼다.
"""

import pytest


@pytest.fixture(autouse=True)
def _legacy_buyer_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOP_BUYER_AUTH", "legacy")
