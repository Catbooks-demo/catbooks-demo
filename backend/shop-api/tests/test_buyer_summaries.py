"""판매 기록 범위 조회와 구매자 요약 (로드맵 3단계 3-3, 계약 `shop_sales`·`shop_buyer_summaries`).

- `GET /api/shop/sales-export`: 접수·환불 범위(선택), 둘 다 주면 OR, 인자 없으면 예전과 같음
- `GET /api/shop/buyer-summaries`: 결제 완료 정의(취소·결제 실패·결제 전 제외, 부분 환불 남음),
  `as_of` 전만, 첫·두 번째·마지막 시각, 항목, 속성, 커서, `changed_since`, 권한
"""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.data_access.shop_buyer_summaries import ShopBuyerSummariesResult
from ax_contracts.data_access.shop_sales import ShopSalesExportResult
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

DATA = {"Authorization": "Bearer mock:data"}
MARKETING = {"Authorization": "Bearer mock:marketing"}
SUPPORT = {"Authorization": "Bearer mock:support"}
DETECTOR = {"Authorization": "Bearer mock:dashboard-api"}
EXPORT = "/api/shop/sales-export"
SUMMARIES = "/api/shop/buyer-summaries"
AS_OF = "2026-11-20T15:00:00Z"  # 한국 11/21 0시 = 기준일 11/20 24시


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def _order(  # noqa: PLR0913 - 시험 주문 하나의 모양
    client: TestClient,
    db: Db,
    buyer: str,
    created: str,
    items: list[tuple[str, int]] | None = None,
    pay: bool = True,
    status: str | None = None,
    src: str | None = None,
    updated: str | None = None,
) -> dict:
    order = client.post(
        "/api/shop/orders",
        json={
            "buyer_id": buyer,
            "items": [{"product_id": p, "qty": q} for p, q in items or [("prd_0001", 1)]],
        },
    ).json()["data"]
    if pay:
        order = client.post(f"/api/shop/orders/{order['order_id']}/payments", json={}).json()[
            "data"
        ]
    with db.tx() as conn:
        conn.execute(
            "UPDATE orders SET created_at = ?, updated_at = ?, src_source = ?,"
            " status = COALESCE(?, status) WHERE order_id = ?",
            (created, updated or created, src, status, order["order_id"]),
        )
    return order


def _refund(db: Db, order: dict, amount: int, at: str) -> None:
    payment = order["payments"][0]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO refunds VALUES (?, ?, ?, ?, ?, 'shop-demo', NULL, ?)",
            (f"rfd_{at}", payment["payment_id"], order["order_id"], amount, f"k_{at}", at),
        )


def _export(client: TestClient, **params: str) -> ShopSalesExportResult:
    res = client.get(EXPORT, params=params, headers=DATA)
    assert res.status_code == 200, res.text
    return ShopSalesExportResult.model_validate(res.json()["data"])


def _summaries(client: TestClient, **params: str | int) -> ShopBuyerSummariesResult:
    res = client.get(SUMMARIES, params={"as_of": AS_OF, **params}, headers=DATA)
    assert res.status_code == 200, res.text
    return ShopBuyerSummariesResult.model_validate(res.json()["data"])


# --- 판매 기록 범위 조회 -------------------------------------------------------------


