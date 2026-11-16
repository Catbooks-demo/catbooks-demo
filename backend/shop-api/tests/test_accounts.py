"""회원 가입·로그인·로그인 토큰(세션): 이메일 유일, ID 발급, 해시 저장, 보유기간 제외."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api import retention
from ax_shop_api.accounts import Accounts
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

SIGNUP = {
    "email": "neko@example.com",
    "password": "correct-horse-1",
    "display_name": "고양이",
    "agree_terms": True,
    "agree_privacy": True,
}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.delenv("SHOP_DEMO_LOGIN", raising=False)
    events.reset_mock_bus()


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def _signup(client: TestClient, email: str, **extra: object) -> dict:
    res = client.post("/api/shop/auth/signup", json=SIGNUP | {"email": email} | extra)
    assert res.status_code == 201, res.text
    return res.json()["data"]["user"]


def test_signup_returns_a_token_and_a_new_buyer_after_the_seed_buyers(client: TestClient) -> None:
    res = client.post("/api/shop/auth/signup", json=SIGNUP)
    assert res.status_code == 201
    data = res.json()["data"]
    assert data["token"] and data["user"]["buyer_id"] == "buy_0031"
    assert data["user"]["email"] == "neko@example.com" and data["user"]["job_group"] is None


def test_email_is_unique_regardless_of_case_and_spaces(client: TestClient) -> None:
    client.post("/api/shop/auth/signup", json=SIGNUP)
    again = client.post("/api/shop/auth/signup", json=SIGNUP | {"email": "  NEKO@Example.com "})
    assert again.status_code == 409 and again.json()["error"]["code"] == "EMAIL_TAKEN"


def test_new_buyer_ids_skip_existing_numbers(client: TestClient, db: Db) -> None:
    with db.tx() as c:
        c.execute(
            "INSERT INTO buyers (buyer_id, tenant_id, created_at) "
            "VALUES ('buy_0100','shop-demo','2026-11-01T00:00:00Z')"
        )
    assert _signup(client, "a@example.com")["buyer_id"] == "buy_0101"


def test_login_wrong_password_and_unknown_email_look_the_same(client: TestClient) -> None:
    client.post("/api/shop/auth/signup", json=SIGNUP)
    wrong = client.post(
        "/api/shop/auth/login", json={"email": SIGNUP["email"], "password": "wrong-password"}
    )
    unknown = client.post(
        "/api/shop/auth/login", json={"email": "nobody@example.com", "password": "correct-horse-1"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["error"]["code"] == "INVALID_LOGIN"
    ok = client.post(
        "/api/shop/auth/login", json={"email": " NEKO@example.com", "password": SIGNUP["password"]}
    )
    assert ok.status_code == 200 and ok.json()["data"]["user"]["buyer_id"] == "buy_0031"


def test_password_is_stored_hashed_and_token_is_stored_hashed(client: TestClient, db: Db) -> None:
    data = client.post("/api/shop/auth/signup", json=SIGNUP).json()["data"]
    with db.tx() as c:
        account = dict(c.execute("SELECT * FROM accounts").fetchone())
        sessions = [dict(r) for r in c.execute("SELECT * FROM sessions")]
    assert SIGNUP["password"] not in account.values()
    assert account["password_hash"].startswith("scrypt$16384$8$1$")
    assert len(sessions) == 1 and data["token"] not in sessions[0].values()
    assert len(sessions[0]["token_hash"]) == 64


def test_logout_revokes_the_token(client: TestClient, db: Db) -> None:
    token = client.post("/api/shop/auth/signup", json=SIGNUP).json()["data"]["token"]
    accounts = Accounts(db)
    assert accounts.resolve(token) == "buy_0031"
    res = client.post("/api/shop/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert accounts.resolve(token) is None
    assert accounts.resolve("") is None and accounts.resolve("not-a-token") is None


def test_expired_session_does_not_resolve(client: TestClient, db: Db) -> None:
    token = client.post("/api/shop/auth/signup", json=SIGNUP).json()["data"]["token"]
    with db.tx() as c:
        c.execute("UPDATE sessions SET expires_at = '2020-01-01T00:00:00Z'")
    assert Accounts(db).resolve(token) is None


@pytest.mark.parametrize("password", ["short1", "x" * 129])
def test_password_rules(client: TestClient, password: str) -> None:
    res = client.post("/api/shop/auth/signup", json=SIGNUP | {"password": password})
    assert res.status_code == 422 and res.json()["error"]["code"] == "WEAK_PASSWORD"
    assert (
        client.post("/api/shop/auth/signup", json=SIGNUP | {"password": "x" * 128}).status_code
        == 201
    )


def test_terms_must_be_agreed(client: TestClient) -> None:
    res = client.post("/api/shop/auth/signup", json=SIGNUP | {"agree_terms": False})
    assert res.status_code == 422
    res = client.post(
        "/api/shop/auth/signup", json={k: v for k, v in SIGNUP.items() if k != "agree_privacy"}
    )
    assert res.status_code == 422


def test_optional_attributes_accept_only_codes(client: TestClient) -> None:
    bad = client.post("/api/shop/auth/signup", json=SIGNUP | {"job_group": "학생"})
    assert bad.status_code == 422
    old = client.post("/api/shop/auth/signup", json=SIGNUP | {"job_group": "office"})
    assert old.status_code == 422
    good = client.post(
        "/api/shop/auth/signup",
        json=SIGNUP | {"job_group": "student", "age_band": "20s", "gender": "female"},
    )
    user = good.json()["data"]["user"]
    assert (
        user["job_group"] == "student" and user["age_band"] == "20s" and user["gender"] == "female"
    )


def test_demo_login_is_off_by_default(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/api/shop/auth/config").json()["data"] == {"demo_login": False}
    off = client.post("/api/shop/auth/demo-login", json={"buyer_id": "buy_0001"})
    assert off.status_code == 404
    monkeypatch.setenv("SHOP_DEMO_LOGIN", "on")
    assert client.get("/api/shop/auth/config").json()["data"] == {"demo_login": True}
    on = client.post("/api/shop/auth/demo-login", json={"buyer_id": "buy_0001"})
    assert on.status_code == 200
    user = on.json()["data"]["user"]
    assert user["email"] is None and user["display_name"] == "데모 구매자 0001"
    _signup(client, "a@example.com")
    taken = client.post("/api/shop/auth/demo-login", json={"buyer_id": "buy_0031"})
    assert taken.status_code == 401


def test_retention_keeps_buyers_with_an_account(client: TestClient, db: Db) -> None:
    user = _signup(client, "old@example.com")
    with db.tx() as c:
        c.execute(
            "INSERT INTO buyers (buyer_id, tenant_id, created_at) "
            "VALUES ('buy_0200','shop-demo','2020-01-01T00:00:00Z')"
        )
        c.execute(
            "UPDATE buyers SET created_at = '2020-01-01T00:00:00Z' WHERE buyer_id = ?",
            (user["buyer_id"],),
        )
        c.execute(
            "INSERT INTO sessions VALUES "
            "('h', 'buy_0200', '2020-01-01T00:00:00Z', '2099-01-01T00:00:00Z')"
        )
    retention.purge_expired(db, datetime.now(UTC), apply=True)
    with db.tx() as c:
        left = {r["buyer_id"] for r in c.execute("SELECT buyer_id FROM buyers")}
        sessions = c.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE buyer_id = 'buy_0200'"
        ).fetchone()
    assert user["buyer_id"] in left  # 계정이 있는 오래된 회원은 남는다
    assert "buy_0200" not in left  # 계정 없는 오래된 구매자는 지워진다
    assert sessions["n"] == 0  # 지워진 구매자의 토큰도 같이 지워진다
