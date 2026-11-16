"""구매자 성별 코드(`gender`): 저장·조회·지움, 연령대와 따로 바뀜, 판매 집계 기준.

코드값(`female`·`male`·`no_answer`)만 받는다. 값이 없으면 None이고 채워 넣지 않는다.
직업·생년월일은 받지 않는다.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

SUPPORT = {"Authorization": "Bearer mock:support"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setattr("ax_shop_api.lookups.MIN_GROUP_BUYERS", 1)  # 숨기기는 별도 파일에서 본다
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _user(client: TestClient, buyer: str = "buy_0001") -> dict:
    return client.get(f"/api/shop/users/{buyer}").json()["data"]


def test_a_buyer_has_no_gender_until_it_is_set(client: TestClient) -> None:
    assert _user(client)["gender"] is None  # 옛 구매자·입력 전 구매자는 모르는 것이다
    session = client.post("/api/shop/sessions", json={"buyer_id": "buy_0001"}).json()
    assert session["data"]["user"]["gender"] is None


def test_the_user_list_does_not_expose_gender(client: TestClient) -> None:
    client.patch("/api/shop/users/buy_0001", json={"gender": "female"})
    first = client.get("/api/shop/users").json()["data"][0]
    assert first == {"buyer_id": "buy_0001", "display_name": "데모 구매자 0001"}


@pytest.mark.parametrize("code", ["female", "male", "no_answer"])
def test_set_and_read_back_each_code(client: TestClient, code: str) -> None:
    done = client.patch("/api/shop/users/buy_0002", json={"gender": code})
    assert done.status_code == 200 and done.json()["data"]["gender"] == code
    assert _user(client, "buy_0002")["gender"] == code


def test_clear_with_null_and_reject_anything_else(client: TestClient) -> None:
    client.patch("/api/shop/users/buy_0003", json={"gender": "male"})
    cleared = client.patch("/api/shop/users/buy_0003", json={"gender": None})
    assert cleared.status_code == 200 and cleared.json()["data"]["gender"] is None
    # 직업군(`occupation`)은 회원 프로필(`PATCH /me`)이 받는다. 이 경로는 거절한다(의도)
    for bad in (
        {"gender": "other"},
        {"gender": 1},
        {"occupation": "student"},
        {"job_title": "student"},
        {},
    ):
        assert client.patch("/api/shop/users/buy_0003", json=bad).status_code == 422
    assert _user(client, "buy_0003")["gender"] is None


def test_only_the_sent_fields_change(client: TestClient) -> None:
    client.patch("/api/shop/users/buy_0001", json={"age_band": "20s", "gender": "female"})
    client.patch("/api/shop/users/buy_0001", json={"gender": "male"})  # 연령대는 그대로
    assert (_user(client)["age_band"], _user(client)["gender"]) == ("20s", "male")
    client.patch("/api/shop/users/buy_0001", json={"age_band": None})  # 성별은 그대로
    assert (_user(client)["age_band"], _user(client)["gender"]) == (None, "male")


def test_unknown_buyer_is_404(client: TestClient) -> None:
    missing = client.patch("/api/shop/users/buy_9999", json={"gender": "female"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "BUYER_NOT_FOUND"


def _paid_order(client: TestClient, buyer: str, qty: int) -> None:
    done = client.post(
        "/api/shop/orders",
        json={"buyer_id": buyer, "items": [{"product_id": "prd_0001", "qty": qty}]},
    )
    assert done.status_code in (200, 201), done.text
    order_id = done.json()["data"]["order_id"]
    paid = client.post(f"/api/shop/orders/{order_id}/payments", json={"simulate": "success"})
    assert paid.status_code == 200, paid.text


def _window() -> str:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    now = datetime.now(UTC)
    since = (now - timedelta(days=1)).strftime(fmt)
    until = (now + timedelta(days=1)).strftime(fmt)
    return f"from={since}&to={until}"


def test_sales_summary_can_group_by_gender_and_age_band(client: TestClient) -> None:
    client.patch("/api/shop/users/buy_0001", json={"age_band": "20s", "gender": "female"})
    client.patch("/api/shop/users/buy_0002", json={"age_band": "20s", "gender": "male"})
    _paid_order(client, "buy_0001", 2)
    _paid_order(client, "buy_0002", 1)
    _paid_order(client, "buy_0003", 4)  # 성별·연령대를 모르는 구매자
    summary = client.get(
        f"/api/shop/sales-summary?{_window()}&group_by=age_band,gender", headers=SUPPORT
    )
    assert summary.status_code == 200, summary.text
    rows = {(r["age_band"], r["gender"]): r["gross_qty"] for r in summary.json()["data"]["rows"]}
    assert rows == {("20s", "female"): 2, ("20s", "male"): 1, (None, None): 4}
    only = client.get(f"/api/shop/sales-summary?{_window()}&group_by=gender", headers=SUPPORT)
    by_gender = {r["gender"]: r["gross_qty"] for r in only.json()["data"]["rows"]}
    assert by_gender == {"female": 2, "male": 1, None: 4}


def test_the_gender_survives_an_old_database_without_the_column(tmp_path) -> None:
    """이미 만들어진 DB 파일에는 `gender` 열이 없다 — 켤 때 더해지고 기존 구매자는 None이다."""
    import sqlite3

    path = str(tmp_path / "old.db")
    first = Db(path)
    with first.tx():
        pass
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE buyers DROP COLUMN gender")
    conn.commit()
    conn.close()
    client = TestClient(create_app(Db(path)))
    assert _user(client)["gender"] is None
