"""판매 집계 `GET /api/shop/sales-summary`: 결제가 끝난 주문의 판매 수량을 기준별로 합친다."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.lookups import MAX_SALES_SUMMARY_DAYS
from ax_shop_api.main import create_app

URL = "/api/shop/sales-summary"
SUPPORT = {"Authorization": "Bearer mock:support"}
DATA = {"Authorization": "Bearer mock:data"}
ADMIN = {"Authorization": "Bearer mock:admin"}
FINANCE = {"Authorization": "Bearer mock:finance"}
T0 = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)
KEYS = ("product_id", "age_band", "category_code")


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    # 이 파일은 집계 값 자체를 본다 — 작은 묶음 숨기기는 test_sales_summary_suppression.py에서 본다
    monkeypatch.setattr("ax_shop_api.lookups.MIN_GROUP_BUYERS", 1)
    events.reset_mock_bus()


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def _products(client: TestClient) -> dict[str, str]:
    """분류 코드 → 그 분류의 첫 상품 ID."""
    found: dict[str, str] = {}
    for p in client.get("/api/shop/products").json()["data"]:
        found.setdefault(p["category_code"], p["product_id"])
    return found


def _order(
    client: TestClient,
    db: Db,
    product: str,
    qty: int = 1,
    *,
    buyer: str = "buy_0001",
    at: datetime = T0,
    pay: str | None = "success",
) -> dict:
    """주문 하나를 만들고(pay가 있으면 결제하고) 접수 시각을 at으로 맞춘다."""
    order = client.post(
        "/api/shop/orders",
        json={"buyer_id": buyer, "items": [{"product_id": product, "qty": qty}]},
    ).json()["data"]
    if pay:
        order_id = order["order_id"]
        order = client.post(f"/api/shop/orders/{order_id}/payments", json={"simulate": pay}).json()[
            "data"
        ]
    with db.tx() as conn:
        conn.execute(
            "UPDATE orders SET created_at = ? WHERE order_id = ?", (_iso(at), order["order_id"])
        )
    return order


def _refund(client: TestClient, order: dict, amount: int) -> None:
    order_id = order["order_id"]
    body = {
        "refund_amount": amount,
        "idempotency_key": f"k-{order_id}-{amount}",
        "context_id": "ctx_shop_aaa",
    }
    payment_id = order["payments"][0]["payment_id"]
    done = client.post(f"/api/shop/payments/{payment_id}/refund", json=body, headers=SUPPORT)
    assert done.status_code == 201, done.text


def _get(client: TestClient, **params: str) -> dict:
    query = {"from": _iso(T0 - timedelta(hours=1)), "to": _iso(T0 + timedelta(hours=1))} | params
    res = client.get(URL, params=query, headers=SUPPORT)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _rows(client: TestClient, **params: str) -> dict:
    """줄을 (상품, 연령대, 분류) 중 묶은 기준의 값 조합으로 찾을 수 있게 한다."""
    return {tuple(v for k, v in r.items() if k in KEYS): r for r in _get(client, **params)["rows"]}


def test_only_paid_orders_are_counted_by_product(client: TestClient, db: Db) -> None:
    book = _products(client)["book"]
    _order(client, db, book, 2)
    _order(client, db, book, 1)
    _order(client, db, book, 5, pay="failure")  # 결제 실패
    _order(client, db, book, 7, pay=None)  # 결제 전
    rows = _get(client)["rows"]
    assert rows == [{"product_id": book, "order_count": 2, "gross_qty": 3, "net_qty": 3}]


def test_fully_refunded_orders_leave_net_qty_but_stay_in_gross_qty(
    client: TestClient, db: Db
) -> None:
    book = _products(client)["book"]
    _order(client, db, book, 2)
    full = _order(client, db, book, 3)
    partial = _order(client, db, book, 4)
    _refund(client, full, full["total_amount"])  # 전액 환불
    _refund(client, partial, 1000)  # 일부 환불은 그대로 센다
    row = _get(client)["rows"][0]
    assert (row["order_count"], row["gross_qty"], row["net_qty"]) == (3, 9, 6)


def test_group_by_age_band_keeps_unknown_buyers_as_null(client: TestClient, db: Db) -> None:
    book = _products(client)["book"]
    client.patch("/api/shop/users/buy_0001", json={"age_band": "20s"})
    client.patch("/api/shop/users/buy_0002", json={"age_band": "30s"})
    _order(client, db, book, 1, buyer="buy_0001")
    _order(client, db, book, 2, buyer="buy_0001")
    _order(client, db, book, 4, buyer="buy_0002")
    _order(client, db, book, 8, buyer="buy_0003")  # 연령대를 모르는 구매자
    rows = _rows(client, group_by="age_band")
    assert {k: r["gross_qty"] for k, r in rows.items()} == {(None,): 8, ("20s",): 3, ("30s",): 4}
    assert all("buyer_id" not in r for r in rows.values())


def test_group_by_category_and_combinations(client: TestClient, db: Db) -> None:
    prods = _products(client)
    assert len(prods) >= 2, prods
    other_category = next(c for c in prods if c != "book")
    book, other = prods["book"], prods[other_category]
    client.patch("/api/shop/users/buy_0001", json={"age_band": "20s"})
    _order(client, db, book, 2, buyer="buy_0001")
    _order(client, db, other, 3, buyer="buy_0001")
    _order(client, db, book, 5, buyer="buy_0002")
    by_category = _rows(client, group_by="category")
    assert {k[0]: r["gross_qty"] for k, r in by_category.items()} == {
        "book": 7,
        other_category: 3,
    }
    both = _rows(client, group_by="product,age_band")
    assert both[(book, "20s")]["gross_qty"] == 2
    assert both[(book, None)]["gross_qty"] == 5
    assert both[(other, "20s")]["gross_qty"] == 3
    assert _get(client, group_by="age_band,product,age_band")["group_by"] == [
        "age_band",
        "product",
    ]


@pytest.mark.parametrize("bad", ["", "price", "product,price", ",", "Product"])
def test_group_by_accepts_only_the_listed_values(client: TestClient, bad: str) -> None:
    res = client.get(
        URL,
        params={"from": _iso(T0), "to": _iso(T0 + timedelta(hours=1)), "group_by": bad},
        headers=SUPPORT,
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_GROUP_BY"


def test_range_rules(client: TestClient) -> None:
    def call(**params: str):  # noqa: ANN202
        return client.get(URL, params=params, headers=SUPPORT)

    naive = call(**{"from": "2026-11-02T06:00:00", "to": "2026-11-02T07:00:00"})
    assert naive.status_code == 400 and naive.json()["error"]["code"] == "INVALID_QUERY"
    backwards = call(**{"from": _iso(T0), "to": _iso(T0)})
    assert backwards.status_code == 400 and backwards.json()["error"]["code"] == "INVALID_RANGE"
    ok = call(**{"from": _iso(T0), "to": _iso(T0 + timedelta(days=MAX_SALES_SUMMARY_DAYS))})
    assert ok.status_code == 200
    over = T0 + timedelta(days=MAX_SALES_SUMMARY_DAYS, seconds=1)
    too_long = call(**{"from": _iso(T0), "to": _iso(over)})
    assert too_long.status_code == 400 and too_long.json()["error"]["code"] == "RANGE_TOO_LONG"
    assert call(to=_iso(T0)).status_code == 422  # from 없음


def test_from_is_inclusive_and_to_is_exclusive(client: TestClient, db: Db) -> None:
    book = _products(client)["book"]
    _order(client, db, book, 1, at=T0)  # from과 같은 시각: 센다
    _order(client, db, book, 2, at=T0 + timedelta(hours=1))  # to와 같은 시각: 안 센다
    _order(client, db, book, 4, at=T0 - timedelta(seconds=1))  # from 직전: 안 센다
    rows = _get(client, **{"from": _iso(T0), "to": _iso(T0 + timedelta(hours=1))})["rows"]
    assert [r["gross_qty"] for r in rows] == [1]


def test_only_data_support_and_admin_may_read(client: TestClient) -> None:
    params = {"from": _iso(T0), "to": _iso(T0 + timedelta(hours=1))}
    assert client.get(URL, params=params).status_code == 401
    assert client.get(URL, params=params, headers=FINANCE).status_code == 403
    for headers in (SUPPORT, DATA, ADMIN):
        assert client.get(URL, params=params, headers=headers).status_code == 200


def test_the_response_has_no_personal_identifiers(client: TestClient, db: Db) -> None:
    _order(client, db, _products(client)["book"], 1)
    data = _get(client, group_by="product,age_band,category")
    allowed = {
        "product_id",
        "age_band",
        "category_code",
        "order_count",
        "gross_qty",
        "net_qty",
        "suppressed",
    }
    assert data["rows"] and all(set(r) <= allowed for r in data["rows"])
    assert set(data) == {"from", "to", "group_by", "rows", "collected_at"}
