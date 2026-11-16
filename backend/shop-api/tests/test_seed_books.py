"""실제 책 목록 받기 스크립트(scripts/seed_books.py).

KDC→장르, 데모 가격, 키 숨기기를 본다. 네트워크 없음.
"""

import importlib.util
import json
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

from ax_shop_api.genres import GENRES

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "seed_books.py"
_spec = importlib.util.spec_from_file_location("seed_books", SCRIPT)
assert _spec and _spec.loader
seed_books = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seed_books)

FAKE_KEY = "fake-key-0123456789abcdef"


@pytest.mark.parametrize(
    ("class_no", "addition", "title", "expected"),
    [
        ("813.7", "03810", "소년이 온다", "novel"),
        ("823", "03840", "영미 소설", "novel"),
        ("843.6", "", "", "novel"),
        ("814.6", "03810", "에세이", "essay"),
        ("811.7", "", "시집", "essay"),
        ("816", "", "", "essay"),
        ("325.211", "03320", "역행자", "self_help"),
        ("320.4", "", "", "business"),
        ("325.1", "", "", "business"),
        ("189.2", "", "", "self_help"),
        ("199.1", "", "", "self_help"),
        ("160", "", "", "humanities"),
        ("230", "", "", "humanities"),
        ("911", "", "", "history"),
        ("981.1", "", "여행", "hobby"),
        ("331.5", "", "", "society"),
        ("340.1", "", "", "society"),
        ("420", "", "", "science"),
        ("004.73", "", "", "it"),
        ("005.133", "", "", "it"),
        ("740", "", "", "language"),
        ("657.1", "07810", "", "comics"),
        ("813.8", "77810", "그림책", "kids"),
        ("400", "73400", "", "kids"),
        ("813.7", "", "만화로 보는 소설", "comics"),
        ("594.5", "", "요리", "hobby"),
        ("650", "", "", "hobby"),
        ("011", "", "", None),
        ("029.4", "", "독서법", None),
        ("530", "", "", None),
        ("812", "", "희곡", None),
        ("", "", "", None),
    ],
)
def test_map_genre(class_no, addition, title, expected) -> None:
    assert seed_books.map_genre(class_no, addition, title) == expected


def test_mapped_genres_are_real_codes() -> None:
    assert {g for g in seed_books.KDC_PREFIXES.values() if g} <= set(GENRES)
    assert set(seed_books.PRICE_BANDS) == set(seed_books.BOOK_GENRES)
    assert len(seed_books.BOOK_GENRES) == 13


def test_demo_price_is_deterministic_and_in_band() -> None:
    isbns = [f"97889{n:07d}0" for n in range(200)]
    for genre, (low, high) in seed_books.PRICE_BANDS.items():
        prices = [seed_books.demo_price(i, genre) for i in isbns]
        assert all(low <= p <= high and p % 100 == 0 for p in prices)
        assert len(set(prices)) > 20  # 고르게 퍼진다
        assert prices == [seed_books.demo_price(i, genre) for i in isbns]
    assert seed_books.demo_price("9788936434120", "comics") <= 12000
    stocks = [seed_books.demo_stock(i) for i in isbns]
    assert all(20 <= s <= 300 for s in stocks)
    assert stocks == [seed_books.demo_stock(i) for i in isbns]


def test_load_key_reads_only_that_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATA4LIBRARY_API_KEY", raising=False)
    monkeypatch.setattr(seed_books, "DEFAULT_ENV_FILES", ())
    env = tmp_path / ".env"
    env.write_text('OTHER=1\n# c\nDATA4LIBRARY_API_KEY="abc"\n', encoding="utf-8")
    assert seed_books.load_key([env]) == "abc"
    monkeypatch.setenv("DATA4LIBRARY_API_KEY", "from-env")
    assert seed_books.load_key([env]) == "from-env"
    monkeypatch.delenv("DATA4LIBRARY_API_KEY")
    with pytest.raises(SystemExit) as info:
        seed_books.load_key([tmp_path / "missing.env"])
    assert "abc" not in str(info.value)


