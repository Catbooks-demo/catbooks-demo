"""구매자 요약 (`GET /api/shop/buyer-summaries`, `data_access.shop_buyer_summaries`). 읽기 전용.

AXcellent data가 하루 집계(재구매·코호트·떠날 위험·채널 가치)를 계산할 때 원본 주문을 저장하지 않고
이것만 읽어 간다(로드맵 3단계 3-3, 11/25).

정의 (data의 구매자 요약 표 `sales_summary`와 같게, 병행 비교가 0차이로 끝나도록)
- 결제 완료 주문 = 성공한 결제가 있고, 상태가 취소·결제 실패·결제 대기·전액 환불 상태가 아닌 주문
  (`EXCLUDED`). 부분 환불은 결제 완료에 남는다. 취소 클레임으로 전액 환불된 주문은 상태가
  `cancelled`가 되어 빠진다.
- 결제 시각 = 주문 접수 시각(`created_at`). `as_of` 전 접수만 센다.
- 첫 주문 순서 = (접수 시각, 주문 id). 두 번째 시각은 재구매율용. `first_src_source`는 첫
  결제 완료 주문의 유입 출처.
- 항목(`items`): 결제 완료 주문의 줄마다 장르(`genre_code`)·분류(`category_code`)·상품
  (`product_id`).
  값이 없는 칸은 건너뛴다. 같은 주문에 같은 항목이 여러 줄이어도 주문 수는 1이다.
- 속성(`attributes`): 고객 내보내기와 같은 key(`age_band`·`gender`·`occupation`), 값이 있는 것만.

고르는 구매자
- `as_of` 전 접수 주문이 하나라도 있는 구매자. 결제 완료 주문이 없으면 시각 칸은 None, 수는 0
  (취소로 결제 완료가 0이 된 구매자를 받는 쪽이 지울 수 있게).
- `changed_since`가 있으면 그 뒤 주문이 바뀐(결제·취소·환불은 주문 `updated_at`을 바꾼다) 구매자와
  프로필을 고친 구매자만.
- 순서는 `buyer_id` 오름차순. 커서는 마지막 `buyer_id`를 URL-safe base64로 감싼 글자.

구매자 이름·연락처·주소는 담지 않는다. 쓰기 없음.
"""

from __future__ import annotations

import base64
import binascii
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ax_contracts.data_access.shop_buyer_summaries import (
    BuyerItemSummary,
    BuyerSummaryRow,
    ShopBuyerSummariesQuery,
    ShopBuyerSummariesResult,
)
from ax_shop_api.db import Db
from ax_shop_api.service import ShopError

