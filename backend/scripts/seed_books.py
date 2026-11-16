"""캣북스 실제 책 상품 데이터를 도서관 정보나루(data4library.kr) Open API에서 받아 json으로 쓴다.

가끔 사람이 한 번 돌리는 스크립트다. 결과(`shop-api/src/ax_shop_api/catalog/books.json`)는 커밋해서
키 없이도 재현된다. 넣기는 `python -m ax_shop_api.load_catalog`가 한다.

- 키: `DATA4LIBRARY_API_KEY`를 환경 변수에서, 없으면 `--env-file`(여러 번 줄 수 있다), 그다음
  `backend/.env`, `backend/shop-api/.env` 순서로 읽는다. 키는 출력·기록·저장하지 않고 오류 문장에도
  싣지 않는다(요청 주소도 authKey를 뺀 값만 보인다).
- 호출: 인기대출도서 `loanItemSrch`(KDC 대분류·부가기호별)로 장르 후보를 모으고, 고른 책마다
  도서 상세 `srchDtlList`로 책 소개를 받는다. 1초에 한 번 이하, 실패는 간격을 늘려 다시 하고,
  전체 호출 수 상한(`--max-calls`, 기본 600)을 넘지 않는다.
- 캐시: 응답은 `backend/.cache/data4library/`에 저장해 다시 돌리면 이어서 한다
  (키는 저장하지 않는다).
- 가격·재고는 API에 없어 ISBN 해시로 정한 **데모 값**이다(장르별 현실적인 가격대, 100원 단위).
- 표지 그림은 받지도 가리키지도 않는다(쇼핑몰이 그리는 표지를 그대로 쓴다).

실행: `cd backend && uv run python scripts/seed_books.py [--env-file PATH] [--per-genre 30]`
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from ax_shop_api.book_info import AUTHOR_MAX, DESCRIPTION_MAX, PUBLISHER_MAX, normalize_isbn13
from ax_shop_api.genres import GENRES

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILES = (BACKEND / ".env", BACKEND / "shop-api" / ".env")
DEFAULT_CACHE = BACKEND / ".cache" / "data4library"
DEFAULT_OUT = BACKEND / "shop-api" / "src" / "ax_shop_api" / "catalog" / "books.json"
DEFAULT_URL = "http://data4library.kr/api"
KEY_NAME = "DATA4LIBRARY_API_KEY"
SOURCE = "도서관 정보나루(data4library.kr)"
TITLE_MAX = 200

BOOK_GENRES = [code for code in GENRES if code not in ("writing", "notebook", "deco")]

# --- KDC(한국십진분류) → 캣북스 장르 -------------------------------------------------------
# 분류 기호의 앞자리(점 뺀 숫자)로 찾는다. 가장 긴 접두사가 이긴다. 표에 없으면 넣지 않는다(건너뜀).
# 아동 도서(부가기호 첫 자리 7)와 만화는 분류보다 먼저 정한다(`map_genre` 참고).
KDC_PREFIXES: dict[str, str | None] = {
    # 0 총류: 컴퓨터만 쓴다(004 컴퓨터과학, 005 프로그래밍). 나머지(도서관·독서·백과)는 건너뜀
    "004": "it",
    "005": "it",
    # 1 철학: 10~17 철학·사상 → 인문, 18 심리학 · 19 윤리(처세 포함) → 자기계발
    "10": "humanities",
    "11": "humanities",
    "12": "humanities",
    "13": "humanities",
    "14": "humanities",
    "15": "humanities",
    "16": "humanities",
    "17": "humanities",
    "18": "self_help",
    "19": "self_help",
    # 2 종교 → 인문
    "2": "humanities",
    # 3 사회과학: 32 경제(경영 포함) → 경제경영, 325.21x 성공법(자기계발서 대부분) → 자기계발,
    # 나머지(30 통계·31 사회학·33 사회문제·34 정치·35 행정·36 법·37 교육·38 풍속·39 국방)
    # → 사회·정치
    "3": "society",
    "32": "business",
    "32521": "self_help",
    # 4 자연과학 → 과학
    "4": "science",
    # 5 기술과학: 51 의학 → 과학, 59 가정학(요리·살림·육아) → 여행·취미, 나머지 건너뜀
    "51": "science",
    "59": "hobby",
    # 6 예술(미술·사진·음악·공예·스포츠·오락) → 여행·취미, 657 만화 → 만화
    "6": "hobby",
    "657": "comics",
    # 7 언어 → 외국어
    "7": "language",
    # 8 문학: 셋째 자리가 형식이다. 1 시·4 수필·6 서간·7 풍자·8 르포 → 시·에세이, 3 소설 → 소설,
    # 80 문학 일반 → 인문. 2 희곡·5 연설은 건너뜀.
    # (81 한국·82 중국·83 일본·84 영미 … 89 기타 모두 같다)
    "80": "humanities",
    **{f"8{lang}{form}": genre for lang in "123456789" for form, genre in
       (("1", "essay"), ("3", "novel"), ("4", "essay"), ("6", "essay"), ("7", "essay"),
        ("8", "essay"))},
    # 9 역사: 98 지리(여행기·여행 안내) → 여행·취미, 나머지 → 역사
    "9": "history",
    "98": "hobby",
}  # fmt: skip

COMIC_TITLE_HINTS = ("만화", "웹툰", "코믹", "그래픽노블", "그래픽 노블")


def kdc_digits(class_no: str | None) -> str:
    return re.sub(r"\D", "", class_no or "")


def map_genre(
    class_no: str | None,
    addition_symbol: str | None = None,
    title: str | None = None,
    class_nm: str | None = None,
) -> str | None:
    """책 하나의 캣북스 장르 코드. 정할 수 없으면 None(넣지 않는다).

    순서: 657 만화 분류 → 만화, 부가기호 첫 자리 7(아동) → 어린이, 분류 이름·제목에 만화 단서 →
    만화, 그다음 KDC 접두사 표.
    """
    digits = kdc_digits(class_no)
    if digits.startswith("657"):
        return "comics"
    addition = (addition_symbol or "").strip()
    if addition[:1] == "7":
        return "kids"
    hints = f"{class_nm or ''} {title or ''}"
    if any(hint in hints for hint in COMIC_TITLE_HINTS):
        return "comics"
    if not digits:
        return None
    for size in range(len(digits), 0, -1):
        if digits[:size] in KDC_PREFIXES:
            return KDC_PREFIXES[digits[:size]]
    return None


# --- 데모 가격·재고 -------------------------------------------------------------------------
# API에 가격이 없다. ISBN 해시로 장르별 가격대 안에서 정한다(같은 책은 늘 같은 값, 100원 단위).
PRICE_BANDS: dict[str, tuple[int, int]] = {
    "novel": (12000, 19800),
    "essay": (11000, 18000),
    "business": (15000, 25000),
    "self_help": (13000, 19800),
    "humanities": (15000, 28000),
    "society": (15000, 25000),
    "history": (16000, 32000),
    "science": (16000, 30000),
    "it": (22000, 38000),
    "language": (12000, 22000),
    "kids": (9800, 16800),
    "comics": (6000, 12000),
    "hobby": (14000, 24000),
}
STOCK_RANGE = (20, 300)


def _hash_int(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")


def demo_price(isbn13: str, genre: str) -> int:
    low, high = PRICE_BANDS[genre]
    steps = (high - low) // 100 + 1
    return low + (_hash_int(f"price:{isbn13}") % steps) * 100


def demo_stock(isbn13: str) -> int:
    low, high = STOCK_RANGE
    return low + _hash_int(f"stock:{isbn13}") % (high - low + 1)


# --- 키 ------------------------------------------------------------------------------------
def _read_env_file(path: Path) -> str | None:
    """`.env` 파일에서 키 한 줄만 찾는다. 다른 줄은 보지 않는다."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        name, sep, value = line.partition("=")
        if sep and name.strip() == KEY_NAME:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value or None
    return None