def _client(tmp_path: Path, http_get, **kw):
    return seed_books.Client(FAKE_KEY, cache_dir=tmp_path / "cache", min_interval=0,
                             http_get=http_get, sleep=lambda s: None, **kw)  # fmt: skip


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.HTTPError(f"http://x/api?authKey={FAKE_KEY}", 500, "err", {}, None),
        urllib.error.URLError(f"bad host for authKey={FAKE_KEY}"),
        TimeoutError(f"timed out {FAKE_KEY}"),
    ],
)
def test_key_never_in_raised_error(tmp_path: Path, failure) -> None:
    calls = []

    def http_get(url, timeout):
        calls.append(url)
        raise failure

    client = _client(tmp_path, http_get, retries=2)
    with pytest.raises(seed_books.ApiError) as info:
        client.get("loanItemSrch", {"kdc": "8"})
    assert FAKE_KEY not in str(info.value)
    assert f"authKey={FAKE_KEY}" not in str(info.value)
    assert info.value.__cause__ is None or FAKE_KEY not in str(info.value.__cause__)
    assert len(calls) == 3  # 처음 + 다시 2번
    assert FAKE_KEY in calls[0]  # 실제 요청에는 키가 간다
    assert not (tmp_path / "cache").exists()


def test_api_error_body_stops_without_key(tmp_path: Path) -> None:
    def http_get(url, timeout):
        body = {"response": {"error": f"API 활성화 상태가 아닙니다 {FAKE_KEY}"}}
        return json.dumps(body).encode()

    client = _client(tmp_path, http_get)
    with pytest.raises(seed_books.ApiError) as info:
        client.get("srchDtlList", {"isbn13": "9788936434120"})
    assert FAKE_KEY not in str(info.value)
    assert client.calls == 1


def test_cache_is_reused_and_stores_no_key(tmp_path: Path) -> None:
    body = {"response": {"docs": [{"doc": {"isbn13": "9788936434120", "bookname": "책"}}]}}
    calls = []

    def http_get(url, timeout):
        calls.append(url)
        return json.dumps(body).encode()

    client = _client(tmp_path, http_get)
    assert client.get("loanItemSrch", {"kdc": "8"}) == body
    assert client.get("loanItemSrch", {"kdc": "8"}) == body
    assert len(calls) == 1 and client.cache_hits == 1
    for path in (tmp_path / "cache").iterdir():
        assert FAKE_KEY not in path.name
        assert FAKE_KEY not in path.read_text(encoding="utf-8")


def test_budget_cap(tmp_path: Path) -> None:
    client = _client(tmp_path, lambda url, t: b'{"response": {"docs": []}}', max_calls=2)
    client.get("loanItemSrch", {"pageNo": "1"})
    client.get("loanItemSrch", {"pageNo": "2"})
    with pytest.raises(seed_books.BudgetExceeded):
        client.get("loanItemSrch", {"pageNo": "3"})


