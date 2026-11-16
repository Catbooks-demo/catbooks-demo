"""판매 집계: 구매자 속성(연령대·성별·직업군)으로 묶으면 구매자 5명 미만인 줄의 숫자를 숨긴다."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.lookups import MIN_GROUP_BUYERS
from ax_shop_api.main import create_app

SUPPORT = {"Authorization": "Bearer mock:support"}
PRODUCT = "prd_0001"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _buyers(client: TestClient) -> list[str]:
    return [u["buyer_id"] for u in client.get("/api/shop/users").json()["data"]]


def _buy(client: TestClient, buyer: str, qty: int = 1, times: int = 1) -> None:
    for _ in range(times):
        done = client.post(
            "/api/shop/orders",
            json={"buyer_id": buyer, "items": [{"product_id": PRODUCT, "qty": qty}]},
        )
        order_id = done.json()["data"]["order_id"]
        paid = client.post(f"/api/shop/orders/{order_id}/payments", json={"simulate": "success"})
        assert paid.status_code == 200, paid.text


def _rows(client: TestClient, group_by: str) -> list[dict]:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    now = datetime.now(UTC)
    params = {
        "from": (now - timedelta(days=1)).strftime(fmt),
        "to": (now + timedelta(days=1)).strftime(fmt),
        "group_by": group_by,
    }
    res = client.get("/api/shop/sales-summary", params=params, headers=SUPPORT)
    assert res.status_code == 200, res.text
    return res.json()["data"]["rows"]


def test_the_threshold_is_five_buyers() -> None:
    assert MIN_GROUP_BUYERS == 5


def test_a_group_below_five_buyers_hides_its_numbers(client: TestClient) -> None:
    ids = _buyers(client)
    for buyer in ids[:4]:  # 20대 여성 4명
        client.patch(f"/api/shop/users/{buyer}", json={"age_band": "20s", "gender": "female"})
        _buy(client, buyer, qty=2)
    rows = _rows(client, "age_band,gender")
    assert rows == [
        {
            "age_band": "20s",
            "gender": "female",
            "order_count": None,
            "gross_qty": None,
            "net_qty": None,
            "suppressed": True,
        }
    ]


def test_five_buyers_show_their_numbers(client: TestClient) -> None:
    ids = _buyers(client)
    for buyer in ids[:5]:
        client.patch(f"/api/shop/users/{buyer}", json={"gender": "male", "occupation": "student"})
        _buy(client, buyer, qty=3)
    for group in ("gender", "occupation", "gender,occupation"):
        (row,) = _rows(client, group)
        assert row["suppressed"] is False
        assert (row["order_count"], row["gross_qty"], row["net_qty"]) == (5, 15, 15), group


def test_one_buyer_with_many_orders_is_still_one_buyer(client: TestClient) -> None:
    buyer = _buyers(client)[0]
    client.patch(f"/api/shop/users/{buyer}", json={"gender": "female"})
    _buy(client, buyer, qty=1, times=6)  # 주문 6건이어도 구매자는 1명이다
    (row,) = _rows(client, "gender")
    assert row["suppressed"] is True and row["order_count"] is None


def test_each_row_is_judged_on_its_own(client: TestClient) -> None:
    ids = _buyers(client)
    for buyer in ids[:5]:
        client.patch(f"/api/shop/users/{buyer}", json={"gender": "female"})
        _buy(client, buyer)
    client.patch(f"/api/shop/users/{ids[5]}", json={"gender": "male"})
    _buy(client, ids[5])
    by = {r["gender"]: r for r in _rows(client, "gender")}
    assert by["female"]["suppressed"] is False and by["female"]["gross_qty"] == 5
    assert by["male"]["suppressed"] is True and by["male"]["gross_qty"] is None


def test_grouping_without_a_buyer_attribute_is_unchanged(client: TestClient) -> None:
    _buy(client, _buyers(client)[0], qty=2)
    for group in ("product", "category", "product,category"):
        rows = _rows(client, group)
        assert rows and all("suppressed" not in r and r["gross_qty"] == 2 for r in rows), group
