"""상품 목록 파일(`catalog/books.json`·`catalog/stationery.json`)을 지금 DB에 넣는다.

`python -m ax_shop_api.load_catalog [--dry-run] [--db PATH] [--catalog-dir DIR]`

- DB는 `SHOP_DB_PATH`(또는 `--db`)다. 다시 돌려도 같다: 책은 같은 `isbn13`이 이미 있으면, 문구는
  같은 이름의 문구 상품이 이미 있으면 건너뛴다.
- 새 상품 ID는 상품 번호표(`counters.product`)로 받는다. 시드 상품 prd_0001~0008은 건드리지 않는다.
- 판매자: 책은 책마루(sel_0001)가 대부분이고 일부(ISBN 해시로 4권 중 1권)는 북앤펜(sel_0003),
  문구는 종이나라(sel_0002)·북앤펜(sel_0003)을 이름 해시로 나눈다.
  판매자가 없는 빈 DB면 시드를 먼저 넣는다.
- 한 트랜잭션이라 서버가 떠 있어도 반쯤 넣은 상태가 보이지 않는다.
- 책 정보 출처는 도서관 정보나루이고 가격·재고는 데모 값이다(`catalog/books.json`의 note).
"""

from __future__ import annotations

import argparse
import json
import zlib
from datetime import UTC, datetime
from pathlib import Path

from ax_shop_api import book_info
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.genres import GENRES
from ax_shop_api.seed import seed

CATALOG_DIR = Path(__file__).resolve().parent / "catalog"
BOOK_SELLERS = ("sel_0001", "sel_0001", "sel_0001", "sel_0003")
STATIONERY_SELLERS = ("sel_0002", "sel_0003")
STATIONERY_GENRES = ("writing", "notebook", "deco")


def _pick(key: str, sellers: tuple[str, ...]) -> str:
    return sellers[zlib.crc32(key.encode("utf-8")) % len(sellers)]


def _read(path: Path, field: str) -> list[dict]:
    if not path.exists():
        return []
    return list(json.loads(path.read_text(encoding="utf-8")).get(field) or [])


def _book_row(item: dict) -> dict:
    isbn = book_info.normalize_isbn13(item.get("isbn13"))
    if not isbn:
        raise ValueError("책에 isbn13이 없다")
    genre = item.get("genre_code")
    if genre not in GENRES or genre in STATIONERY_GENRES:
        raise ValueError(f"{isbn}: 책 장르가 아니다 ({genre})")
    return {
        "name": str(item["title"]).strip(),
        "category_code": "book",
        "price": int(item["price"]),
        "stock": int(item["stock"]),
        "isbn13": isbn,
        "author": (book_info.clean_text(item.get("author")) or "")[: book_info.AUTHOR_MAX] or None,
        "publisher": (book_info.clean_text(item.get("publisher")) or "")[: book_info.PUBLISHER_MAX]
        or None,
        "genre_code": genre,
        "description": (book_info.clean_text(item.get("description")) or "")[
            : book_info.DESCRIPTION_MAX
        ]
        or None,
        # 표지 주소: http·https만, 어긋나면 버린다(표지 없음). 그림 자체는 받지 않는다
        "cover_url": book_info.safe_cover_url(item.get("cover_url")),
        "seller_id": _pick(isbn, BOOK_SELLERS),
    }


def _stationery_row(item: dict) -> dict:
    name = str(item["name"]).strip()
    genre = item.get("genre_code")
    if genre not in STATIONERY_GENRES:
        raise ValueError(f"{name}: 문구 장르가 아니다 ({genre})")
    return {
        "name": name,
        "category_code": "stationery",
        "price": int(item["price"]),
        "stock": int(item["stock"]),
        "isbn13": None,
        "author": None,
        "publisher": None,
        "genre_code": genre,
        "description": (book_info.clean_text(item.get("description")) or "")[
            : book_info.DESCRIPTION_MAX
        ]
        or None,
        "cover_url": None,
        "seller_id": _pick(name, STATIONERY_SELLERS),
    }


def load_catalog(db: Db, catalog_dir: Path = CATALOG_DIR, dry_run: bool = False) -> dict:
    """목록을 넣고 넣은 수·건너뛴 수를 돌려준다.

    `{books_added, books_skipped, stationery_added, stationery_skipped}`
    """
    books = [_book_row(item) for item in _read(catalog_dir / "books.json", "books")]
    goods = [_stationery_row(item) for item in _read(catalog_dir / "stationery.json", "items")]
    counts = {"books_added": 0, "books_skipped": 0, "stationery_added": 0,
              "stationery_skipped": 0}  # fmt: skip
    if not dry_run:
        seed(db)  # 판매자가 없는 빈 DB만 채운다(이미 있으면 아무것도 안 한다)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.tx() as conn:
        have_isbn = {r[0] for r in conn.execute(
            "SELECT isbn13 FROM products WHERE isbn13 IS NOT NULL")}  # fmt: skip
        have_names = {r[0] for r in conn.execute(
            "SELECT name FROM products WHERE category_code = 'stationery'")}  # fmt: skip
        for kind, rows in (("books", books), ("stationery", goods)):
            for row in rows:
                if (row["isbn13"] in have_isbn) if kind == "books" else (row["name"] in have_names):
                    counts[f"{kind}_skipped"] += 1
                    continue
                if kind == "books":
                    have_isbn.add(row["isbn13"])
                else:
                    have_names.add(row["name"])
                counts[f"{kind}_added"] += 1
                if dry_run:
                    continue
                product_id = db.next_id(conn, "product", "prd_")
                conn.execute(
                    "INSERT INTO products (product_id, seller_id, name, category_code, price, "
                    "stock, active, tenant_id, context_id, created_at, updated_at, isbn13, author, "
                    "publisher, genre_code, description, cover_url) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (product_id, row["seller_id"], row["name"], row["category_code"],
                     row["price"], row["stock"], TENANT_ID, now, now, row["isbn13"],
                     row["author"], row["publisher"], row["genre_code"], row["description"],
                     row["cover_url"]),
                )  # fmt: skip
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="상품 목록 파일을 DB에 넣는다")
    parser.add_argument("--dry-run", action="store_true", help="넣지 않고 셀 것만 보인다")
    parser.add_argument("--db", help="DB 파일(기본 SHOP_DB_PATH)")
    parser.add_argument("--catalog-dir", type=Path, default=CATALOG_DIR)
    args = parser.parse_args(argv)
    if not (args.catalog_dir / "books.json").exists():
        print("books.json 없음: 책은 건너뛰고 문구만 넣어요 (scripts/seed_books.py로 만들어요)")
    db = Db(args.db)
    if not db.is_file:
        print("주의: DB가 메모리(:memory:)다. SHOP_DB_PATH나 --db로 파일을 준다")
    counts = load_catalog(db, args.catalog_dir, dry_run=args.dry_run)
    label = "넣을 것(dry-run)" if args.dry_run else "넣음"
    print(f"책 {label} {counts['books_added']}권, 건너뜀 {counts['books_skipped']}권")
    print(f"문구 {label} {counts['stationery_added']}개, 건너뜀 {counts['stationery_skipped']}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
