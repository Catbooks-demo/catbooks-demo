"""판매 집계의 시간 단위(`period`): 한국 시간 기준 구간 시작일·정렬·부분 구간·숨기기."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

URL = "/api/shop/sales-summary"
SUPPORT = {"Authorization": "Bearer mock:support"}
PRODUCT = "prd_0001"
KST = timezone(timedelta(hours=9))  # 구간은 한국 시간 기준이다(한국은 일광절약시간이 없다)
# 2026-11-02는 월요일이다 (한국 시간으로 적는다)
MON = datetime(2026, 11, 2, 15, 0, tzinfo=KST)
SUN = datetime(2026, 11, 8, 23, 30, tzinfo=KST)  # 같은 주 일요일
NEXT_MON = datetime(2026, 11, 9, 0, 10, tzinfo=KST)  # 다음 주 월요일 (UTC로는 아직 일요일 15:10)
LAST_OCT = datetime(2026, 10, 31, 21, 0, tzinfo=KST)  # 지난달 토요일


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setattr("ax_shop_api.lookups.MIN_GROUP_BUYERS", 1)  # 숨기기는 따로 본다
    events.reset_mock_bus()


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def _order(client: TestClient, db: Db, at: datetime, qty: int = 1, buyer: str = "buy_0001") -> None:
    made = client.post(
        "/api/shop/orders",
        json={"buyer_id": buyer, "items": [{"product_id": PRODUCT, "qty": qty}]},
    ).json()["data"]
    order_id = made["order_id"]
    paid = client.post(f"/api/shop/orders/{order_id}/payments", json={"simulate": "success"})
    assert paid.status_code == 200, paid.text
    with db.tx() as conn:
        conn.execute("UPDATE orders SET created_at = ? WHERE order_id = ?", (_iso(at), order_id))


def _get(client: TestClient, **params: str) -> dict:
    query = {"from": _iso(LAST_OCT - timedelta(days=1)), "to": _iso(NEXT_MON + timedelta(days=40))}
    res = client.get(URL, params=query | params, headers=SUPPORT)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _qty(data: dict) -> dict[str, int]:
    return {r["period_start"]: r["gross_qty"] for r in data["rows"]}


def _seed(client: TestClient, db: Db) -> None:
    _order(client, db, LAST_OCT, 5)
    _order(client, db, MON, 1)
    _order(client, db, MON + timedelta(hours=3), 2, buyer="buy_0002")
    _order(client, db, SUN, 4)
    _order(client, db, NEXT_MON, 8)


def test_without_period_the_shape_is_unchanged(client: TestClient, db: Db) -> None:
    _seed(client, db)
    data = _get(client)
    assert set(data) == {"from", "to", "group_by", "rows", "collected_at"}
    assert all("period_start" not in r for r in data["rows"])


def test_day_buckets_start_on_the_utc_date_and_sort_by_period(client: TestClient, db: Db) -> None:
    _seed(client, db)
    data = _get(client, period="day")
    assert data["period"] == "day" and data["group_by"] == ["product"]
    assert _qty(data) == {"2026-10-31": 5, "2026-11-02": 3, "2026-11-08": 4, "2026-11-09": 8}
    starts = [r["period_start"] for r in data["rows"]]
    assert starts == sorted(starts)


def test_week_buckets_start_on_monday(client: TestClient, db: Db) -> None:
    _seed(client, db)
    # 10/31(토)은 10/26(월) 주, 11/2(월)~11/8(일)은 한 주, 11/9(월)은 다음 주
    assert _qty(_get(client, period="week")) == {
        "2026-10-26": 5,
        "2026-11-02": 7,
        "2026-11-09": 8,
    }


def test_month_buckets_start_on_the_first(client: TestClient, db: Db) -> None:
    _seed(client, db)
    assert _qty(_get(client, period="month")) == {"2026-10-01": 5, "2026-11-01": 15}


def test_only_orders_inside_the_range_are_counted_in_edge_buckets(
    client: TestClient, db: Db
) -> None:
    _seed(client, db)
    # 11/3부터 읽으면 월요일(11/2) 주문 둘은 빠지고 그 주 구간에는 일요일 주문만 남는다
    data = _get(client, period="week", **{"from": _iso(MON + timedelta(days=1))})
    assert _qty(data) == {"2026-11-02": 4, "2026-11-09": 8}


def test_period_combines_with_other_groups_in_period_first_order(
    client: TestClient, db: Db
) -> None:
    client.patch("/api/shop/users/buy_0001", json={"gender": "female"})
    client.patch("/api/shop/users/buy_0002", json={"gender": "male"})
    _seed(client, db)
    data = _get(client, period="week", group_by="gender")
    keyed = {(r["period_start"], r["gender"]): r["gross_qty"] for r in data["rows"]}
    assert keyed == {
        ("2026-10-26", "female"): 5,
        ("2026-11-02", "female"): 5,  # 월요일 1 + 일요일 4
        ("2026-11-02", "male"): 2,
        ("2026-11-09", "female"): 8,
    }
    order = [(r["period_start"], r["gender"]) for r in data["rows"]]
    assert order == sorted(order)


def test_small_groups_are_hidden_per_period_row(
    client: TestClient, db: Db, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ax_shop_api.lookups.MIN_GROUP_BUYERS", 2)
    client.patch("/api/shop/users/buy_0001", json={"gender": "female"})
    client.patch("/api/shop/users/buy_0002", json={"gender": "female"})
    _order(client, db, MON, 1)  # 11/2 구간: 구매자 2명
    _order(client, db, MON + timedelta(hours=1), 1, buyer="buy_0002")
    _order(client, db, NEXT_MON, 7)  # 11/9 구간: 구매자 1명
    rows = {r["period_start"]: r for r in _get(client, period="week", group_by="gender")["rows"]}
    assert rows["2026-11-02"]["suppressed"] is False and rows["2026-11-02"]["gross_qty"] == 2
    assert rows["2026-11-09"]["suppressed"] is True and rows["2026-11-09"]["gross_qty"] is None


@pytest.mark.parametrize("bad", ["year", "DAY", "daily", "1"])
def test_an_unknown_period_is_422(client: TestClient, bad: str) -> None:
    res = client.get(
        URL,
        params={"from": _iso(MON), "to": _iso(MON + timedelta(days=1)), "period": bad},
        headers=SUPPORT,
    )
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_PERIOD"


def test_a_blank_period_means_none(client: TestClient, db: Db) -> None:
    _seed(client, db)
    data = _get(client, period="")
    assert "period" not in data and all("period_start" not in r for r in data["rows"])


def test_buckets_change_at_korean_midnight_not_at_utc_midnight(client: TestClient, db: Db) -> None:
    """UTC 15:00은 한국 0시다. 한국 날짜·월요일·1일 0시에 구간이 바뀐다."""
    before_day = datetime(2026, 11, 1, 14, 59, 59, tzinfo=UTC)  # 한국 11/1(일) 23:59:59
    on_day = datetime(2026, 11, 1, 15, 0, 0, tzinfo=UTC)  # 한국 11/2(월) 00:00:00
    _order(client, db, before_day, 1)
    _order(client, db, on_day, 2)
    assert _qty(_get(client, period="day")) == {"2026-11-01": 1, "2026-11-02": 2}
    assert _qty(_get(client, period="week")) == {"2026-10-26": 1, "2026-11-02": 2}


def test_the_month_changes_on_the_first_at_korean_midnight(client: TestClient, db: Db) -> None:
    before_month = datetime(2026, 10, 31, 14, 59, 59, tzinfo=UTC)  # 한국 10/31 23:59:59
    on_month = datetime(2026, 10, 31, 15, 0, 0, tzinfo=UTC)  # 한국 11/1 00:00:00
    _order(client, db, before_month, 4)
    _order(client, db, on_month, 8)
    assert _qty(_get(client, period="month")) == {"2026-10-01": 4, "2026-11-01": 8}
    assert _qty(_get(client, period="day")) == {"2026-10-31": 4, "2026-11-01": 8}


def test_period_start_is_the_korean_date_even_when_the_utc_date_differs(
    client: TestClient, db: Db
) -> None:
    late_night_kst = datetime(2026, 11, 3, 16, 30, tzinfo=UTC)  # UTC 11/3, 한국 11/4 01:30
    _order(client, db, late_night_kst, 3)
    assert _qty(_get(client, period="day")) == {"2026-11-04": 3}  # UTC 날짜(11/3)가 아니다
