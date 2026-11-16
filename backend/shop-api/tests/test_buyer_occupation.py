"""구매자 직업군 코드(`occupation`): 큰 구분만 선택으로 받는다. 세부 직업은 받지 않는다."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

CODES = ["student", "employee", "self_employed", "other", "no_answer"]


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _user(client: TestClient, buyer: str = "buy_0001") -> dict:
    return client.get(f"/api/shop/users/{buyer}").json()["data"]


def test_a_buyer_has_no_occupation_until_it_is_set(client: TestClient) -> None:
    assert _user(client)["occupation"] is None  # 모르는 것이다. 채워 넣지 않는다
    session = client.post("/api/shop/sessions", json={"buyer_id": "buy_0001"}).json()
    assert session["data"]["user"]["occupation"] is None
    first = client.get("/api/shop/users").json()["data"][0]
    assert first == {"buyer_id": "buy_0001", "display_name": "데모 구매자 0001"}


@pytest.mark.parametrize("code", CODES)
def test_set_and_read_back_each_code(client: TestClient, code: str) -> None:
    done = client.patch("/api/shop/users/buy_0002", json={"occupation": code})
    assert done.status_code == 200 and done.json()["data"]["occupation"] == code
    assert _user(client, "buy_0002")["occupation"] == code


def test_clear_reject_and_keep_other_fields(client: TestClient) -> None:
    client.patch("/api/shop/users/buy_0003", json={"age_band": "20s", "occupation": "student"})
    client.patch("/api/shop/users/buy_0003", json={"gender": "female"})  # 직업은 그대로
    assert _user(client, "buy_0003")["occupation"] == "student"
    cleared = client.patch("/api/shop/users/buy_0003", json={"occupation": None})
    assert cleared.status_code == 200 and cleared.json()["data"]["occupation"] is None
    assert _user(client, "buy_0003")["age_band"] == "20s"
    for bad in ({"occupation": "developer"}, {"occupation": 3}, {"job_title": "x"}, {}):
        assert client.patch("/api/shop/users/buy_0003", json=bad).status_code == 422


def test_the_occupation_survives_an_old_database_without_the_column(tmp_path) -> None:
    import sqlite3

    path = str(tmp_path / "old.db")
    with Db(path).tx():
        pass
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE buyers DROP COLUMN occupation")
    conn.commit()
    conn.close()
    assert _user(TestClient(create_app(Db(path))))["occupation"] is None