def test_sales_export_ranges_created_refunded_or_and_unchanged_without(
    client: TestClient, db: Db
) -> None:
    old = _order(client, db, "buy_0001", "2026-10-01T03:00:00Z")
    day = _order(client, db, "buy_0002", "2026-11-20T03:00:00Z")
    late = _order(client, db, "buy_0003", "2026-11-21T03:00:00Z")
    _refund(db, old, 3000, "2026-11-20T05:00:00Z")  # 옛 주문의 그날 환불
    ids = {o["order_id"]: n for n, o in {"old": old, "day": day, "late": late}.items()}

    def names(page: ShopSalesExportResult) -> list[str]:
        return sorted(ids[o.order_id] for o in page.orders)

    assert names(_export(client)) == ["day", "late", "old"]  # 인자 없으면 예전 그대로
    window = {"created_from": "2026-11-19T15:00:00Z", "created_to": "2026-11-20T15:00:00Z"}
    assert names(_export(client, **window)) == ["day"]  # [from, to)
    refunds = {"refunded_from": "2026-11-19T15:00:00Z", "refunded_to": "2026-11-20T15:00:00Z"}
    assert names(_export(client, **refunds)) == ["old"]
    assert names(_export(client, **window, **refunds)) == ["day", "old"]  # 둘 다 주면 OR
    assert names(_export(client, created_from="2026-11-21T03:00:00Z")) == ["late"]  # 끝 포함
    assert names(_export(client, created_to="2026-11-21T03:00:00Z")) == ["day", "old"]  # 끝 제외
    # 범위 안에서도 커서로 이어 받는다
    first = _export(client, limit="1", **window, **refunds)
    assert len(first.orders) == 1 and first.has_more
    rest = _export(client, cursor=first.next_cursor or "", **window, **refunds)
    assert {o.order_id for o in first.orders + rest.orders} == {old["order_id"], day["order_id"]}
    assert not rest.has_more
    # 시간대 없는 시각은 400
    res = client.get(EXPORT, params={"created_from": "2026-11-20T00:00:00"}, headers=DATA)
    assert res.status_code == 400


# --- 구매자 요약 ----------------------------------------------------------------------


def test_buyer_summary_paid_definition_times_items_and_attributes(
    client: TestClient, db: Db
) -> None:
    _order(client, db, "buy_0001", "2026-11-01T01:00:00Z",
           [("prd_0001", 1), ("prd_0004", 2)], src="naver")  # fmt: skip
    _order(client, db, "buy_0001", "2026-11-03T01:00:00Z", pay=False)  # 결제 전: 안 셈
    _order(client, db, "buy_0001", "2026-11-04T01:00:00Z", status="cancelled")  # 취소: 안 셈
    part = _order(client, db, "buy_0001", "2026-11-05T01:00:00Z", [("prd_0001", 2)], src="kakao")
    _refund(db, part, 1000, "2026-11-06T01:00:00Z")  # 부분 환불: 결제 완료에 남는다
    _order(client, db, "buy_0001", "2026-11-10T01:00:00Z", [("prd_0007", 1)])
    _order(client, db, "buy_0001", AS_OF)  # as_of 그 시각은 빠진다(전만)
    with db.tx() as conn:
        conn.execute(
            "UPDATE buyers SET age_band = '20s', gender = 'female', occupation = NULL "
            "WHERE buyer_id = 'buy_0001'"
        )
    page = _summaries(client)
    assert [b.buyer_id for b in page.buyers] == ["buy_0001"]
    b = page.buyers[0]
    assert b.paid_orders == 3
    assert b.first_paid_at.isoformat() == "2026-11-01T01:00:00+00:00"
    assert b.second_paid_at is not None and b.second_paid_at.isoformat().startswith("2026-11-05")
    assert b.last_paid_at is not None and b.last_paid_at.isoformat().startswith("2026-11-10")
    assert b.first_src_source == "naver"  # 첫 결제 완료 주문의 유입
    assert b.attributes == {"age_band": "20s", "gender": "female"}  # 값이 있는 것만
    items = {(i.level, i.code): (i.paid_orders, i.first_paid_at.day, i.last_paid_at.day)
             for i in b.items}  # fmt: skip
    assert items == {
        ("category", "book"): (3, 1, 10),  # 같은 주문의 같은 분류 여러 줄도 주문 1
        ("category", "stationery"): (1, 1, 1),
        ("product", "prd_0001"): (2, 1, 5),
        ("product", "prd_0004"): (1, 1, 1),
        ("product", "prd_0007"): (1, 10, 10),
    }  # 장르가 없는 상품은 genre 항목이 없다
    assert page.as_of.isoformat() == "2026-11-20T15:00:00+00:00" and not page.has_more
    raw = client.get(SUMMARIES, params={"as_of": AS_OF}, headers=DATA).json()["data"]
    assert set(raw["buyers"][0]) <= {
        "buyer_id", "first_paid_at", "second_paid_at", "last_paid_at", "paid_orders",
        "first_src_source", "attributes", "items", "tenant_id",
    }  # 이름·연락처·주소 없음  # fmt: skip


