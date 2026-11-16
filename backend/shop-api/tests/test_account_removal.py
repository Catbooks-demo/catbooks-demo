"""계정 삭제·탈퇴와 가입·로그인 시도 제한, 시험용 구매자 목록.

- 삭제 요청 집행(`purge_buyer`)은 구매자 행이 남더라도 계정·후기·토큰을 지우고 속성을 비운다.
- 회원 탈퇴(`POST /api/shop/me/withdraw`)는 비밀번호를 확인한 뒤 같은 삭제를 한다(주문은 남는다).
- 로그인은 이메일·IP별, 가입은 IP별로 시도 수를 센다(429 `RATE_LIMITED`).
- `GET /api/shop/auth/demo-buyers`는 `SHOP_DEMO_LOGIN=on`일 때만, 계정 없는 구매자만 준다.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api import accounts, retention
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.main import create_app

PASSWORD = "correct-horse-1"
DATA = {"Authorization": "Bearer mock:data"}
ITEMS = [{"product_id": "prd_0001", "qty": 1}]


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


def _signup(client: TestClient, email: str, **extra: object) -> tuple[dict[str, str], str]:
    res = client.post(
        "/api/shop/auth/signup",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "고양이",
            "agree_terms": True,
            "agree_privacy": True,
            **extra,
        },
    )
    assert res.status_code == 201, res.text
    data = res.json()["data"]
    return {"Authorization": f"Bearer {data['token']}"}, data["user"]["buyer_id"]


def _count(db: Db, table: str, buyer_id: str) -> int:
    with db.tx() as c:
        return c.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE buyer_id = ?",  # noqa: S608
            (buyer_id,),
        ).fetchone()["n"]


def _buyer_row(db: Db, buyer_id: str):  # noqa: ANN202
    with db.tx() as c:
        return c.execute(
            "SELECT age_band, gender, occupation, change_seq FROM buyers WHERE buyer_id = ?",
            (buyer_id,),
        ).fetchone()


def _review(db: Db, buyer_id: str, order_id: str = "ord_x") -> None:
    with db.tx() as c:
        c.execute(
            "INSERT INTO reviews VALUES (?, ?, 'prd_0001', ?, 5, '열 글자가 넘는 후기예요', "
            "'2026-11-01T00:00:00Z')",
            (f"rev_{buyer_id}", order_id, buyer_id),
        )


def _order(db: Db, order_id: str, buyer_id: str) -> None:
    with db.tx() as c:
        c.execute(
            "INSERT INTO orders(order_id, buyer_id, status, total_amount, tenant_id, "
            "created_at, updated_at) VALUES (?, ?, 'paid', 1000, ?, "
            "'2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z')",
            (order_id, buyer_id, TENANT_ID),
        )


ATTRS = {"age_band": "20s", "gender": "female", "occupation": "student"}


# --- 삭제 요청 집행(purge_buyer) ----------------------------------------------


def test_purge_buyer_with_orders_keeps_the_row_but_removes_the_account(
    client: TestClient, db: Db
) -> None:
    headers, buyer_id = _signup(client, "a@example.com", **ATTRS)
    _order(db, "ord_a1", buyer_id)
    _review(db, buyer_id, "ord_a1")
    before_seq = _buyer_row(db, buyer_id)["change_seq"]

    dry = retention.purge_buyer(db, buyer_id)
    assert dry.applied is False
    assert dry.counts["accounts"] == 1 and dry.counts["sessions"] == 1
    assert dry.counts["reviews"] == 1 and dry.counts["buyers"] == 0
    assert _count(db, "accounts", buyer_id) == 1  # dry-run은 바꾸지 않는다
    assert _buyer_row(db, buyer_id)["gender"] == "female"

    report = retention.purge_buyer(db, buyer_id, apply=True)
    assert report.counts == dry.counts
    assert "accounts=1" in report.summary() and buyer_id not in report.summary()
    row = _buyer_row(db, buyer_id)
    assert row is not None  # 법정 보존 주문이 있어 구매자 행은 남는다
    assert (row["age_band"], row["gender"], row["occupation"]) == (None, None, None)
    assert row["change_seq"] > before_seq
    for table in ("accounts", "sessions", "reviews"):
        assert _count(db, table, buyer_id) == 0
    assert _count(db, "orders", buyer_id) == 1
    assert client.get("/api/shop/me", headers=headers).status_code == 401


def test_purge_buyer_without_orders_removes_everything(client: TestClient, db: Db) -> None:
    _headers, buyer_id = _signup(client, "b@example.com", **ATTRS)
    _review(db, buyer_id)
    report = retention.purge_buyer(db, buyer_id, apply=True)
    assert report.counts["buyers"] == 1 and report.counts["accounts"] == 1
    assert _buyer_row(db, buyer_id) is None
    for table in ("accounts", "sessions", "reviews"):
        assert _count(db, table, buyer_id) == 0


def test_purge_buyer_change_shows_in_customers_export(client: TestClient, db: Db) -> None:
    _headers, buyer_id = _signup(client, "c@example.com", **ATTRS)
    _order(db, "ord_c1", buyer_id)
    first = client.get("/api/shop/customers-export", params={"limit": 100}, headers=DATA).json()
    cursor = first["data"]["next_cursor"]
    retention.purge_buyer(db, buyer_id, apply=True)
    after = client.get(
        "/api/shop/customers-export", params={"cursor": cursor}, headers=DATA
    ).json()["data"]["customers"]
    assert [r["buyer_id"] for r in after] == [buyer_id]
    assert not any(after[0]["attributes"].values())


# --- 만료 토큰 정리(purge_expired) ----------------------------------------------


def test_purge_expired_removes_expired_sessions_only_on_apply(db: Db) -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    past = (now - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    later = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.tx() as c:
        c.execute("INSERT INTO sessions VALUES ('old', 'buy_0001', ?, ?)", (past, past))
        c.execute("INSERT INTO sessions VALUES ('new', 'buy_0001', ?, ?)", (past, later))
    report = retention.purge_expired(db, now)
    assert "sessions" not in report.counts and _count(db, "sessions", "buy_0001") == 2
    retention.purge_expired(db, now, apply=True)
    with db.tx() as c:
        left = [r[0] for r in c.execute("SELECT token_hash FROM sessions")]
    assert left == ["new"]


# --- 회원 탈퇴 -----------------------------------------------------------------


def test_withdraw_removes_account_but_keeps_orders(client: TestClient, db: Db) -> None:
    headers, buyer_id = _signup(client, "w@example.com", **ATTRS)
    order = client.post("/api/shop/orders", json={"items": ITEMS}, headers=headers)
    assert order.status_code == 201, order.text
    _review(db, buyer_id)

    wrong = client.post("/api/shop/me/withdraw", json={"password": "nope-nope"}, headers=headers)
    assert wrong.status_code == 403 and wrong.json()["error"]["code"] == "INVALID_PASSWORD"
    assert _count(db, "accounts", buyer_id) == 1

    ok = client.post("/api/shop/me/withdraw", json={"password": PASSWORD}, headers=headers)
    assert ok.status_code == 200 and ok.json()["data"] == {"withdrawn": True}
    for table in ("accounts", "sessions", "reviews"):
        assert _count(db, table, buyer_id) == 0
    row = _buyer_row(db, buyer_id)
    assert (row["age_band"], row["gender"], row["occupation"]) == (None, None, None)
    assert _count(db, "orders", buyer_id) == 1
    assert client.get("/api/shop/me", headers=headers).status_code == 401
    creds = {"email": "w@example.com", "password": PASSWORD}
    login = client.post("/api/shop/auth/login", json=creds)
    assert login.status_code == 401
    # 같은 이메일로 다시 가입할 수 있다(새 구매자 ID)
    _h, again = _signup(client, "w@example.com")
    assert again != buyer_id


def test_withdraw_needs_a_buyer_login_and_a_password_account(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.post("/api/shop/me/withdraw", json={"password": PASSWORD}).status_code == 401
    admin = {"Authorization": "Bearer mock:admin"}
    res = client.post("/api/shop/me/withdraw", json={"password": PASSWORD}, headers=admin)
    assert res.status_code == 403
    monkeypatch.setenv("SHOP_DEMO_LOGIN", "on")
    demo = client.post("/api/shop/auth/demo-login", json={"buyer_id": "buy_0002"}).json()
    headers = {"Authorization": f"Bearer {demo['data']['token']}"}
    res = client.post("/api/shop/me/withdraw", json={"password": PASSWORD}, headers=headers)
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_PASSWORD"


def test_withdraw_is_rate_limited(client: TestClient) -> None:
    headers, _ = _signup(client, "r@example.com")
    limit, _window = accounts.PASSWORD_CHECK_PER_BUYER
    for _ in range(limit):
        res = client.post("/api/shop/me/withdraw", json={"password": "wrong-pw"}, headers=headers)
        assert res.status_code == 403
    res = client.post("/api/shop/me/withdraw", json={"password": PASSWORD}, headers=headers)
    assert res.status_code == 429 and res.json()["error"]["code"] == "RATE_LIMITED"


# --- 가입·로그인 시도 제한 -------------------------------------------------------


def test_login_is_limited_per_email(client: TestClient) -> None:
    _signup(client, "l@example.com")
    limit, _window = accounts.LOGIN_PER_EMAIL
    for _ in range(limit):
        res = client.post(
            "/api/shop/auth/login", json={"email": "l@example.com", "password": "wrong-pw"}
        )
        assert res.status_code == 401
    blocked = client.post(
        "/api/shop/auth/login", json={"email": " L@Example.com ", "password": PASSWORD}
    )
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "RATE_LIMITED"
    # 다른 이메일은 그대로 된다
    _signup(client, "m@example.com")
    ok = client.post("/api/shop/auth/login", json={"email": "m@example.com", "password": PASSWORD})
    assert ok.status_code == 200


def test_login_is_limited_per_ip(db: Db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accounts, "LOGIN_PER_IP", (3, 300.0))
    client = TestClient(create_app(db))
    for n in range(3):
        res = client.post(
            "/api/shop/auth/login", json={"email": f"x{n}@example.com", "password": "wrong-pw"}
        )
        assert res.status_code == 401
    res = client.post("/api/shop/auth/login", json={"email": "y@example.com", "password": "pw"})
    assert res.status_code == 429


def test_signup_is_limited_per_ip(client: TestClient) -> None:
    limit, _window = accounts.SIGNUP_PER_IP
    for n in range(limit):
        _signup(client, f"s{n}@example.com")
    res = client.post(
        "/api/shop/auth/signup",
        json={
            "email": "last@example.com",
            "password": PASSWORD,
            "display_name": "고양이",
            "agree_terms": True,
            "agree_privacy": True,
        },
    )
    assert res.status_code == 429 and res.json()["error"]["code"] == "RATE_LIMITED"


def test_limiter_window_expires() -> None:
    now = [0.0]
    limiter = accounts.KeyedRateLimiter(2, 10.0, clock=lambda: now[0])
    limiter.hit("k")
    limiter.hit("k")
    with pytest.raises(accounts.ShopError):
        limiter.hit("k")
    limiter.hit("other")
    now[0] = 10.0
    limiter.hit("k")


# --- 시험용 구매자 목록 ----------------------------------------------------------


def test_demo_buyers_only_when_enabled_and_without_accounts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.get("/api/shop/auth/demo-buyers").status_code == 404
    _h, member = _signup(client, "d@example.com", age_band="30s")
    monkeypatch.setenv("SHOP_DEMO_LOGIN", "on")
    res = client.get("/api/shop/auth/demo-buyers")  # 토큰 없이 연다
    assert res.status_code == 200
    rows = res.json()["data"]
    ids = [r["buyer_id"] for r in rows]
    assert member not in ids and "buy_0001" in ids
    assert len(rows) <= accounts.DEMO_BUYERS_MAX
    assert all(set(r) == {"buyer_id", "display_name", "age_band"} for r in rows)
    assert rows[0]["display_name"] == "데모 구매자 0001"


def test_product_search_query_is_capped(client: TestClient) -> None:
    assert client.get("/api/shop/products", params={"q": "가" * 100}).status_code == 200
    assert client.get("/api/shop/products", params={"q": "가" * 101}).status_code == 422


def test_client_ip_uses_forwarded_for_only_when_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.requests import Request

    from ax_shop_api.main import _client_ip

    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"1.1.1.1, 203.0.113.7")],
        "client": ("10.0.0.2", 5000),
    }
    monkeypatch.delenv("SHOP_TRUST_PROXY", raising=False)
    assert _client_ip(Request(scope)) == "10.0.0.2"
    monkeypatch.setenv("SHOP_TRUST_PROXY", "on")
    assert _client_ip(Request(scope)) == "203.0.113.7"
