"""시드 데모 상품·구매자 지우기 (실제 책·가상 회원으로 바꾼 운영 서버용).

`seed.py`가 빈 DB에 넣은 상품 prd_0001~prd_0008과 구매자 buy_0001~buy_0030을, 그 구매자의 주문과
거기에 딸린 행(품목·결제·환불·반품·후기·클레임·문의·아이디어·계정·로그인 토큰·쿠폰)과 함께 한
트랜잭션에 지운다. 시드 구매자에게 넣어 본 시험 주문(예: `ord_1000000`)도 같이 지워진다.

    cd backend
    SHOP_DB_PATH=.data/shop.db uv run python scripts/clean_seed.py --dry-run
    SHOP_DB_PATH=.data/shop.db uv run python scripts/clean_seed.py

- 판매자 sel_0001~sel_0003은 남긴다. 실제 책(`load_catalog`)이 이 판매자에 붙고, 판매자가 하나라도
  있으면 `seed()`가 다시 넣지 않는다(서버를 다시 켜도 시드가 돌아오지 않는다).
- 가상 회원(`buy_100001`~)·실제 가입자(`buy_0031`~)의 행은 건드리지 않는다. 그런 주문·후기가
  시드 상품을 가리키면 그 상품은 지우지 않고 판매 중지(`active = 0`)만 한다(판매 기록이 상품을 잃지
  않게).
- 지우는 시드 주문 중 취소되지 않은 주문이 가져간 재고는 남는 상품(시드가 아닌 상품)에 되돌린다.
- outbox 이벤트는 지우지 않는다(이미 보냈거나 보낼 사건 기록이다. 보유기간 삭제가 치운다).
- 지운 시드 구매자 번호는 새 가입자에게 다시 주지 않는다(`accounts._next_buyer_id`).
- `--dry-run`은 같은 삭제를 해 본 뒤 되돌려 건수만 보여 준다. DB가 `:memory:`면 멈춘다.
- 시드가 아닌 판매 중 상품이 하나도 없으면(실제 책을 아직 안 넣었으면) 멈춘다. 지우면 팔 상품이 없는
  쇼핑몰이 된다. 순서는 `load_catalog` → 이 스크립트 → 되채우기 → 매일 모드다. `--force`로 넘긴다.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from typing import Any

from ax_shop_api.db import Db
from ax_shop_api.seed import PRODUCTS, SEED_BUYER_IDS, SELLERS

SEED_PRODUCT_IDS = tuple(p[0] for p in PRODUCTS)
SEED_SELLER_IDS = tuple(s[0] for s in SELLERS)


class _DryRun(Exception):
    """건수만 얻고 되돌린다."""


class NoCatalogError(ValueError):
    """시드가 아닌 판매 중 상품이 없다(`--force`로 넘긴다)."""


def _marks(values: tuple[str, ...]) -> str:
    return ",".join("?" * len(values))


def clean(conn: sqlite3.Connection) -> dict[str, Any]:
    """시드 상품·구매자와 딸린 행을 지운다(호출하는 쪽 트랜잭션 안). 건수를 돌려준다."""
    buyers = tuple(sorted(SEED_BUYER_IDS))
    products = SEED_PRODUCT_IDS
    b, p = _marks(buyers), _marks(products)
    seed_orders = f"SELECT order_id FROM orders WHERE buyer_id IN ({b})"  # noqa: S608 - 자리표뿐
    counts: dict[str, Any] = {}
    # 지우는 주문이 가져간 재고를 남는 상품에 되돌린다(시드 상품은 지우거나 판매 중지한다)
    taken = conn.execute(
        "SELECT i.product_id, SUM(i.qty) AS qty FROM order_items i JOIN orders o "
        f"ON o.order_id = i.order_id WHERE o.buyer_id IN ({b}) AND o.status != 'cancelled' "  # noqa: S608
        f"AND i.product_id NOT IN ({p}) GROUP BY i.product_id",
        (*buyers, *products),
    ).fetchall()
    for row in taken:
        conn.execute(
            "UPDATE products SET stock = stock + ? WHERE product_id = ?",
            (row["qty"], row["product_id"]),
        )
    counts["stock_restored_units"] = sum(row["qty"] for row in taken)
    steps: tuple[tuple[str, str, tuple[str, ...]], ...] = (
        (
            "return_sync",
            f"claim_id IN (SELECT claim_id FROM returns WHERE order_id IN ({seed_orders}))",
            buyers,
        ),
        ("returns", f"order_id IN ({seed_orders})", buyers),
        ("refunds", f"order_id IN ({seed_orders})", buyers),
        (
            "pg_fake_records",
            f"payment_id IN (SELECT payment_id FROM payments WHERE order_id IN ({seed_orders}))",
            buyers,
        ),
        ("payment_attempts", f"order_id IN ({seed_orders})", buyers),
        ("payments", f"order_id IN ({seed_orders})", buyers),
        ("order_items", f"order_id IN ({seed_orders})", buyers),
        ("reviews", f"buyer_id IN ({b}) OR order_id IN ({seed_orders})", (*buyers, *buyers)),
        ("claim_inbox", f"buyer_id IN ({b}) OR order_id IN ({seed_orders})", (*buyers, *buyers)),
        (
            "inquiry_inbox",
            f"buyer_id IN ({b}) OR order_id IN ({seed_orders})",
            (*buyers, *buyers),
        ),
        ("idea_inbox", f"buyer_id IN ({b})", buyers),
        ("sessions", f"buyer_id IN ({b})", buyers),
        ("accounts", f"buyer_id IN ({b})", buyers),
        ("orders", f"buyer_id IN ({b})", buyers),
        ("coupons", f"buyer_id IN ({b})", buyers),
        ("buyers", f"buyer_id IN ({b})", buyers),
    )
    for table, condition, args in steps:
        sql = f"DELETE FROM {table} WHERE {condition}"  # noqa: S608 - 표·조건은 위 상수뿐
        counts[table] = conn.execute(sql, args).rowcount
    # 남은(시드가 아닌) 주문·후기가 가리키는 시드 상품은 판매 중지만 한다
    kept = {
        r[0]
        for r in conn.execute(
            f"SELECT product_id FROM order_items WHERE product_id IN ({p}) "  # noqa: S608
            f"UNION SELECT product_id FROM reviews WHERE product_id IN ({p})",
            (*products, *products),
        )
    }
    keep = tuple(sorted(kept))
    gone = tuple(pid for pid in products if pid not in kept)
    counts["products"] = (
        conn.execute(f"DELETE FROM products WHERE product_id IN ({_marks(gone)})", gone).rowcount  # noqa: S608
        if gone
        else 0
    )
    if keep:
        conn.execute(
            f"UPDATE products SET active = 0 WHERE product_id IN ({_marks(keep)})",  # noqa: S608
            keep,
        )
    counts["products_deactivated"] = list(keep)
    counts["sellers_kept"] = conn.execute(
        f"SELECT COUNT(*) FROM sellers WHERE seller_id IN ({_marks(SEED_SELLER_IDS)})",  # noqa: S608
        SEED_SELLER_IDS,
    ).fetchone()[0]
    return counts


def run(db: Db, *, apply: bool, force: bool = False) -> dict[str, Any]:
    """한 트랜잭션. `apply=False`면 같은 삭제를 해 본 뒤 되돌린다."""
    if not db.is_file:
        raise ValueError("DB가 메모리(:memory:)라 지울 것이 없다. SHOP_DB_PATH나 --db로 준다")
    counts: dict[str, Any] = {}
    try:
        with db.tx() as conn:
            conn.execute("BEGIN IMMEDIATE")  # 세는 동안 다른 쓰기가 끼지 않게
            marks = _marks(SEED_PRODUCT_IDS)
            real = conn.execute(
                f"SELECT COUNT(*) FROM products WHERE active = 1 AND product_id NOT IN ({marks})",  # noqa: S608
                SEED_PRODUCT_IDS,
            ).fetchone()[0]
            if not real and not force:
                raise NoCatalogError(
                    "시드가 아닌 판매 중 상품이 없다. load_catalog로 실제 책을 먼저 넣는다"
                    "(--force로 넘긴다)"
                )
            counts = clean(conn)
            if not apply:
                raise _DryRun
    except _DryRun:
        pass
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="시드 데모 상품·구매자와 딸린 행을 지운다")
    parser.add_argument("--db", help="DB 파일(기본 SHOP_DB_PATH)")
    parser.add_argument("--dry-run", action="store_true", help="건수만 보고 되돌린다")
    parser.add_argument("--force", action="store_true", help="실제 상품이 없어도 지운다")
    args = parser.parse_args(argv)
    db = Db(args.db)
    try:
        counts = run(db, apply=not args.dry_run, force=args.force)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    mode = "dry-run(되돌림)" if args.dry_run else "지움"
    print(f"[시드 지우기 {mode}]")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