# data `sales_clean.EXCLUDED_STATUSES`와 같다
EXCLUDED = frozenset(
    {"cancelled", "canceled", "payment_failed", "pending_payment", "refunded_full"}
)
ATTRIBUTES = ("age_band", "gender", "occupation")
LEVELS = (("genre", "genre_code"), ("category", "category_code"), ("product", "product_id"))
_CHUNK = 500


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def encode_cursor(buyer_id: str) -> str:
    return base64.urlsafe_b64encode(buyer_id.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> str:
    """커서 → 마지막 buyer_id. 모양이 틀리면 400 `INVALID_CURSOR`."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        buyer_id = base64.urlsafe_b64decode(padded.encode()).decode()
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise ShopError("INVALID_CURSOR", "커서를 읽을 수 없다") from error
    if not buyer_id:
        raise ShopError("INVALID_CURSOR", "커서를 읽을 수 없다")
    return buyer_id


class BuyerSummaries:
    def __init__(self, db: Db, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.db = db
        self.now = now

    def summaries(self, query: ShopBuyerSummariesQuery) -> ShopBuyerSummariesResult:
        as_of = _iso(query.as_of)
        sql = (
            "SELECT b.buyer_id, b.age_band, b.gender, b.occupation FROM buyers b "
            "WHERE b.tenant_id = ? AND EXISTS (SELECT 1 FROM orders o "
            "WHERE o.buyer_id = b.buyer_id AND o.created_at < ?)"
        )
        args: list[Any] = [query.tenant_id, as_of]
        if query.changed_since is not None:
            since = _iso(query.changed_since)
            sql += (
                " AND (EXISTS (SELECT 1 FROM orders o WHERE o.buyer_id = b.buyer_id "
                "AND o.updated_at >= ?) OR COALESCE(b.updated_at, b.created_at) >= ?)"
            )
            args += [since, since]
        if query.cursor:
            sql += " AND b.buyer_id > ?"
            args.append(decode_cursor(query.cursor))
        sql += " ORDER BY b.buyer_id LIMIT ?"
        args.append(query.limit + 1)  # 하나 더 읽어 남은 것이 있는지 안다
        with self.db.tx() as conn:
            rows = conn.execute(sql, args).fetchall()
            has_more = len(rows) > query.limit
            rows = rows[: query.limit]
            paid = self._paid_orders(conn, [r["buyer_id"] for r in rows], as_of)
        buyers = [self._row(r, paid.get(r["buyer_id"], []), query.tenant_id) for r in rows]
        next_cursor = encode_cursor(rows[-1]["buyer_id"]) if rows else query.cursor
        return ShopBuyerSummariesResult(
            buyers=buyers,
            next_cursor=next_cursor,
            has_more=has_more,
            as_of=query.as_of,
            tenant_id=query.tenant_id,
        )

    @staticmethod
    def _row(buyer: Any, orders: list[dict[str, Any]], tenant_id: str) -> BuyerSummaryRow:
        attributes = {key: buyer[key] for key in ATTRIBUTES if buyer[key]}
        if not orders:
            return BuyerSummaryRow(
                buyer_id=buyer["buyer_id"], attributes=attributes, tenant_id=tenant_id
            )
        items: dict[tuple[str, str], dict[str, Any]] = {}
        for order in orders:  # (접수 시각, 주문 id) 순
            at = order["created_at"]
            for level, code in sorted(order["items"]):
                item = items.setdefault((level, code), {"first": at, "last": at, "orders": 0})
                item["last"] = max(item["last"], at)
                item["orders"] += 1
        return BuyerSummaryRow(
            buyer_id=buyer["buyer_id"],
            first_paid_at=_parse(orders[0]["created_at"]),
            second_paid_at=_parse(orders[1]["created_at"]) if len(orders) > 1 else None,
            last_paid_at=_parse(max(o["created_at"] for o in orders)),
            paid_orders=len(orders),
            first_src_source=orders[0]["src_source"],
            attributes=attributes,
            items=[
                BuyerItemSummary(
                    level=level,  # type: ignore[arg-type]
                    code=code,
                    first_paid_at=_parse(item["first"]),
                    last_paid_at=_parse(item["last"]),
                    paid_orders=item["orders"],
                )
                for (level, code), item in sorted(items.items())
            ],
            tenant_id=tenant_id,
        )

    @staticmethod
    def _paid_orders(
        conn: Any, buyer_ids: list[str], as_of: str
    ) -> dict[str, list[dict[str, Any]]]:
        """구매자마다 `as_of` 전 결제 완료 주문(접수 시각·주문 id 순)과 그 주문의 항목."""
        out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for start in range(0, len(buyer_ids), _CHUNK):
            chunk = buyer_ids[start : start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            excluded = sorted(EXCLUDED)
            ex_marks = ",".join("?" * len(excluded))
            orders = conn.execute(
                "SELECT o.order_id, o.buyer_id, o.created_at, o.src_source FROM orders o "
                f"WHERE o.buyer_id IN ({marks}) AND o.created_at < ? "
                f"AND LOWER(o.status) NOT IN ({ex_marks}) "
                "AND EXISTS (SELECT 1 FROM payments p WHERE p.order_id = o.order_id "
                "AND p.status = 'succeeded') "
                "ORDER BY o.buyer_id, o.created_at, o.order_id",
                [*chunk, as_of, *excluded],
            ).fetchall()
            ids = [o["order_id"] for o in orders]
            items: dict[str, set[tuple[str, str]]] = defaultdict(set)
            for i in range(0, len(ids), _CHUNK):
                part = ids[i : i + _CHUNK]
                for line in conn.execute(
                    "SELECT i.order_id, i.product_id, p.category_code, p.genre_code "
                    "FROM order_items i LEFT JOIN products p ON p.product_id = i.product_id "
                    f"WHERE i.order_id IN ({','.join('?' * len(part))})",
                    part,
                ):
                    for level, column in LEVELS:
                        if line[column]:
                            items[line["order_id"]].add((level, line[column]))
            for o in orders:
                out[o["buyer_id"]].append(
                    {
                        "created_at": o["created_at"],
                        "src_source": o["src_source"],
                        "items": items[o["order_id"]],
                    }
                )
        return out