def load_key(env_files: list[Path] | None = None) -> str:
    value = os.environ.get(KEY_NAME, "").strip()
    if value:
        return value
    for path in [*(env_files or []), *DEFAULT_ENV_FILES]:
        found = _read_env_file(path)
        if found:
            return found
    raise SystemExit(f"{KEY_NAME}가 없다: 환경 변수나 backend/.env에 넣는다")


# --- 호출 ----------------------------------------------------------------------------------
class ApiError(RuntimeError):
    """API 호출 실패. 문장에는 키도, 키가 든 주소도 싣지 않는다."""


class BudgetExceeded(ApiError):
    pass


HttpGet = Callable[[str, float], bytes]


def urllib_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "catbooks-seed/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 (고정 주소)
        return res.read()


class Client:
    def __init__(
        self,
        key: str,
        base_url: str = DEFAULT_URL,
        cache_dir: Path = DEFAULT_CACHE,
        max_calls: int = 600,
        min_interval: float = 1.0,
        retries: int = 3,
        http_get: HttpGet = urllib_get,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._key = key
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir
        self.max_calls = max_calls
        self.min_interval = min_interval
        self.retries = retries
        self._http_get = http_get
        self._sleep = sleep
        self.calls = 0  # 실제 네트워크 요청 수(캐시 적중은 세지 않는다)
        self.cache_hits = 0
        self._last = 0.0

    def _scrub(self, text: str) -> str:
        return text.replace(self._key, "***") if self._key else text

    def _cache_path(self, api: str, params: dict[str, str]) -> Path:
        canon = urllib.parse.urlencode(sorted(params.items()))
        digest = hashlib.sha1(f"{api}?{canon}".encode()).hexdigest()[:16]
        return self.cache_dir / f"{api}_{digest}.json"

    def get(self, api: str, params: dict[str, str]) -> dict:
        """`api`를 `params`(키 제외)로 부른다. 캐시에 있으면 그 값을 쓴다."""
        path = self._cache_path(api, params)
        if path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))
        shown = f"{api}?{urllib.parse.urlencode(sorted(params.items()))}"
        query = urllib.parse.urlencode({"authKey": self._key, **params, "format": "json"})
        url = f"{self.base_url}/{api}?{query}"
        last_error = "알 수 없음"
        for attempt in range(self.retries + 1):
            if self.calls >= self.max_calls:
                raise BudgetExceeded(f"호출 상한 {self.max_calls}회에 닿았다 ({shown})")
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            try:
                body = self._http_get(url, 20.0)
                data = json.loads(body.decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                reason = getattr(exc, "reason", None) or type(exc).__name__
                last_error = self._scrub(f"연결 실패: {reason}")
            except (ValueError, UnicodeDecodeError):
                last_error = "JSON이 아닌 응답"
            else:
                response = data.get("response") if isinstance(data, dict) else None
                error = response.get("error") if isinstance(response, dict) else None
                if error:
                    # 키 거부·호출 한도 등: 다시 해도 같으므로 멈춘다(캐시에 넣지 않는다)
                    raise ApiError(self._scrub(f"API 오류 ({shown}): {error}"))
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            if attempt < self.retries:
                self._sleep(2.0 * 2**attempt)
        raise ApiError(f"{shown} 호출 실패 ({self.retries + 1}회): {last_error}")


# --- 응답 읽기 ------------------------------------------------------------------------------
def loan_docs(data: dict) -> list[dict]:
    docs = (data.get("response") or {}).get("docs") or []
    return [item.get("doc", item) for item in docs if isinstance(item, dict)]


def detail_book(data: dict) -> dict:
    detail = (data.get("response") or {}).get("detail") or []
    if isinstance(detail, dict):
        detail = [detail]
    for item in detail:
        if isinstance(item, dict):
            return item.get("book", item)
    return {}


_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"[ \t\r\f\v]+")


