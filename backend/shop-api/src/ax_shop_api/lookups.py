"""data 에이전트가 읽는 조회 API의 업무 규칙(실패 주문 목록·지표 시계열·판매자 정보). 읽기 전용이다.

형식은 `contracts/data-access/`의 `shop_failed_orders`·`shop_metric_series`·`shop_seller_profile`·
`shop_seller_prices`를 따른다.
지표는 DB에서 코드로 계산한다(LLM 없음). 웹 프레임워크에 의존하지 않는다.

- 실패 주문: 결제가 `failed`인 기록 하나가 한 줄이다. 주문의 `category`는 첫 품목의 상품 분류다.
- 시계열: 5분 버킷, 버킷 시작 시각(UTC)이 `ts`다. 구간은 `from <= 시각 < to`이고 `from`은 버킷
  경계로 내림한다. 건수 지표는 값이 없는 버킷도 0으로 채우고, 비율 지표는 결제 시도가 없는
  버킷을 뺀다(0%와 '측정 못 함'을 섞지 않는다).
- 판매자 정보(fraud_score): 기존 `sellers`·`products`만 읽는다(테이블 변경 없음).
  - `joined_at` = `sellers.created_at`, `product_count` = 판매 중(active) 상품 수
  - `listings_last_24h` = `now - 24시간 <= products.created_at <= now`인 상품 수(판매 중지 포함,
    "등록한 수"라서). 정확히 24시간 전에 만든 상품은 센다
  - 사업자 해시는 테이블에 없어 `business_hash`·`business_hash_duplicated`를 null로 준다
- 판매자 가격: 판매자의 판매 중 상품마다 같은 카테고리 판매 중 상품(모든 판매자, 이 판매자 포함)
  가격의 중앙값. 개수가 짝수면 가운데 두 값의 평균을 반올림(0.5는 올림)한다. 카테고리에 상품이
  하나뿐이면 그 상품 가격이 중앙값이다
- 없는 판매자는 404 `SELLER_NOT_FOUND`
"""

from __future__ import annotations

import base64
import binascii
import math
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_contracts.data_access.shop_customers import (
    CustomerRow,
    ShopCustomersExportQuery,
    ShopCustomersExportResult,
)
from ax_contracts.data_access.shop_failed_orders import (
    FailedOrder,
    ShopFailedOrdersQuery,
    ShopFailedOrdersResult,
)
from ax_contracts.data_access.shop_metric_series import (
    MetricPoint,
    ShopMetricSeriesQuery,
    ShopMetricSeriesResult,
)
from ax_contracts.data_access.shop_seller_prices import (
    SellerProductPrice,
    ShopSellerPricesQuery,
    ShopSellerPricesResult,
)
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfile, ShopSellerProfileQuery
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.service import ShopError

BUCKET = timedelta(minutes=5)
LISTING_WINDOW = timedelta(hours=24)  # 판매자 정보의 "최근 등록" 구간
MAX_ATTRIBUTION_DAYS = 31  # 유입 출처 주문 조회의 기간 상한
# 판매 집계(`sales_summary`)로 묶을 수 있는 기준. 정한 값만 받고 다른 값은 422다
SALES_GROUPS = ("product", "age_band", "category", "gender", "occupation", "genre")
# 구매자 속성으로 묶는 기준. 이 기준이 든 집계는 작은 묶음의 숫자를 숨긴다(개인이 드러나지 않게)
SALES_PROFILE_GROUPS = ("age_band", "gender", "occupation")
# 한 줄의 구매자 수가 이보다 적으면 그 줄의 숫자를 숨긴다
MIN_GROUP_BUYERS = 5
# 판매 집계의 기간 상한. 넓은 기간은 주문·품목을 통째로 합쳐 서버(연결 하나)를 붙잡는다
MAX_SALES_SUMMARY_DAYS = 93
# 전액 환불된 주문: 환불 합계가 주문 금액 이상이다 (`attributed_orders`·쿠폰 귀속 집계와 같은 기준)
_FULLY_REFUNDED = (
    "o.total_amount > 0 AND COALESCE((SELECT SUM(r.amount) FROM refunds r "
    "WHERE r.order_id = o.order_id), 0) >= o.total_amount"
)
MAX_POINTS = 2016  # 5분 버킷 7일치. 넘으면 잘라서 `truncated`로 알린다
# 지표 시계열 한 번의 기간 상한. 넓은 기간은 결제·주문을 통째로 읽어 서버를 붙잡는다(2026-11-10).
# 더 긴 기간은 부르는 쪽이 하루씩 나눠 부른다(data 커넥터 SERIES_CHUNK = 1일)
MAX_SERIES_SPAN = timedelta(hours=24)

