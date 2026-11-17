"""가상 회원(`scripts/gen_synthetic.py`)의 구매자 ID 구간. 구간 정의는 이 파일 한 곳에만 둔다.

가상 회원은 `buy_100001`부터 번호를 받는다. 시드 구매자(`buy_0001`~`buy_0030`)와 실제 가입자는
이 구간 아래에서 번호를 받는다(`accounts._next_buyer_id`가 이 구간을 건너뛴다). 그래서 가상 회원을
다시 넣거나 지워도(`--replace`) 실제 가입자와 겹치지 않는다.
가상 회원 계정의 이메일은 예약 최상위 도메인 `.invalid`(RFC 2606)라 실제로 보낼 수 없다.
"""

from __future__ import annotations

import re

SYNTHETIC_BUYER_FROM = 100_001
EMAIL_DOMAIN = "synthetic.catbooks.invalid"
_BUYER_NUMBER = re.compile(r"^buy_([0-9]{4,9})$")

# SQL에서 가상 구매자 행을 고르는 조건(열 이름 `buyer_id`). 상수 문장이라 그대로 이어 붙여 쓴다
BUYER_WHERE = (
    "(buyer_id GLOB 'buy_[0-9]*' AND length(buyer_id) >= 10 "
    f"AND CAST(substr(buyer_id, 5) AS INTEGER) >= {SYNTHETIC_BUYER_FROM})"
)


def buyer_id(index: int) -> str:
    """0부터 센 가상 회원 순번 → 구매자 ID(`buy_100001`, `buy_100002`, …)."""
    return f"buy_{SYNTHETIC_BUYER_FROM + index}"


def is_synthetic(buyer: str) -> bool:
    found = _BUYER_NUMBER.match(buyer)
    return found is not None and int(found.group(1)) >= SYNTHETIC_BUYER_FROM


def email(buyer: str) -> str:
    return f"{buyer}@{EMAIL_DOMAIN}"