def test_buyer_with_only_cancelled_orders_has_no_paid_times(client: TestClient, db: Db) -> None:
    _order(client, db, "buy_0002", "2026-11-02T01:00:00Z", status="cancelled")
    _order(client, db, "buy_0003", "2026-11-25T01:00:00Z")  # as_of 뒤 주문뿐: 안 나온다
    page = _summaries(client)
    assert [b.buyer_id for b in page.buyers] == ["buy_0002"]
    b = page.buyers[0]
    assert (b.first_paid_at, b.second_paid_at, b.last_paid_at, b.paid_orders, b.items) == (
        None, None, None, 0, []
    )  # fmt: skip


def test_buyer_summaries_cursor_changed_since_and_errors(client: TestClient, db: Db) -> None:
    for n, buyer in enumerate(("buy_0001", "buy_0002", "buy_0003"), start=1):
        _order(client, db, buyer, f"2026-11-0{n}T01:00:00Z")
    first = _summaries(client, limit=2)
    assert [b.buyer_id for b in first.buyers] == ["buy_0001", "buy_0002"] and first.has_more
    rest = _summaries(client, limit=2, cursor=first.next_cursor or "")
    assert [b.buyer_id for b in rest.buyers] == ["buy_0003"] and not rest.has_more
    empty = _summaries(client, cursor=rest.next_cursor or "")
    assert empty.buyers == [] and empty.next_cursor == rest.next_cursor  # 받은 커서 그대로
    # changed_since: 그 뒤 주문이 바뀐(환불 등) 구매자와 프로필을 고친 구매자만
    with db.tx() as conn:
        conn.execute(
            "UPDATE orders SET updated_at = '2026-11-15T00:00:00Z' WHERE buyer_id = 'buy_0002'"
        )
        conn.execute(
            "UPDATE buyers SET created_at = '2026-01-01T00:00:00Z', updated_at = CASE buyer_id"
            " WHEN 'buy_0003' THEN '2026-11-16T00:00:00Z' ELSE '2026-01-01T00:00:00Z' END"
        )
    changed = _summaries(client, changed_since="2026-11-10T00:00:00Z")
    assert [b.buyer_id for b in changed.buyers] == ["buy_0002", "buy_0003"]
    assert client.get(SUMMARIES, params={"as_of": AS_OF, "cursor": "!!"}, headers=DATA
                      ).status_code == 400  # fmt: skip
    assert client.get(SUMMARIES, params={"as_of": "2026-11-20T00:00:00"}, headers=DATA
                      ).status_code == 400  # 시간대 없음  # fmt: skip
    assert client.get(SUMMARIES, params={"as_of": AS_OF, "limit": 1001}, headers=DATA
                      ).status_code == 400  # fmt: skip
    assert client.get(SUMMARIES, headers=DATA).status_code in (400, 422)  # as_of 필수


def test_buyer_summaries_permissions(client: TestClient) -> None:
    params = {"as_of": AS_OF}
    assert client.get(SUMMARIES, params=params).status_code == 401
    assert client.get(SUMMARIES, params=params, headers=MARKETING).status_code == 403
    assert client.get(SUMMARIES, params=params, headers=SUPPORT).status_code == 403
    assert client.get(SUMMARIES, params=params, headers=DETECTOR).status_code == 200
    assert client.get(SUMMARIES, params=params, headers=DATA).status_code == 200
