"""표지 주소(`cover_url`): 주소 확인, 옛 DB 칸 더하기, 목록 넣기, API, seed_books·fill_cover_urls.

그림은 받지 않고 주소만 다룬다. 네트워크 없음(HTTP는 가짜 함수).
"""

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.book_info import COVER_URL_MAX, normalize_cover_url, safe_cover_url
from ax_shop_api.db import Db
from ax_shop_api.load_catalog import load_catalog
from ax_shop_api.main import create_app
from ax_shop_api.seed import seed

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fill = _load("fill_cover_urls")
seed_books = fill.seed_books  # fill이 쓰는 것과 같은 모듈(같은 예외 클래스)

ADMIN = {"Authorization": "Bearer mock:admin"}
FAKE_KEY = "fake-key-0123456789abcdef"
COVER = "https://image.aladin.co.kr/product/1/2/cover/8936434128_1.jpg"
ISBN_A = "9788936434120"
ISBN_B = "9788901234564"
NEW = {"seller_id": "sel_0001", "name": "새 책", "category_code": "book", "price": 1, "stock": 1}
BAD_URLS = [
    "javascript:alert(1)",
    "data:image/png;base64,AAAA",
    "ftp://example.com/a.jpg",
    "//example.com/a.jpg",
    "https://",
    "https:///a.jpg",
    "https://user@example.com/a.jpg",
    'https://example.com/a.jpg" onerror="x',
    "https://example.com/a b.jpg",
    "https://example.com/" + "a" * COVER_URL_MAX,
]


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


# --- 주소 확인 -----------------------------------------------------------------------------
def test_normalize_cover_url_keeps_http_and_https_as_given() -> None:
    assert normalize_cover_url(f"  {COVER} ") == COVER
    http = "http://bookthumb-phinf.pstatic.net/cover/1/2/a.jpg?type=m1"
    assert normalize_cover_url(http) == http  # https로 바꾸지 않는다
    assert normalize_cover_url("HTTPS://Example.com/a.jpg") == "HTTPS://Example.com/a.jpg"
    assert normalize_cover_url("") is None and normalize_cover_url(None) is None


@pytest.mark.parametrize("bad", BAD_URLS)
def test_bad_cover_urls_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        normalize_cover_url(bad)
    assert safe_cover_url(bad) is None


def test_safe_cover_url_ignores_non_strings() -> None:
    assert safe_cover_url(None) is None and safe_cover_url(123) is None
    assert safe_cover_url(COVER) == COVER


