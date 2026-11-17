"""책 정보(ISBN-13·저자·출판사) 입력 확인. 상품에 붙는 선택 값이고 웹 프레임워크에 의존하지 않는다.

외부 베스트셀러·도서 자료는 ISBN으로 오는 경우가 많아, 값이 있는 상품은 이름을 추정하지 않고
정확히 이을 수 있다.
값을 지어내지 않는다: 시드 상품에는 넣지 않고(NULL), 비어 있으면 모르는 것이다.
"""

from __future__ import annotations

AUTHOR_MAX = 100
PUBLISHER_MAX = 100
DESCRIPTION_MAX = 4000
ISBN_PREFIXES = ("978", "979")


def isbn13_check_digit(first12: str) -> int:
    """ISBN-13의 마지막 확인 숫자. 앞 12자리에 1, 3을 번갈아 곱해 더한 값으로 정한다."""
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(first12))
    return (10 - total % 10) % 10


def normalize_isbn13(value: str | None) -> str | None:
    """하이픈·공백을 없애고 ISBN-13인지 확인한다. 빈 값(None·빈 글)은 None(지움)이다.

    13자리 숫자이고 `978`·`979`로 시작하며 확인 숫자가 맞아야 한다. 아니면 ValueError다.
    """
    if value is None:
        return None
    digits = value.replace("-", "").replace(" ", "")
    if not digits:
        return None
    if len(digits) != 13 or not digits.isdigit():
        raise ValueError("ISBN-13은 하이픈을 뺀 13자리 숫자여야 한다")
    if not digits.startswith(ISBN_PREFIXES):
        raise ValueError("ISBN-13은 978 또는 979로 시작해야 한다")
    if isbn13_check_digit(digits[:12]) != int(digits[12]):
        raise ValueError("ISBN-13의 확인 숫자가 맞지 않는다")
    return digits


def clean_text(value: str | None) -> str | None:
    """앞뒤 공백을 없앤다. 빈 글은 None(지움)이다."""
    if value is None:
        return None
    text = value.strip()
    return text or None