def clean_description(text: str | None) -> str:
    """HTML 엔티티·태그를 풀고 공백을 정리해 4000자 이하로 자른다."""
    value = html.unescape(html.unescape(text or ""))
    value = _TAG.sub(" ", value)
    value = "\n".join(_SPACE.sub(" ", line).strip() for line in value.splitlines())
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if len(value) > DESCRIPTION_MAX:
        value = value[: DESCRIPTION_MAX - 1].rstrip() + "…"
    return value


def _clip(text: str | None, size: int) -> str:
    value = _SPACE.sub(" ", html.unescape(text or "")).strip()
    return value[:size].rstrip()


def series_key(title: str) -> str:
    """같은 시리즈의 여러 권을 하나로 본다(숫자·기호·공백을 빼고 앞 10자)."""
    base = re.split(r"[:=(]", title)[0]
    return re.sub(r"[\W\d_]+", "", base)[:10]


# --- 후보 모으기 ----------------------------------------------------------------------------
# 장르마다 후보를 찾을 인기대출도서 조건(순서대로, 페이지를 넘기며).
# 같은 조건은 캐시로 한 번만 부른다.
GENRE_SOURCES: dict[str, list[dict[str, str]]] = {
    "novel": [{"kdc": "8"}],
    "essay": [{"kdc": "8"}],
    "business": [{"kdc": "3"}],
    "self_help": [{"kdc": "3"}, {"kdc": "1"}],
    "humanities": [{"kdc": "1"}, {"kdc": "2"}],
    "society": [{"kdc": "3"}],
    "history": [{"kdc": "9"}],
    "science": [{"kdc": "4"}],
    "it": [{"kdc": "0"}, {"kdc": "5"}],
    "language": [{"kdc": "7"}],
    "kids": [{"addCode": "7"}],
    "comics": [{"kdc": "6"}, {"addCode": "7"}, {"kdc": "8"}],
    "hobby": [{"kdc": "6"}, {"kdc": "5"}, {"kdc": "9"}],
}


