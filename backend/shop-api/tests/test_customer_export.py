"""데이터 권한 전용 고객 내보내기(계약): 가명 ID와 값 있는 속성뿐이고, 고친 구매자는 다시 나온다."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.data_access.shop_customers import ShopCustomersExportResult
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

DATA = {"Authorization": "Bearer mock:data"}
SUPPORT = {"Authorization": "Bearer mock:support"}
KEYS = {"buyer_id", "joined_at", "updated_at", "attributes", "tenant_id"}
EMAIL = "neko@example.com"
PASSWORD = "correct-horse-1"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("SHOP_BUYER_AUTH", "required")
    monkeypatch.delenv("SHOP_DEMO_LOGIN", raising=False)
    events.reset_mock_bus()


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def _signup(client: TestClient) -> str:
    res = client.post(
        "/api/shop/auth/signup",
        json={
            "email": EMAIL,
            "password": PASSWORD,
            "display_name": "고양이",
            "agree_terms": True,
            "agree_privacy": True,
            "age_band": "20s",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["data"]["token"]


def test_export_has_no_names_or_contacts(client: TestClient) -> None:
    _signup(client)
    res = client.get("/api/shop/customers-export", headers=DATA)
    assert res.status_code == 200
    body = res.json()["data"]
    rows = body["customers"]
    assert rows and all(set(row) == KEYS for row in rows)
    assert body["collected_at"] and "has_more" in body
    assert EMAIL not in res.text and "고양이" not in res.text
    ShopCustomersExportResult.model_validate(body)


def test_attributes_only_carry_filled_keys(client: TestClient) -> None:
    _signup(client)  # age_band만 골랐다
    rows = client.get("/api/shop/customers-export", params={"limit": 1000}, headers=DATA).json()[
        "data"
    ]["customers"]
    new = next(r for r in rows if r["buyer_id"] == "buy_0031")
    assert new["attributes"] == {"age_band": "20s"}
    assert all(set(r["attributes"]) <= {"age_band", "gender", "occupation"} for r in rows)
    assert all(v for r in rows for v in r["attributes"].values())


def _poll(client: TestClient, cursor: str | None, limit: int) -> tuple[list[dict], str | None]:
    """has_more인 동안 이어 읽고, (읽은 줄, 다음 읽기에 쓸 커서)를 돌려준다."""
    rows: list[dict] = []
    while True:
        params: dict[str, object] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        meta_body = client.get("/api/shop/customers-export", params=params, headers=DATA).json()
        rows += meta_body["data"]["customers"]
        cursor = meta_body["data"]["next_cursor"]
        if not meta_body["data"]["has_more"]:
            return rows, cursor


def test_export_pages_by_cursor_and_returns_changes(client: TestClient) -> None:
    token = _signup(client)
    rows, saved = _poll(client, None, 10)
    assert len(rows) == 31 and len({r["buyer_id"] for r in rows}) == 31
    assert saved

    # 끝난 뒤 저장한 커서로 다시 읽으면 0줄이고 같은 커서가 돌아온다
    idle = client.get("/api/shop/customers-export", params={"cursor": saved}, headers=DATA).json()
    assert idle["data"]["customers"] == []
    assert idle["data"]["next_cursor"] == saved and idle["data"]["has_more"] is False

    patch = client.patch(
        "/api/shop/me",
        json={"occupation": "employee"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch.status_code == 200
    changed, saved2 = _poll(client, saved, 10)
    assert [r["buyer_id"] for r in changed] == ["buy_0031"]
    assert changed[0]["attributes"]["occupation"] == "employee" and saved2 != saved


def test_earlier_buyer_changed_after_the_reader_passed_it_reappears(client: TestClient) -> None:
    """읽은 곳보다 buyer_id가 앞선 구매자가 같은 초에 바뀌어도 다음 읽기에 나온다."""
    _signup(client)  # buy_0031
    rows, saved = _poll(client, None, 30)
    assert len(rows) == 31

    res = client.patch(
        "/api/shop/users/buy_0005",
        json={"gender": "female"},
        headers={"Authorization": "Bearer mock:admin"},
    )
    assert res.status_code == 200, res.text
    after, _ = _poll(client, saved, 30)
    assert [r["buyer_id"] for r in after] == ["buy_0005"]


def test_export_rejects_a_bad_cursor_and_limit(client: TestClient) -> None:
    assert (
        client.get("/api/shop/customers-export", params={"cursor": "!!"}, headers=DATA).status_code
        == 400
    )
    assert (
        client.get("/api/shop/customers-export", params={"limit": 0}, headers=DATA).status_code
        == 422
    )
    assert (
        client.get("/api/shop/customers-export", params={"limit": 1001}, headers=DATA).status_code
        == 422
    )
    assert (
        client.get("/api/shop/customers-export", params={"limit": 1000}, headers=DATA).status_code
        == 200
    )


def test_old_path_is_gone(client: TestClient) -> None:
    assert client.get("/api/shop/customers/export", headers=DATA).status_code == 404


def test_export_is_data_or_admin_only(client: TestClient) -> None:
    assert client.get("/api/shop/customers-export").status_code == 401
    assert client.get("/api/shop/customers-export", headers=SUPPORT).status_code == 403
    assert client.get("/api/shop/customers-export", headers=DATA).status_code == 200
