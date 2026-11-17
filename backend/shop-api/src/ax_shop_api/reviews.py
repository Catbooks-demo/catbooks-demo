"""구매 후기. 배송 완료된 본인 주문의 상품에만, 주문·상품마다 한 번 쓴다.

공개 목록에는 구매자 ID·이름을 싣지 않고 연령대 표시(`author_label`)만 싣는다.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from ax_shop_api.db import Db
from ax_shop_api.service import ShopError

BODY_MIN = 10
BODY_MAX = 1000
RATINGS = (1, 2, 3, 4, 5)
_AGE_LABELS = {
    "10s": "10대",
    "20s": "20대",
    "30s": "30대",
    "40s": "40대",
    "50s_plus": "50대 이상",
}
ANONYMOUS_LABEL = "구매자"


def author_label(age_band: str | None) -> str:
    return _AGE_LABELS.get(age_band or "", ANONYMOUS_LABEL)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Reviews:
    def __init__(self, db: Db) -> None:
        self.db = db

    def create(
        self, buyer_id: str, product_id: str, order_id: str, rating: int, body: str
    ) -> dict[str, Any]:
        with self.db.tx() as conn:
            ok = conn.execute(
                "SELECT 1 FROM orders o JOIN order_items i ON i.order_id = o.order_id "
                "WHERE o.order_id = ? AND o.buyer_id = ? AND i.product_id = ? "
                "AND o.delivered_at IS NOT NULL LIMIT 1",
                (order_id, buyer_id, product_id),
            ).fetchone()
            if ok is None:
                raise ShopError("NOT_REVIEWABLE", "배송이 끝난 내 주문 상품만 후기를 쓴다", 403)
            exists = conn.execute(
                "SELECT 1 FROM reviews WHERE order_id = ? AND product_id = ?",
                (order_id, product_id),
            ).fetchone()
            if exists is not None:
                raise ShopError("REVIEW_EXISTS", "이미 후기를 썼다", 409)
            review_id = self.db.next_id(conn, "review", "rev_")
            created_at = _now()
            conn.execute(
                "INSERT INTO reviews(review_id, order_id, product_id, buyer_id, rating, body, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (review_id, order_id, product_id, buyer_id, rating, body, created_at),
            )
        return {
            "review_id": review_id,
            "order_id": order_id,
            "product_id": product_id,
            "rating": rating,
            "body": body,
            "created_at": created_at,
        }

    def for_product(self, product_id: str, page: int, size: int) -> tuple[dict[str, Any], int]:
        with self.db.tx() as conn:
            by_rating = dict.fromkeys(("1", "2", "3", "4", "5"), 0)
            total = 0
            score = 0
            for row in conn.execute(
                "SELECT rating, COUNT(*) AS n FROM reviews WHERE product_id = ? GROUP BY rating",
                (product_id,),
            ):
                by_rating[str(row["rating"])] = row["n"]
                total += row["n"]
                score += row["rating"] * row["n"]
            rows = conn.execute(
                "SELECT r.review_id, r.rating, r.body, r.created_at, b.age_band "
                "FROM reviews r LEFT JOIN buyers b ON b.buyer_id = r.buyer_id "
                "WHERE r.product_id = ? ORDER BY r.created_at DESC, r.review_id DESC "
                "LIMIT ? OFFSET ?",
                (product_id, size, (page - 1) * size),
            ).fetchall()
        items = [
            {
                "review_id": r["review_id"],
                "rating": r["rating"],
                "body": r["body"],
                "created_at": r["created_at"],
                "author_label": author_label(r["age_band"]),
            }
            for r in rows
        ]
        avg = round(score / total, 2) if total else 0
        summary = {"count": total, "avg": avg, "by_rating": by_rating}
        return {"summary": summary, "items": items}, total

    def mine(self, buyer_id: str) -> list[dict[str, Any]]:
        with self.db.tx() as conn:
            rows = conn.execute(
                "SELECT review_id, product_id, order_id, rating, body, created_at FROM reviews "
                "WHERE buyer_id = ? ORDER BY created_at DESC, review_id DESC",
                (buyer_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def reviewable(self, buyer_id: str) -> list[dict[str, Any]]:
        with self.db.tx() as conn:
            return _reviewable(conn, buyer_id)


def _reviewable(conn: sqlite3.Connection, buyer_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT DISTINCT o.order_id, i.product_id, COALESCE(i.title, p.name) AS title "
        "FROM orders o JOIN order_items i ON i.order_id = o.order_id "
        "LEFT JOIN products p ON p.product_id = i.product_id "
        "WHERE o.buyer_id = ? AND o.delivered_at IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM reviews r "
        "WHERE r.order_id = o.order_id AND r.product_id = i.product_id) "
        "ORDER BY o.delivered_at DESC, o.order_id DESC, i.product_id",
        (buyer_id,),
    ).fetchall()
    return [dict(r) for r in rows]
