"""구매자 연령대 구간(`age_band`): 저장·조회·지움, 문의·클레임·아이디어를 읽어 가는 쪽에 실린다.

생년월일은 받지 않고 구간만 저장한다. 값이 없으면 None이고 채워 넣지 않는다.
"""

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
    monkeypatch.setenv("SHOP_INQUIRY_RATE_PER_BUYER", "50")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _order(client: TestClient, buyer: str) -> str:
    done = client.post(
        "/api/shop/orders",
        json={"buyer_id": buyer, "items": [{"product_id": "prd_0001", "qty": 1}]},
    )
    return done.json()["data"]["order_id"]


def test_a_buyer_has_no_age_band_until_it_is_set(client: TestClient) -> None:
    user = client.get("/api/shop/users/buy_0001").json()["data"]
    assert user["age_band"] is None  # 옛 구매자·입력 전 구매자는 모르는 것이다. 채워 넣지 않는다
    session = client.post("/api/shop/sessions", json={"buyer_id": "buy_0001"}).json()
    assert session["data"]["user"]["age_band"] is None


def test_the_user_list_does_not_expose_age_bands(client: TestClient) -> None:
    client.patch("/api/shop/users/buy_0001", json={"age_band": "20s"})
    first = client.get("/api/shop/users").json()["data"][0]
    assert first == {"buyer_id": "buy_0001", "display_name": "데모 구매자 0001"}


@pytest.mark.parametrize("band", ["10s", "20s", "30s", "40s", "50s_plus"])
def test_set_and_read_back_each_band(client: TestClient, band: str) -> None:
    done = client.patch("/api/shop/users/buy_0002", json={"age_band": band})
    assert done.status_code == 200 and done.json()["data"]["age_band"] == band
    assert client.get("/api/shop/users/buy_0002").json()["data"]["age_band"] == band


def test_clear_with_null_and_reject_anything_else(client: TestClient) -> None:
    client.patch("/api/shop/users/buy_0003", json={"age_band": "30s"})
    cleared = client.patch("/api/shop/users/buy_0003", json={"age_band": None})
    assert cleared.status_code == 200 and cleared.json()["data"]["age_band"] is None
    for bad in ({"age_band": "teens"}, {"age_band": 25}, {"birth_year": 1999}, {}):
        assert client.patch("/api/shop/users/buy_0003", json=bad).status_code == 422
    assert client.get("/api/shop/users/buy_0003").json()["data"]["age_band"] is None


def test_unknown_buyer_is_404(client: TestClient) -> None:
    missing = client.patch("/api/shop/users/buy_9999", json={"age_band": "20s"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "BUYER_NOT_FOUND"


def test_inquiries_claims_and_ideas_carry_the_buyers_age_band(client: TestClient) -> None:
    client.patch("/api/shop/users/buy_0001", json={"age_band": "20s"})  # buy_0002는 비워 둔다
    for buyer in ("buy_0001", "buy_0002"):
        order = _order(client, buyer)
        sent = client.post(
            "/api/shop/inquiries", json={"buyer_id": buyer, "order_id": order, "body": "문의"}
        )
        assert sent.status_code == 201
        claim = client.post(
            "/api/shop/claims",
            json={
                "buyer_id": buyer,
                "customer_name": "구매자",
                "order_id": order,
                "type": "return",
                "reason": "change_of_mind",
            },
        )
        assert claim.status_code == 201
        idea = client.post(
            "/api/shop/ideas", json={"buyer_id": buyer, "kind": "event", "text": "생일 쿠폰"}
        )
        assert idea.status_code == 201, idea.text
    for path in ("inquiries", "claims", "ideas"):
        rows = client.get(f"/api/shop/admin/{path}", headers=SUPPORT).json()["data"]
        bands = {r["buyer_id"]: r["age_band"] for r in rows}
        assert bands == {"buy_0001": "20s", "buy_0002": None}, path


def test_the_age_band_survives_an_old_database_without_the_column(tmp_path) -> None:
    """이미 만들어진 DB 파일에는 `age_band` 열이 없다 — 켤 때 더해지고 기존 구매자는 None이다."""
    import sqlite3

    path = str(tmp_path / "old.db")
    first = Db(path)
    first.tx().__enter__()  # 열을 만든다 (초기화)
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE buyers DROP COLUMN age_band")
    conn.commit()
    conn.close()
    client = TestClient(create_app(Db(path)))
    assert client.get("/api/shop/users/buy_0001").json()["data"]["age_band"] is None