def collect(
    client: Client,
    period: tuple[str, str],
    per_genre: int,
    page_size: int,
    max_pages: int,
    log: Callable[[str], None],
) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """장르별 후보(인기 순서). 같은 isbn13·같은 시리즈는 한 번만. 건너뛴 이유를 센다."""
    start, end = period
    picked: dict[str, list[dict]] = {g: [] for g in BOOK_GENRES}
    seen_isbn: set[str] = set()
    seen_series: set[str] = set()
    skipped = {"unmapped": 0, "bad_isbn": 0, "duplicate": 0, "series": 0, "genre_full": 0}
    processed: set[tuple] = set()

    def take(doc: dict) -> None:
        try:
            isbn = normalize_isbn13(str(doc.get("isbn13") or ""))
        except ValueError:
            isbn = None
        if not isbn:
            skipped["bad_isbn"] += 1
            return
        if isbn in seen_isbn:
            skipped["duplicate"] += 1
            return
        title = _clip(doc.get("bookname"), TITLE_MAX)
        genre = map_genre(doc.get("class_no"), doc.get("addition_symbol"), title,
                          doc.get("class_nm"))  # fmt: skip
        seen_isbn.add(isbn)
        if genre is None or genre not in picked:
            skipped["unmapped"] += 1
            return
        if len(picked[genre]) >= per_genre:
            skipped["genre_full"] += 1
            return
        key = series_key(title)
        if key and key in seen_series:
            skipped["series"] += 1
            return
        seen_series.add(key)
        picked[genre].append({**doc, "isbn13": isbn, "bookname": title, "_genre": genre})

    for genre in BOOK_GENRES:
        for source in GENRE_SOURCES[genre]:
            for page in range(1, max_pages + 1):
                if len(picked[genre]) >= per_genre:
                    break
                params = {"startDt": start, "endDt": end, "pageNo": str(page),
                          "pageSize": str(page_size), **source}  # fmt: skip
                marker = tuple(sorted(params.items()))
                data = client.get("loanItemSrch", params)
                docs = loan_docs(data)
                if marker not in processed:
                    processed.add(marker)
                    for doc in docs:
                        take(doc)
                if len(docs) < page_size:
                    break
        log(f"  {genre}: 후보 {len(picked[genre])}권")
    return picked, skipped


