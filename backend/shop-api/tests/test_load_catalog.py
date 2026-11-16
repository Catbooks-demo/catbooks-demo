"""상품 목록 넣기(`ax_shop_api.load_catalog`): 넣기, 다시 돌려도 같음, 칸, 장르, dry-run."""

import json
from pathlib import Path

import pytest

from ax_shop_api.db import Db
from ax_shop_api.genres import GENRES
from ax_shop_api.load_catalog import load_catalog, main
from ax_shop_api.seed import seed

BOOKS = [
    {"isbn13": "9788936434120", "title": "소년이 온다", "author": "한강 지음",
     "publisher": "창비", "pub_year": 2014, "genre_code": "novel", "kdc": "813.7",
     "description": "소개 글", "price": 13500, "stock": 120},
    {"isbn13": "978-89-01-23456-4", "title": "철학 입문", "author": "", "publisher": "출판사",
     "pub_year": None, "genre_code": "humanities", "kdc": "100", "description": "",
     "price": 18000, "stock": 40},
]  # fmt: skip
GOODS = [
    {"name": "0.5mm 중성펜 검정 10자루", "genre_code": "writing", "category_code": "stationery",
     "price": 6500, "stock": 100, "description": "펜"},
    {"name": "A5 도트 노트", "genre_code": "notebook", "category_code": "stationery",
     "price": 9000, "stock": 50, "description": ""},
]  # fmt: skip


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    (tmp_path / "books.json").write_text(
        json.dumps({"source": "test", "books": BOOKS}, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "stationery.json").write_text(
        json.dumps({"items": GOODS}, ensure_ascii=False), encoding="utf-8"
    )
    return tmp_path


def _db() -> Db:
    db = Db(":memory:")
    seed(db)
    return db


def _new_rows(db: Db) -> list[dict]:
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE product_id > 'prd_0008' ORDER BY product_id"
        ).fetchall()
    return [dict(r) for r in rows]


def test_inserts_books_and_stationery_with_fields(catalog: Path) -> None:
    db = _db()
    with db.tx() as conn:
        before = [dict(r) for r in conn.execute("SELECT * FROM products ORDER BY product_id")]
    counts = load_catalog(db, catalog)
    assert counts == {"books_added": 2, "books_skipped": 0, "stationery_added": 2,
                      "stationery_skipped": 0}  # fmt: skip
    rows = _new_rows(db)
    assert [r["product_id"] for r in rows] == ["prd_0009", "prd_0010", "prd_0011", "prd_0012"]
    novel = next(r for r in rows if r["isbn13"] == "9788936434120")
    assert novel["name"] == "소년이 온다"
    assert novel["category_code"] == "book"
    assert (novel["author"], novel["publisher"]) == ("한강 지음", "창비")
    assert (novel["price"], novel["stock"], novel["active"]) == (13500, 120, 1)
    assert novel["genre_code"] == "novel"
    assert novel["description"] == "소개 글"
    assert novel["seller_id"] in ("sel_0001", "sel_0003")
    other = next(r for r in rows if r["genre_code"] == "humanities")
    assert other["isbn13"] == "9788901234564"  # 하이픈은 지운다
    assert other["author"] is None and other["description"] is None
    pens = [r for r in rows if r["category_code"] == "stationery"]
    assert {r["seller_id"] for r in pens} <= {"sel_0002", "sel_0003"}
    assert all(r["isbn13"] is None for r in pens)
    assert all(r["genre_code"] in GENRES for r in rows)
    # 시드 상품은 그대로다
    with db.tx() as conn:
        sql = "SELECT * FROM products WHERE product_id <= 'prd_0008' ORDER BY product_id"
        seeded = [dict(r) for r in conn.execute(sql)]
    assert seeded == before


def test_second_run_skips_everything(catalog: Path) -> None:
    db = _db()
    load_catalog(db, catalog)
    counts = load_catalog(db, catalog)
    assert counts == {"books_added": 0, "books_skipped": 2, "stationery_added": 0,
                      "stationery_skipped": 2}  # fmt: skip
    assert len(_new_rows(db)) == 4


def test_dry_run_writes_nothing(catalog: Path) -> None:
    db = _db()
    with db.tx() as conn:
        counter = conn.execute("SELECT value FROM counters WHERE name='product'").fetchone()[0]
    counts = load_catalog(db, catalog, dry_run=True)
    assert counts["books_added"] == 2 and counts["stationery_added"] == 2
    assert _new_rows(db) == []
    with db.tx() as conn:
        after = conn.execute("SELECT value FROM counters WHERE name='product'").fetchone()[0]
    assert after == counter


def test_unknown_genre_is_rejected(catalog: Path) -> None:
    bad = [{**BOOKS[0], "genre_code": "poetry"}]
    (catalog / "books.json").write_text(json.dumps({"books": bad}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_catalog(_db(), catalog)


def test_cli_on_empty_file_db_seeds_then_loads(catalog: Path, tmp_path: Path, capsys) -> None:
    path = str(tmp_path / "shop.db")
    assert main(["--db", path, "--catalog-dir", str(catalog)]) == 0
    assert "책 넣음 2권" in capsys.readouterr().out
    db = Db(path)
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 8 + 4


def test_shipped_catalog_files_are_valid() -> None:
    """커밋된 목록 파일이 넣기 규칙을 지킨다(장르·ISBN·가격)."""
    db = _db()
    counts = load_catalog(db, dry_run=True)
    assert counts["stationery_added"] == 45
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 8


def test_cli_without_books_json_loads_stationery(catalog: Path, tmp_path: Path, capsys) -> None:
    (catalog / "books.json").unlink()
    path = str(tmp_path / "shop.db")
    assert main(["--db", path, "--catalog-dir", str(catalog)]) == 0
    out = capsys.readouterr().out
    assert "books.json 없음: 책은 건너뛰고 문구만 넣어요" in out
    assert "문구 넣음 2개" in out
    with Db(path).tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 8 + 2
