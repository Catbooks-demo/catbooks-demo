"""데이터 권한 전용 고객 내보내기: 가명 ID와 연령대·성별·직업군뿐이고, 고친 구매자는 다시 나온다."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

DATA = {"Authorization": "Bearer mock:data"}
SUPPORT = {"Authorization": "Bearer mock:support"}
KEYS = {"buyer_id", "age_band", "gender", "occupation", "created_at", "updated_at"}
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
    res = client.get("/api/shop/customers/export", headers=DATA)
    assert res.status_code == 200
    rows = res.json()["data"]
    assert rows and all(set(row) == KEYS for row in rows)
    assert EMAIL not in res.text and "고양이" not in res.text


def test_export_pages_by_cursor_and_returns_changes(client: TestClient, db: Db) -> None:
    token = _signup(client)
    # 같은 초 안의 변경이 커서에 가려지지 않도록 모든 구매자를 옛 시각으로 둔다
    with db.tx() as conn:
        conn.execute("UPDATE buyers SET created_at = '2026-01-01T00:00:00Z', updated_at = NULL")

    rows: list[dict] = []
    cursor: str | None = None
    last_cursor = ""
    while True:
        params: dict[str, object] = {"limit": 10}
        if cursor:
            params["cursor"] = cursor
        body = client.get("/api/shop/customers/export", params=params, headers=DATA).json()
        rows += body["data"]
        cursor = body["meta"]["next_cursor"]
        if cursor is None:
            break
        last_cursor = cursor
    assert len(rows) == 31 and len({r["buyer_id"] for r in rows}) == 31

    # 끝까지 읽은 뒤 마지막 커서로 다시 읽으면 마지막 페이지의 줄만 남는다. 변경 뒤엔 새로 나온다
    patch = client.patch(
        "/api/shop/me",
        json={"occupation": "employee"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert patch.status_code == 200
    again = client.get(
        "/api/shop/customers/export", params={"cursor": last_cursor}, headers=DATA
    ).json()
    changed = [r for r in again["data"] if r["occupation"] == "employee"]
    assert len(changed) == 1 and changed[0]["buyer_id"] == "buy_0031"
    assert changed[0]["updated_at"] is not None


def test_export_rejects_a_bad_cursor_and_limit(client: TestClient) -> None:
    assert (
        client.get("/api/shop/customers/export", params={"cursor": "!!"}, headers=DATA).status_code
        == 400
    )
    assert (
        client.get("/api/shop/customers/export", params={"limit": 0}, headers=DATA).status_code
        == 422
    )


def test_export_is_data_or_admin_only(client: TestClient) -> None:
    assert client.get("/api/shop/customers/export").status_code == 401
    assert client.get("/api/shop/customers/export", headers=SUPPORT).status_code == 403
    assert client.get("/api/shop/customers/export", headers=DATA).status_code == 200