def build_books(client: Client, picked: dict[str, list[dict]], log) -> tuple[list[dict], int]:
    books = []
    missing = 0
    for genre in BOOK_GENRES:
        for doc in picked[genre]:
            isbn = doc["isbn13"]
            detail = detail_book(client.get("srchDtlList", {"isbn13": isbn, "loaninfoYN": "N"}))
            description = clean_description(detail.get("description"))
            if not description:
                missing += 1
            year = str(doc.get("publication_year") or detail.get("publication_year") or "")
            books.append({
                "isbn13": isbn,
                "title": doc["bookname"],
                "author": _clip(doc.get("authors") or detail.get("authors"), AUTHOR_MAX),
                "publisher": _clip(doc.get("publisher") or detail.get("publisher"), PUBLISHER_MAX),
                "pub_year": int(year[:4]) if year[:4].isdigit() else None,
                "genre_code": genre,
                "kdc": (doc.get("class_no") or detail.get("class_no") or "").strip(),
                "description": description,
                "price": demo_price(isbn, genre),
                "stock": demo_stock(isbn),
            })  # fmt: skip
        log(f"  {genre}: 상세 {len(picked[genre])}권 완료")
    order = {g: i for i, g in enumerate(BOOK_GENRES)}
    books.sort(key=lambda b: (order[b["genre_code"]], b["isbn13"]))
    return books, missing


def write_catalog(path: Path, books: list[dict], period: tuple[str, str], fetched: str) -> None:
    doc = {
        "source": SOURCE,
        "fetched_at": fetched,
        "loan_period": {"start": period[0], "end": period[1]},
        "note": "가격·재고는 API에 없어 ISBN으로 정한 데모 값이다",
        "books": books,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", action="append", type=Path, default=[])
    parser.add_argument("--per-genre", type=int, default=30)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--max-calls", type=int, default=600)
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--end", help="대출 기간 끝 날(YYYY-MM-DD, 기본 어제)")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--candidates-only", action="store_true", help="상세를 부르지 않는다")
    args = parser.parse_args(argv)

    key = load_key(args.env_file)
    end = date.fromisoformat(args.end) if args.end else date.today() - timedelta(days=1)
    period = ((end - timedelta(days=args.days - 1)).isoformat(), end.isoformat())
    client = Client(key, base_url=os.environ.get("DATA4LIBRARY_URL", DEFAULT_URL),
                    cache_dir=args.cache_dir, max_calls=args.max_calls)  # fmt: skip

    def log(msg: str) -> None:
        print(msg, flush=True)

    try:
        log(f"인기대출도서 {period[0]}~{period[1]}")
        picked, skipped = collect(client, period, args.per_genre, args.page_size,
                                  args.max_pages, log)  # fmt: skip
        log(f"건너뜀: {skipped}")
        if args.candidates_only:
            log(f"호출 {client.calls}회, 캐시 {client.cache_hits}회")
            return 0
        books, missing = build_books(client, picked, log)
    except ApiError as exc:
        print(f"멈춤: {exc} (호출 {client.calls}회)", file=sys.stderr)
        return 2
    write_catalog(args.out, books, period, date.today().isoformat())
    log(f"{len(books)}권 → {args.out} (소개 없음 {missing}권)")
    log(f"호출 {client.calls}회, 캐시 {client.cache_hits}회")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