# --- DB ------------------------------------------------------------------------------------
def test_an_old_database_gets_the_cover_url_column(tmp_path: Path) -> None:
    path = str(tmp_path / "old.db")
    Db(path)._conn.close()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE products DROP COLUMN cover_url")
    conn.commit()
    assert "cover_url" not in {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    conn.close()
    db = Db(path)
    seed(db)
    with db.tx() as c:
        assert "cover_url" in {r[1] for r in c.execute("PRAGMA table_info(products)")}
    client = TestClient(create_app(db))
    product = client.get("/api/shop/products").json()["data"][0]
    assert product["cover_url"] is None


# --- load_catalog --------------------------------------------------------------------------
def _book(isbn: str, title: str, **extra) -> dict:
    return {"isbn13": isbn, "title": title, "author": "저자", "publisher": "출판사",
            "pub_year": 2020, "genre_code": "novel", "kdc": "813", "description": "",
            "price": 10000, "stock": 10, **extra}  # fmt: skip


def test_load_catalog_writes_cover_url_and_drops_bad_ones(tmp_path: Path) -> None:
    books = [
        _book(ISBN_A, "표지 있음", cover_url=COVER),
        _book(ISBN_B, "나쁜 주소", cover_url="javascript:alert(1)"),
        _book("9788901234571", "표지 없음"),
    ]
    (tmp_path / "books.json").write_text(json.dumps({"books": books}), encoding="utf-8")
    db = Db(":memory:")
    seed(db)
    load_catalog(db, tmp_path)
    with db.tx() as conn:
        got = dict(conn.execute("SELECT isbn13, cover_url FROM products WHERE isbn13 IS NOT NULL"))
    assert got == {ISBN_A: COVER, ISBN_B: None, "9788901234571": None}
    # 다시 돌려도 이미 있는 상품은 그대로다
    books[2]["cover_url"] = COVER
    (tmp_path / "books.json").write_text(json.dumps({"books": books}), encoding="utf-8")
    load_catalog(db, tmp_path)
    with db.tx() as conn:
        row = conn.execute("SELECT cover_url FROM products WHERE isbn13 = '9788901234571'")
        assert row.fetchone()[0] is None


# --- API -----------------------------------------------------------------------------------
@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def test_api_stores_returns_and_clears_cover_url(client: TestClient) -> None:
    res = client.post("/api/shop/products", json=NEW | {"cover_url": COVER}, headers=ADMIN)
    assert res.status_code == 201, res.text
    pid = res.json()["data"]["product_id"]
    assert res.json()["data"]["cover_url"] == COVER
    assert client.get(f"/api/shop/products/{pid}").json()["data"]["cover_url"] == COVER
    listed = {p["product_id"]: p for p in client.get("/api/shop/products").json()["data"]}
    assert listed[pid]["cover_url"] == COVER
    assert listed["prd_0001"]["cover_url"] is None
    res = client.patch(f"/api/shop/products/{pid}", json={"name": "새 이름"}, headers=ADMIN)
    assert res.json()["data"]["cover_url"] == COVER  # 안 보내면 그대로
    res = client.patch(f"/api/shop/products/{pid}", json={"cover_url": None}, headers=ADMIN)
    assert res.json()["data"]["cover_url"] is None


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "data:image/png;base64,AA", "x" * 600])
def test_api_rejects_bad_cover_url(client: TestClient, bad: str) -> None:
    res = client.post("/api/shop/products", json=NEW | {"cover_url": bad}, headers=ADMIN)
    assert res.status_code == 422
    res = client.patch("/api/shop/products/prd_0001", json={"cover_url": bad}, headers=ADMIN)
    assert res.status_code == 422
    assert client.get("/api/shop/products/prd_0001").json()["data"]["cover_url"] is None


# --- seed_books ----------------------------------------------------------------------------
def test_seed_books_saves_cover_url_only(tmp_path: Path) -> None:
    def http_get(url, timeout):
        return json.dumps({"response": {"detail": [{"book": {
            "description": "소개", "bookImageURL": "javascript:x"}}]}}).encode()  # fmt: skip

    client = seed_books.Client(FAKE_KEY, cache_dir=tmp_path / "cache", min_interval=0,
                               http_get=http_get, sleep=lambda s: None)  # fmt: skip
    picked = {g: [] for g in seed_books.BOOK_GENRES}
    picked["novel"] = [
        {"isbn13": ISBN_A, "bookname": "가", "class_no": "813", "bookImageURL": COVER},
        {"isbn13": ISBN_B, "bookname": "나", "class_no": "813"},  # 상세의 주소는 나빠서 버린다
    ]
    books, _ = seed_books.build_books(client, picked, lambda m: None)
    by_isbn = {b["isbn13"]: b for b in books}
    assert by_isbn[ISBN_A]["cover_url"] == COVER
    assert "cover_url" not in by_isbn[ISBN_B]


# --- fill_cover_urls -----------------------------------------------------------------------
def _detail_stub(calls: list[str]):
    covers = {ISBN_A: COVER, ISBN_B: "javascript:alert(1)"}

    def http_get(url, timeout):
        calls.append(url)
        isbn = url.split("isbn13=")[1][:13]
        book = {"bookname": "책"} | ({"bookImageURL": covers[isbn]} if isbn in covers else {})
        return json.dumps({"response": {"detail": [{"book": book}]}}).encode()

    return http_get


def _fill_client(tmp_path: Path, calls: list[str], dry_run: bool, **kw):
    return seed_books.Client(FAKE_KEY, cache_dir=tmp_path / "cache", min_interval=0,
                             http_get=_detail_stub(calls), sleep=lambda s: None,
                             write_cache=not dry_run, **kw)  # fmt: skip


