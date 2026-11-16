"""상품 장르 코드 목록. 코드 목록은 이 파일 한 곳에만 둔다.

화면·검색·장르 목록 경로·판매 집계가 모두 여기를 가져다 쓴다. 코드는 영문 소문자이고 상품당
하나다. 실제 책 데이터를 넣을 때 도서관 분류(KDC)를 이 목록으로 바꿔 넣는다. 순서는 화면에
보여 주는 순서다(도서 13개, 문구 3개). 목록을 바꾸는 일은 추가만 한다(코드를 지우거나 이름을
바꾸면 이미 저장된 값과 쓰는 쪽이 깨진다).
"""

GENRES: dict[str, str] = {
    # 도서
    "novel": "소설",
    "essay": "시·에세이",
    "business": "경제경영",
    "self_help": "자기계발",
    "humanities": "인문",
    "society": "사회·정치",
    "history": "역사",
    "science": "과학",
    "it": "AI·IT",
    "language": "외국어",
    "kids": "어린이",
    "comics": "만화",
    "hobby": "여행·취미",
    # 문구
    "writing": "필기구",
    "notebook": "노트",
    "deco": "다꾸",
}


def normalize_genre(value: str | None) -> str | None:
    """장르 코드를 확인한다. 비었으면 None(모름), 목록에 없으면 ValueError."""
    if value is None:
        return None
    code = value.strip()
    if not code:
        return None
    if code not in GENRES:
        raise ValueError(f"genre_code는 {', '.join(GENRES)} 중 하나여야 한다")
    return code