# 주문 금액·쿠폰 지표는 `orders`에서 계산한다. 접수 시각(created_at)이 속한 버킷에 센다.
# - order_amount(매출): 결제가 끝난 주문의 total_amount. 결제 대기·결제 실패·취소는 뺀다
# - coupon_*: 쿠폰을 쓴 주문. 장수를 돌려받은 주문(취소·전액 환불)은 뺀다
#   (`coupon_used_units`와 같은 기준)
_COUPON_KEPT = (
    "o.coupon_id IS NOT NULL AND o.status != 'cancelled' AND NOT (o.total_amount > 0 AND "
    "COALESCE((SELECT SUM(r.amount) FROM refunds r WHERE r.order_id = o.order_id), 0) "
    ">= o.total_amount)"
)
ORDER_VALUE_FILTER = {
    "order_amount": "o.status NOT IN ('pending_payment', 'payment_failed', 'cancelled')",
    "coupon_order_count": _COUPON_KEPT,
    "coupon_discount_amount": _COUPON_KEPT,
}
ORDER_VALUE_METRICS = frozenset(ORDER_VALUE_FILTER)


def _parse_group_by(text: str) -> tuple[str, ...]:
    """`product,age_band`처럼 쉼표로 이은 기준을 확인한다. 정한 값이 아니거나 비면 422다."""
    groups = tuple(dict.fromkeys(part.strip() for part in text.split(",") if part.strip()))
    bad = [g for g in groups if g not in SALES_GROUPS]
    if not groups or bad:
        raise ShopError(
            "INVALID_GROUP_BY",
            f"group_by는 {', '.join(SALES_GROUPS)} 중에서 쉼표로 이어 고른다",
            422,
        )
    return groups


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _floor(moment: datetime) -> datetime:
    seconds = int(BUCKET.total_seconds())
    return datetime.fromtimestamp(int(moment.timestamp()) // seconds * seconds, UTC)


def median_price(prices: list[int]) -> int:
    """정수 원 중앙값. 짝수 개면 가운데 두 값의 평균을 반올림한다(0.5는 올림)."""
    ordered = sorted(prices)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return math.floor((ordered[middle - 1] + ordered[middle]) / 2 + 0.5)


FAILED_ORDER_STATUSES = ("failed", "timeout")  # `/failed-orders?status=`로 고를 수 있는 결제 상태


class Lookups:
    def __init__(self, db: Db, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.db = db
        self.now = now

    def failed_orders(
        self, query: ShopFailedOrdersQuery, status: str = "failed"
    ) -> ShopFailedOrdersResult:
        """결제 실패(`failed`, 기본) 또는 시간 초과(`timeout`) 결제의 주문.

        시간 초과는 결제사에 요청은 갔는데 응답을 못 받은 결제다. 다시 확인해 정해지기 전까지만
        `timeout`으로 남는다. `failed_at`은 두 경우 모두 결제를 시도한 시각이다.
        """
        if status not in FAILED_ORDER_STATUSES:
            allowed = "·".join(FAILED_ORDER_STATUSES)
            raise ShopError("INVALID_QUERY", f"status는 {allowed} 중 하나다")
        if query.to <= query.from_:
            raise ShopError("INVALID_RANGE", "to는 from보다 뒤여야 한다")
        sql = (
            "SELECT p.order_id, o.buyer_id, p.amount, p.failure_code, p.created_at, "
            "  (SELECT pr.category_code FROM order_items i JOIN products pr "
            "   ON pr.product_id = i.product_id WHERE i.order_id = p.order_id "
            "   ORDER BY i.rowid LIMIT 1) AS category "
            "FROM payments p JOIN orders o ON o.order_id = p.order_id "
            "WHERE p.status = ? AND p.tenant_id = ? "
            "AND p.created_at >= ? AND p.created_at < ?"
        )
        args: list[Any] = [status, query.tenant_id, _iso(query.from_), _iso(query.to)]
        if query.category:
            sql += " AND category = ?"
            args.append(query.category)
        sql += " ORDER BY p.created_at, p.payment_id LIMIT ?"
        args.append(query.limit + 1)  # 하나 더 읽어 잘림을 안다
        with self.db.tx() as conn:
            rows = conn.execute(sql, args).fetchall()
        orders = [
            FailedOrder(
                order_id=row["order_id"],
                buyer_id=row["buyer_id"],
                amount=row["amount"],
                category=row["category"] or "unknown",
                failed_at=_parse(row["created_at"]),
                reason=row["failure_code"] or "UNKNOWN",
                tenant_id=query.tenant_id,
            )
            for row in rows[: query.limit]
        ]
        return ShopFailedOrdersResult(
            orders=orders, collected_at=self.now(), truncated=len(rows) > query.limit
        )

    def customer_export(self, query: ShopCustomersExportQuery) -> ShopCustomersExportResult:
        """고객 내보내기(`shop_customers` 계약): 가명 ID와 값이 있는 연령대·성별·직업군뿐이다.

        `(change_seq, buyer_id)`(바뀐 순서, 없으면 0) 순으로 `limit`줄씩 준다. 커서는 마지막 줄의
        그 두 값을 이은 불투명 문자열이고, 프로필을 고친 구매자는 다음 읽기에 다시 나온다.
        """
        cursor, limit = query.cursor, query.limit
        after: tuple[int, str] | None = None
        if cursor:
            try:
                raw = base64.urlsafe_b64decode(cursor.encode()).decode()
                seq_text, _, buyer_id = raw.partition("|")
                seq = int(seq_text)
            except (binascii.Error, UnicodeError, ValueError):
                seq, buyer_id = 0, ""
            if not buyer_id:
                raise ShopError("INVALID_CURSOR", "cursor가 올바르지 않다", 400)
            after = (seq, buyer_id)
        sql = (
            "SELECT buyer_id, age_band, gender, occupation, created_at, updated_at, "
            "COALESCE(change_seq, 0) AS seq FROM buyers WHERE tenant_id = ?"
        )
        params: list[Any] = [TENANT_ID]
        if after:
            sql += (
                " AND (COALESCE(change_seq, 0) > ?"
                " OR (COALESCE(change_seq, 0) = ? AND buyer_id > ?))"
            )
            params += [after[0], after[0], after[1]]
        sql += " ORDER BY COALESCE(change_seq, 0), buyer_id LIMIT ?"
        params.append(limit + 1)
        with self.db.tx() as conn:
            rows = conn.execute(sql, params).fetchall()
        page = rows[:limit]
        customers = [
            CustomerRow(
                buyer_id=r["buyer_id"],
                joined_at=_parse(r["created_at"]),
                updated_at=_parse(r["updated_at"] or r["created_at"]),
                attributes={key: r[key] for key in ("age_band", "gender", "occupation") if r[key]},
                tenant_id=query.tenant_id,
            )
            for r in page
        ]
        next_cursor = cursor or None  # 빈 페이지면 받은 커서를 그대로 돌려준다
        if page:
            last = page[-1]
            raw = f"{last['seq']}|{last['buyer_id']}"
            next_cursor = base64.urlsafe_b64encode(raw.encode()).decode()
        return ShopCustomersExportResult(
            customers=customers,
            next_cursor=next_cursor,
            has_more=len(rows) > limit,
            collected_at=self.now(),
            tenant_id=query.tenant_id,
        )

    def attributed_orders(
        self,
        since: datetime,
        until: datetime,
        campaign_id: str | None = None,
        source: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        """유입 출처(`src_*`)가 하나라도 있는 주문. 접수 시각이 `since` 이상 `until` 미만이다.

        쿠폰 귀속 집계(`coupon_used_units`)와 같은 기준으로 취소된 주문과 전액 환불된 주문은 뺀다.
        결제 대기·실패 주문은 `status`로 구분해 읽는 쪽이 고른다. `buyer_id`·결제 수단 같은
        개인 식별값은 돌려주지 않는다. 기간은 `MAX_ATTRIBUTION_DAYS`일까지다.
        """
        if until <= since:
            raise ShopError("INVALID_RANGE", "to는 from보다 뒤여야 한다")
        if until - since > timedelta(days=MAX_ATTRIBUTION_DAYS):
            raise ShopError(
                "RANGE_TOO_LONG", f"기간은 {MAX_ATTRIBUTION_DAYS}일을 넘을 수 없다", 400
            )
        sql = (
            "SELECT o.order_id, o.created_at, o.total_amount, o.status, o.src_source, "
            "o.src_medium, o.src_campaign FROM orders o "
            "WHERE o.tenant_id = ? AND o.created_at >= ? AND o.created_at < ? "
            "AND (o.src_source IS NOT NULL OR o.src_medium IS NOT NULL "
            "OR o.src_campaign IS NOT NULL) AND o.status != 'cancelled' "
            "AND NOT (o.total_amount > 0 AND COALESCE((SELECT SUM(r.amount) FROM refunds r "
            "WHERE r.order_id = o.order_id), 0) >= o.total_amount)"
        )
        args: list[Any] = [TENANT_ID, _iso(since), _iso(until)]
        if campaign_id:
            sql += " AND o.src_campaign = ?"
            args.append(campaign_id)
        if source:
            sql += " AND o.src_source = ?"
            args.append(source)
        sql += " ORDER BY o.created_at, o.order_id LIMIT ?"
        args.append(limit + 1)  # 하나 더 읽어 잘림을 안다
        with self.db.tx() as conn:
            rows = conn.execute(sql, args).fetchall()
            truncated = len(rows) > limit
            rows = rows[:limit]
            items: dict[str, list[dict[str, Any]]] = defaultdict(list)
            ids = [r["order_id"] for r in rows]
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ",".join("?" * len(chunk))
                for item in conn.execute(
                    f"SELECT order_id, product_id, qty, unit_price FROM order_items "
                    f"WHERE order_id IN ({marks}) ORDER BY rowid",
                    chunk,
                ):
                    items[item["order_id"]].append(
                        {
                            "product_id": item["product_id"],
                            "qty": item["qty"],
                            "unit_price": item["unit_price"],
                        }
                    )
        orders = [
            {
                "order_id": r["order_id"],
                "created_at": r["created_at"],
                "total_amount": r["total_amount"],
                "status": r["status"],
                "items": items[r["order_id"]],
                "src_source": r["src_source"],
                "src_medium": r["src_medium"],
                "src_campaign": r["src_campaign"],
            }
            for r in rows
        ]
        return {
            "orders": orders,
            "order_count": len(orders),
            "total_amount": sum(o["total_amount"] for o in orders),
            "truncated": truncated,
        }

    def sales_summary(
        self, since: datetime, until: datetime, group_by: str = "product"
    ) -> dict[str, Any]:
        """결제가 끝난 주문의 판매 수량을 기준별로 합친다.

        접수 시각이 `since` 이상 `until` 미만인 주문이다.

        `group_by`는 쉼표로 이은 `product`·`age_band`·`category`·`gender`·`occupation`·`genre`
        (정한 값만, 다른 값은 422). `genre`는 상품 장르 코드(`genre_code`, 모르면 null)로 묶는다.
        한 줄은 기준 값의 조합 하나이고 두 판매 수량을 같이 준다.
        - `gross_qty`: 결제가 끝난 주문의 판매 수량 (클레임 비율의 분모처럼 환불된 주문도 센다)
        - `net_qty`: 그중 전액 환불된 주문을 뺀 수량 (`attributed_orders`와 같은 기준.
          취소된 주문은 결제가 끝난 적이 없어 처음부터 빠진다)
        `age_band`·`gender`·`occupation`이 null인 줄은 그것을 모르는 구매자다
        (채워 넣지 않는다). 구매자 속성(`age_band`·`gender`·`occupation`)으로 묶으면 한 줄의
        구매자가 `MIN_GROUP_BUYERS`명 미만일 때 `order_count`·`gross_qty`·`net_qty`를 null로
        숨기고 `suppressed`를 true로 한다.
        구매자 ID·이름·결제 수단 같은 개인 식별값은 돌려주지 않는다.
        기간은 `MAX_SALES_SUMMARY_DAYS`일까지다.
        """
        groups = _parse_group_by(group_by)
        if until <= since:
            raise ShopError("INVALID_RANGE", "to는 from보다 뒤여야 한다")
        if until - since > timedelta(days=MAX_SALES_SUMMARY_DAYS):
            raise ShopError(
                "RANGE_TOO_LONG", f"기간은 {MAX_SALES_SUMMARY_DAYS}일을 넘을 수 없다", 400
            )
        columns = {
            "product": ("i.product_id", "product_id"),
            "age_band": ("b.age_band", "age_band"),
            "gender": ("b.gender", "gender"),
            "occupation": ("b.occupation", "occupation"),
            "category": ("pr.category_code", "category_code"),
            "genre": ("pr.genre_code", "genre_code"),  # 상품 장르 코드(모르면 null)
        }
        select = ", ".join(f"{columns[g][0]} AS {columns[g][1]}" for g in groups)
        by = ", ".join(columns[g][0] for g in groups)  # 별칭이 아니라 열로 묶는다(상품 표와 겹침)
        joins = ""
        profile = any(g in SALES_PROFILE_GROUPS for g in groups)
        if profile:
            joins += " LEFT JOIN buyers b ON b.buyer_id = k.buyer_id"
        if "category" in groups or "genre" in groups:
            joins += " LEFT JOIN products pr ON pr.product_id = i.product_id"
        sql = (  # noqa: S608 - 열 이름은 위 고정 표에서만 온다 (고객 입력이 아니다)
            "WITH k AS (SELECT o.order_id, o.buyer_id, "
            f"CASE WHEN {_FULLY_REFUNDED} THEN 1 ELSE 0 END AS refunded_all "
            "FROM orders o WHERE o.tenant_id = ? AND o.status = 'paid' "
            "AND o.created_at >= ? AND o.created_at < ?) "
            f"SELECT {select}, COUNT(DISTINCT k.order_id) AS order_count, "
            "COUNT(DISTINCT k.buyer_id) AS buyer_count, "
            "SUM(i.qty) AS gross_qty, "
            "SUM(CASE WHEN k.refunded_all = 0 THEN i.qty ELSE 0 END) AS net_qty "
            f"FROM k JOIN order_items i ON i.order_id = k.order_id{joins} "
            f"GROUP BY {by} ORDER BY {by}"
        )
        with self.db.tx() as conn:
            rows = conn.execute(sql, [TENANT_ID, _iso(since), _iso(until)]).fetchall()
        out = []
        for r in rows:
            row = dict(r)
            buyers = row.pop("buyer_count")
            if profile:  # 작은 묶음은 숫자를 숨긴다. 줄은 남기고 `suppressed`로 알린다
                hide = buyers < MIN_GROUP_BUYERS
                if hide:
                    row.update(order_count=None, gross_qty=None, net_qty=None)
                row["suppressed"] = hide
            out.append(row)
        return {
            "from": _iso(since),
            "to": _iso(until),
            "group_by": list(groups),
            "rows": out,
            "collected_at": _iso(self.now()),
        }

    def metric_series(self, query: ShopMetricSeriesQuery) -> ShopMetricSeriesResult:
        if query.to <= query.from_:
            raise ShopError("INVALID_RANGE", "to는 from보다 뒤여야 한다")
        if query.to - query.from_ > MAX_SERIES_SPAN:
            raise ShopError(
                "RANGE_TOO_LONG", "기간은 24시간을 넘을 수 없다(하루씩 나눠 부른다)", 422
            )
        start = _floor(query.from_)
        with self.db.tx() as conn:
            if query.metric == "order_count":
                rows = conn.execute(
                    "SELECT created_at, 1 AS ok FROM orders "
                    "WHERE tenant_id = ? AND created_at >= ? AND created_at < ?",
                    (query.tenant_id, _iso(start), _iso(query.to)),
                ).fetchall()
            elif query.metric in ORDER_VALUE_METRICS:
                rows = conn.execute(
                    "SELECT created_at, total_amount, coupon_discount, status, 1 AS ok "
                    "FROM orders o "
                    "WHERE tenant_id = ? AND created_at >= ? AND created_at < ? AND "
                    + ORDER_VALUE_FILTER[query.metric],
                    (query.tenant_id, _iso(start), _iso(query.to)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT created_at, status = 'succeeded' AS ok, order_id FROM payments "
                    "WHERE tenant_id = ? AND created_at >= ? AND created_at < ?",
                    (query.tenant_id, _iso(start), _iso(query.to)),
                ).fetchall()
        buckets: dict[datetime, list[Any]] = defaultdict(list)
        for row in rows:
            buckets[_floor(_parse(row["created_at"]))].append(row)

        points: list[MetricPoint] = []
        truncated = False
        moment = start
        while moment < query.to:
            if len(points) >= MAX_POINTS:
                truncated = True
                break
            value = self._value(query.metric, buckets.get(moment, []))
            if value is not None:
                points.append(MetricPoint(ts=moment, value=value))
            moment += BUCKET
        return ShopMetricSeriesResult(
            metric=query.metric,
            bucket=query.bucket,
            points=points,
            collected_at=self.now(),
            truncated=truncated,
            tenant_id=query.tenant_id,
        )

    @staticmethod
    def _value(metric: str, rows: list[Any]) -> float | None:
        failed = [row for row in rows if not row["ok"]] if metric != "order_count" else []
        if metric == "order_count":
            return float(len(rows))
        if metric == "order_amount":
            return float(sum(row["total_amount"] for row in rows))
        if metric == "coupon_order_count":
            return float(len(rows))
        if metric == "coupon_discount_amount":
            return float(sum(row["coupon_discount"] for row in rows))
        if metric == "payment_count":
            return float(len(rows))
        if metric == "order_fail_count":
            return float(len({row["order_id"] for row in failed}))
        if not rows:  # payment_fail_rate: 결제 시도가 없으면 값이 없다
            return None
        return len(failed) / len(rows)

    def _seller(self, conn: Any, seller_id: str, tenant_id: str) -> Any:
        row = conn.execute(
            "SELECT * FROM sellers WHERE seller_id = ? AND tenant_id = ?", (seller_id, tenant_id)
        ).fetchone()
        if row is None:
            raise ShopError("SELLER_NOT_FOUND", "판매자를 찾을 수 없다", 404)
        return row

    def seller_profile(self, query: ShopSellerProfileQuery) -> ShopSellerProfile:
        now = self.now()
        with self.db.tx() as conn:
            seller = self._seller(conn, query.seller_id, query.tenant_id)
            active = conn.execute(
                "SELECT COUNT(*) FROM products "
                "WHERE seller_id = ? AND tenant_id = ? AND active = 1",
                (query.seller_id, query.tenant_id),
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT COUNT(*) FROM products WHERE seller_id = ? AND tenant_id = ? "
                "AND created_at >= ? AND created_at <= ?",
                (query.seller_id, query.tenant_id, _iso(now - LISTING_WINDOW), _iso(now)),
            ).fetchone()[0]
        return ShopSellerProfile(
            seller_id=seller["seller_id"],
            joined_at=_parse(seller["created_at"]),
            business_hash=None,  # sellers 테이블에 사업자 정보가 없다
            business_hash_duplicated=None,
            product_count=active,
            listings_last_24h=recent,
            tenant_id=query.tenant_id,
        )

    def seller_prices(self, query: ShopSellerPricesQuery) -> ShopSellerPricesResult:
        with self.db.tx() as conn:
            self._seller(conn, query.seller_id, query.tenant_id)
            own = conn.execute(
                "SELECT product_id, category_code, price FROM products "
                "WHERE seller_id = ? AND tenant_id = ? AND active = 1 ORDER BY product_id",
                (query.seller_id, query.tenant_id),
            ).fetchall()
            categories = sorted({row["category_code"] for row in own})
            prices: dict[str, list[int]] = defaultdict(list)
            if categories:
                marks = ",".join("?" * len(categories))
                for row in conn.execute(
                    "SELECT category_code, price FROM products WHERE tenant_id = ? AND active = 1 "
                    f"AND category_code IN ({marks})",
                    (query.tenant_id, *categories),
                ).fetchall():
                    prices[row["category_code"]].append(row["price"])
        medians = {category: median_price(values) for category, values in prices.items()}
        return ShopSellerPricesResult(
            items=[
                SellerProductPrice(
                    product_id=row["product_id"],
                    category=row["category_code"],
                    price=row["price"],
                    category_median_price=medians[row["category_code"]],
                )
                for row in own
            ]
        )