def _make_db(tmp_path: Path) -> Path:
    path = tmp_path / "shop.db"
    db = Db(str(path))
    seed(db)
    with db.tx() as conn:
        pairs = (("prd_0001", ISBN_A), ("prd_0002", ISBN_B), ("prd_0003", "9788901234571"))
        for pid, isbn in pairs:
            conn.execute("UPDATE products SET isbn13 = ? WHERE product_id = ?", (isbn, pid))
    db._conn.close()
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_fill_db_dry_run_touches_nothing(tmp_path: Path) -> None:
    path = _make_db(tmp_path)
    before = _digest(path)
    calls: list[str] = []
    counts = fill.fill_db(str(path), _fill_client(tmp_path, calls, True), True, False,
                          lambda m: None)  # fmt: skip
    assert counts["targets"] == 3 and counts["found"] == 1 and counts["written"] == 0
    assert len(calls) == 3
    assert _digest(path) == before
    assert not (tmp_path / "cache").exists()  # dry-run은 캐시도 쓰지 않는다


def test_fill_db_writes_only_cover_url(tmp_path: Path) -> None:
    path = _make_db(tmp_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    before = {r["product_id"]: dict(r) for r in conn.execute("SELECT * FROM products")}
    conn.close()
    calls: list[str] = []
    counts = fill.fill_db(str(path), _fill_client(tmp_path, calls, False), False, False,
                          lambda m: None)  # fmt: skip
    assert counts["written"] == 1 and counts["stopped"] is None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    after = {r["product_id"]: dict(r) for r in conn.execute("SELECT * FROM products")}
    conn.close()
    assert after["prd_0001"]["cover_url"] == COVER
    for pid, row in before.items():
        expect = row | ({"cover_url": COVER} if pid == "prd_0001" else {})
        assert after[pid] == expect  # updated_at 등 다른 칸은 그대로
    # 두 번째 실행은 이미 채운 상품을 다시 부르지 않는다(나머지는 캐시)
    calls.clear()
    again = fill.fill_db(str(path), _fill_client(tmp_path, calls, False), False, False,
                         lambda m: None)  # fmt: skip
    assert again["targets"] == 2 and calls == []
    for f in (tmp_path / "cache").iterdir():
        assert FAKE_KEY not in f.read_text(encoding="utf-8")


def test_fill_stops_at_call_cap_and_keeps_what_it_found(tmp_path: Path) -> None:
    path = _make_db(tmp_path)
    calls: list[str] = []
    client = _fill_client(tmp_path, calls, False, max_calls=1)
    counts = fill.fill_db(str(path), client, False, False, lambda m: None)
    assert counts["written"] == 1 and counts["stopped"] and FAKE_KEY not in counts["stopped"]
    assert len(calls) == 1


def test_fill_books_json_dry_run_and_real(tmp_path: Path) -> None:
    path = tmp_path / "books.json"
    doc = {"source": "s", "books": [_book(ISBN_A, "가"), _book(ISBN_B, "나")]}
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    before = path.read_bytes()
    calls: list[str] = []
    counts = fill.fill_books_json(path, _fill_client(tmp_path, calls, True), True, False,
                                  lambda m: None)  # fmt: skip
    assert counts["found"] == 1 and path.read_bytes() == before
    counts = fill.fill_books_json(path, _fill_client(tmp_path, calls, False), False, False,
                                  lambda m: None)  # fmt: skip
    got = json.loads(path.read_text(encoding="utf-8"))
    assert got["books"][0] == _book(ISBN_A, "가", cover_url=COVER)
    assert got["books"][1] == _book(ISBN_B, "나")
    assert FAKE_KEY not in path.read_text(encoding="utf-8")


def test_fill_main_dry_run_never_prints_key(tmp_path: Path, monkeypatch, capsys) -> None:
    path = _make_db(tmp_path)
    before = _digest(path)
    monkeypatch.setenv("DATA4LIBRARY_API_KEY", FAKE_KEY)
    calls: list[str] = []
    real_init = seed_books.Client.__init__

    def init(self, key, **kw):
        kw["http_get"] = _detail_stub(calls)
        kw["sleep"] = lambda s: None
        real_init(self, key, **kw)

    monkeypatch.setattr(seed_books.Client, "__init__", init)
    code = fill.main(["--db", str(path), "--dry-run", "--cache-dir", str(tmp_path / "c")])
    out = capsys.readouterr()
    assert code == 0 and len(calls) == 3
    assert FAKE_KEY not in out.out + out.err
    assert _digest(path) == before and not (tmp_path / "c").exists()
