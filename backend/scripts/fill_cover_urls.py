"""이미 있는 책 상품(또는 `books.json`)에 표지 주소(`cover_url`)를 ISBN으로 채운다.

도서관 정보나루 도서 상세(`srchDtlList`)의 `bookImageURL`(그림 **주소**)만 적는다. 그림 파일은
받지도 저장하지도 않는다. 제공처 이용 조건상 표지는 비영리 화면에서 원래 주소로 보여 주기만 할
수 있다(내려받기·저장·재배포·AI 학습 금지).

- 대상: `--db`(기본 `SHOP_DB_PATH`)의 `products`에서 ISBN이 있고 `cover_url`이 빈 상품, 또는
  `--books-json PATH`의 책 중 `cover_url`이 없는 책. `--overwrite`면 이미 있는 주소도 다시 받는다.
- 쓰는 것: DB는 `products.cover_url` 한 칸만(`updated_at` 등 다른 칸은 그대로), json은 책마다
  `cover_url` 한 칸만. `--dry-run`은 아무것도 쓰지 않는다(DB는 읽기 전용으로 열고 캐시도 쓰지
  않는다).
- 키·호출: `seed_books.py`와 같다(`DATA4LIBRARY_API_KEY` 환경 변수 → `--env-file` → `backend/.env`
  → `backend/shop-api/.env`). 키는 출력·저장하지 않는다. 1초에 한 번 이하(`--min-interval`),
  전체 호출은 `--max-calls`(기본 400)까지. 응답 캐시(`backend/.cache/data4library/`)는
  `seed_books.py`와 같이 쓴다(같은 책은 다시 부르지 않는다).

실행: `cd backend && SHOP_DB_PATH=... uv run python scripts/fill_cover_urls.py --dry-run`
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import seed_books  # noqa: E402

from ax_shop_api.book_info import normalize_isbn13, safe_cover_url  # noqa: E402

Log = Callable[[str], None]


def fetch_cover(client: seed_books.Client, isbn13: str) -> str | None:
    """ISBN 하나의 표지 주소(맞지 않거나 없으면 None)."""
    data = client.get("srchDtlList", {"isbn13": isbn13, "loaninfoYN": "N"})
    return safe_cover_url(seed_books.detail_book(data).get("bookImageURL"))


def _lookup(
    client: seed_books.Client, targets: list[tuple[str, str]], log: Log
) -> tuple[dict[str, str], dict[str, int], str | None]:
    """`(이름표, isbn)` 목록을 차례로 부른다. 찾은 주소·셈·멈춘 이유(없으면 None)."""
    found: dict[str, str] = {}
    counts = {"targets": len(targets), "found": 0, "no_cover": 0}
    for label, isbn in targets:
        try:
            cover = fetch_cover(client, isbn)
        except seed_books.ApiError as exc:  # 상한·키 거부 등: 여기까지 찾은 것만 쓴다
            return found, counts, str(exc)
        if cover:
            found[label] = cover
            counts["found"] += 1
        else:
            counts["no_cover"] += 1
    log(f"표지 주소 찾음 {counts['found']}개, 없음 {counts['no_cover']}개")
    return found, counts, None


# --- DB ------------------------------------------------------------------------------------
def _db_targets(conn: sqlite3.Connection, overwrite: bool) -> list[tuple[str, str]]:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(products)")}
    where = "isbn13 IS NOT NULL AND isbn13 != ''"
    if "cover_url" in columns and not overwrite:
        where += " AND (cover_url IS NULL OR cover_url = '')"
    rows = conn.execute(
        f"SELECT product_id, isbn13 FROM products WHERE {where} ORDER BY product_id"  # noqa: S608
    ).fetchall()
    return [(pid, isbn) for pid, isbn in rows]


def fill_db(
    db_path: str, client: seed_books.Client, dry_run: bool, overwrite: bool, log: Log
) -> dict:
    """DB 상품의 `cover_url`만 채운다. dry-run은 읽기 전용 연결로 대상만 세고 부른다."""
    db = None
    if dry_run:
        conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            targets = _db_targets(conn, overwrite)
        finally:
            conn.close()
    else:
        from ax_shop_api.db import Db

        db = Db(db_path)  # 옛 DB면 `cover_url` 칸을 더한다(자동 이전)
        with db.tx() as conn:
            targets = _db_targets(conn, overwrite)
    log(f"대상 상품 {len(targets)}개")
    found, counts, stopped = _lookup(client, targets, log)
    counts["written"] = 0
    if found and db is not None:
        guard = "" if overwrite else " AND (cover_url IS NULL OR cover_url = '')"
        with db.tx() as conn:  # 한 트랜잭션. `updated_at` 등 다른 칸은 건드리지 않는다
            for pid, url in found.items():
                cur = conn.execute(
                    f"UPDATE products SET cover_url = ? WHERE product_id = ?{guard}",  # noqa: S608
                    (url, pid),
                )
                counts["written"] += cur.rowcount
    counts["stopped"] = stopped
    return counts


# --- books.json ----------------------------------------------------------------------------
def fill_books_json(
    path: Path, client: seed_books.Client, dry_run: bool, overwrite: bool, log: Log
) -> dict:
    """`books.json`의 책마다 `cover_url` 한 칸만 더하거나 바꾼다(다른 칸·순서는 그대로)."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    books = doc.get("books") or []
    targets = []
    for index, book in enumerate(books):
        if book.get("cover_url") and not overwrite:
            continue
        try:
            isbn = normalize_isbn13(str(book.get("isbn13") or ""))
        except ValueError:
            isbn = None
        if isbn:
            targets.append((str(index), isbn))
    log(f"대상 책 {len(targets)}권")
    found, counts, stopped = _lookup(client, targets, log)
    counts["written"] = 0
    if found and not dry_run:
        for index, url in found.items():
            books[int(index)]["cover_url"] = url
            counts["written"] += 1
        text = json.dumps(doc, ensure_ascii=False, indent=1) + "\n"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    counts["stopped"] = stopped
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--db", help="DB 파일(기본 SHOP_DB_PATH)")
    where.add_argument("--books-json", type=Path, help="DB 대신 이 books.json을 채운다")
    parser.add_argument("--env-file", action="append", type=Path, default=[])
    parser.add_argument("--dry-run", action="store_true", help="부르기만 하고 아무것도 쓰지 않는다")
    parser.add_argument("--overwrite", action="store_true", help="이미 있는 주소도 다시 받는다")
    parser.add_argument("--max-calls", type=int, default=400)
    parser.add_argument("--min-interval", type=float, default=1.0, help="호출 사이 최소 초")
    parser.add_argument("--cache-dir", type=Path, default=seed_books.DEFAULT_CACHE)
    args = parser.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, flush=True)

    db_path = None
    if args.books_json is None:
        db_path = args.db or os.environ.get("SHOP_DB_PATH", "")
        if not db_path or ":memory:" in db_path or not Path(db_path).is_file():
            print("DB 파일이 없다: --db나 SHOP_DB_PATH로 있는 파일을 준다", file=sys.stderr)
            return 1
    elif not args.books_json.is_file():
        print(f"파일이 없다: {args.books_json}", file=sys.stderr)
        return 1

    key = seed_books.load_key(args.env_file)
    client = seed_books.Client(
        key,
        base_url=os.environ.get("DATA4LIBRARY_URL", seed_books.DEFAULT_URL),
        cache_dir=args.cache_dir,
        max_calls=args.max_calls,
        min_interval=args.min_interval,
        write_cache=not args.dry_run,
    )
    if db_path:
        counts = fill_db(db_path, client, args.dry_run, args.overwrite, log)
    else:
        counts = fill_books_json(args.books_json, client, args.dry_run, args.overwrite, log)
    label = "쓸 것(dry-run)" if args.dry_run else "씀"
    found = counts["found"] if args.dry_run else counts["written"]
    log(f"{label} {found}개 / 대상 {counts['targets']}개, 호출 {client.calls}회, "
        f"캐시 {client.cache_hits}회")  # fmt: skip
    if counts["stopped"]:
        print(f"멈춤: {counts['stopped']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
