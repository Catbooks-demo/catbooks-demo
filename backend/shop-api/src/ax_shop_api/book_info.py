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


# --- 표지 주소(cover_url) ------------------------------------------------------------------
# 도서관 정보나루가 주는 표지 그림 주소(`bookImageURL`)를 그대로 둔다. 제공처 이용 조건상 그림은
# 비영리 화면에 원래 주소로 **보여 주기만** 한다(받기·저장·재배포·AI 학습 금지). 서버는 그림을
# 받지도 대신 보내지도(프록시·캐시) 않고, 웹이 이 주소로 바로 불러온다.
COVER_URL_MAX = 500
_COVER_SCHEMES = ("http://", "https://")


def normalize_cover_url(value: str | None) -> str | None:
    """표지 주소 확인. 빈 값은 None(지움)이다. 어긋나면 ValueError.

    `http://`·`https://`로 시작하고 호스트가 있어야 하며, 500자 이하에 공백·제어 문자·따옴표·
    꺾쇠가 없어야 한다(`javascript:`·`data:` 등은 막는다). http를 https로 바꾸지 않는다(그림
    서버가 https를 받는지 모르므로 받은 그대로 둔다).
    """
    if value is None:
        return None
    url = value.strip()
    if not url:
        return None
    if len(url) > COVER_URL_MAX:
        raise ValueError(f"표지 주소는 {COVER_URL_MAX}자 이하여야 한다")
    if not url.lower().startswith(_COVER_SCHEMES):
        raise ValueError("표지 주소는 http:// 또는 https://로 시작해야 한다")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch in "\"'<>\\`" for ch in url):
        raise ValueError("표지 주소에 공백·제어 문자·따옴표를 넣을 수 없다")
    host = url.split("://", 1)[1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if not host or host.startswith(":"):
        raise ValueError("표지 주소에 호스트가 없다")
    if "@" in host:
        raise ValueError("표지 주소에 사용자 정보(@)를 넣을 수 없다")
    return url


def safe_cover_url(value: object) -> str | None:
    """목록 파일·외부 응답용: 맞지 않는 표지 주소는 오류 대신 버린다(None)."""
    if not isinstance(value, str):
        return None
    try:
        return normalize_cover_url(value)
    except ValueError:
        return None