def test_end_to_end_with_stub(tmp_path: Path) -> None:
    """후보 모으기 → 상세 → 목록 파일. 같은 isbn·같은 시리즈는 한 번, 분류 못 하는 책은 뺀다."""
    docs = [
        {"isbn13": "9788936434120", "bookname": "소년이 온다", "authors": "한강 지음",
         "publisher": "창비", "publication_year": "2014", "class_no": "813.7",
         "addition_symbol": "03810"},
        {"isbn13": "9788936434120", "bookname": "소년이 온다", "class_no": "813.7"},
        {"isbn13": "9788901234564", "bookname": "해리 1", "class_no": "843"},
        {"isbn13": "9788901234571", "bookname": "해리 2", "class_no": "843"},
        {"isbn13": "9788901234588", "bookname": "도서관학", "class_no": "020"},
        {"isbn13": "123", "bookname": "잘못된 ISBN", "class_no": "813"},
    ]  # fmt: skip

    def http_get(url, timeout):
        if "loanItemSrch" in url:
            found = docs if "kdc=8" in url and "pageNo=1" in url else []
            return json.dumps({"response": {"docs": [{"doc": d} for d in found]}}).encode()
        return json.dumps({"response": {"detail": [{"book": {
            "description": "&lt;b&gt;좋은&lt;/b&gt; 책 &amp; 소개"}}]}}).encode()  # fmt: skip

    client = _client(tmp_path, http_get)
    picked, skipped = seed_books.collect(client, ("2025-01-01", "2025-12-31"), 5, 100, 2,
                                         lambda m: None)  # fmt: skip
    assert [d["isbn13"] for d in picked["novel"]] == ["9788936434120", "9788901234564"]
    assert skipped["duplicate"] == 1 and skipped["series"] == 1
    assert skipped["bad_isbn"] == 1 and skipped["unmapped"] == 1
    books, missing = seed_books.build_books(client, picked, lambda m: None)
    assert missing == 0
    assert books[0]["description"] == "좋은 책 & 소개"
    out = tmp_path / "books.json"
    seed_books.write_catalog(out, books, ("2025-01-01", "2025-12-31"), "2026-01-01")
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["source"] == "도서관 정보나루(data4library.kr)"
    assert [b["isbn13"] for b in doc["books"]] == ["9788901234564", "9788936434120"]
    first = doc["books"][1]
    assert first["author"] == "한강 지음" and first["pub_year"] == 2014
    assert first["kdc"] == "813.7" and first["genre_code"] == "novel"
    assert first["price"] == seed_books.demo_price("9788936434120", "novel")
    assert FAKE_KEY not in out.read_text(encoding="utf-8")


def test_cache_scrubs_key_echoed_in_body(tmp_path: Path) -> None:
    key = "k+y/with=chars"

    def http_get(url, timeout):
        quoted = urllib.parse.quote(key, safe="")
        body = {"response": {"request": {"authKey": key, "url": f"?authKey={quoted}"}, "docs": []}}
        return json.dumps(body).encode()

    client = seed_books.Client(key, cache_dir=tmp_path / "cache", min_interval=0,
                               http_get=http_get, sleep=lambda s: None)  # fmt: skip
    data = client.get("loanItemSrch", {"kdc": "8"})
    assert key not in json.dumps(data)
    (path,) = (tmp_path / "cache").iterdir()
    text = path.read_text(encoding="utf-8")
    assert key not in text and urllib.parse.quote(key, safe="") not in text
    assert path.suffix == ".json"


def test_corrupt_cache_is_refetched(tmp_path: Path) -> None:
    calls = []

    def http_get(url, timeout):
        calls.append(url)
        return b'{"response": {"docs": []}}'

    client = _client(tmp_path, http_get)
    client.get("loanItemSrch", {"kdc": "1"})
    (path,) = (tmp_path / "cache").iterdir()
    path.write_text('{"response": ', encoding="utf-8")  # 중간에 끊긴 파일
    assert client.get("loanItemSrch", {"kdc": "1"}) == {"response": {"docs": []}}
    assert len(calls) == 2
    assert json.loads(path.read_text(encoding="utf-8")) == {"response": {"docs": []}}


def test_env_inline_comment_is_stripped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DATA4LIBRARY_API_KEY", raising=False)
    monkeypatch.setattr(seed_books, "DEFAULT_ENV_FILES", ())
    env = tmp_path / ".env"
    env.write_text("DATA4LIBRARY_API_KEY=abc123  # 정보나루 키\n", encoding="utf-8")
    assert seed_books.load_key([env]) == "abc123"
    env.write_text("DATA4LIBRARY_API_KEY='a #b'\n", encoding="utf-8")
    assert seed_books.load_key([env]) == "a #b"
