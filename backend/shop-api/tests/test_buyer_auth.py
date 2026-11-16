"""구매자 경로 본인 확인: 로그인 토큰이 구매자를 정하고, 요청의 `buyer_id`는 믿지 않는다.

- `SHOP_BUYER_AUTH=required`(기본): 토큰 없이 구매자 경로를 부르면 401 `LOGIN_REQUIRED`.
- `admin` 토큰은 `buyer_id`를 들고 구매자를 대신할 수 있다.
- 주문 조회(`GET /orders`·`/orders/{id}`)는 AXcellent가 토큰 없이 읽으므로 기본은 열어 둔다.
"""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

ADMIN = {"Authorization": "Bearer mock:admin"}
SUPPORT = {"Authorization": "Bearer mock:support"}
FINANCE = {"Authorization": "Bearer mock:finance"}
ITEMS = [{"product_id": "prd_0001", "qty": 1}]
PASSWORD = "correct-horse-1"


@pytest.fixture(autouse=True)
def _required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("SHOP_BUYER_AUTH", "required")
    monkeypatch.delenv("SHOP_DEMO_LOGIN", raising=False)
    monkeypatch.delenv("SHOP_ORDERS_PUBLIC_READ", raising=False)
    events.reset_mock_bus()


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _signup(client: TestClient, email: str) -> tuple[dict[str, str], str]:
    res = client.post(
        "/api/shop/auth/signup",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": "고양이",
            "agree_terms": True,
            "agree_privacy": True,
        },
    )
    assert res.status_code == 201
    data = res.json()["data"]
    return _bearer(data["token"]), data["user"]["buyer_id"]


def _order(client: TestClient, headers: dict[str, str], **body: object) -> dict:
    res = client.post("/api/shop/orders", json={"items": ITEMS, **body}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["data"]


def _code(res) -> str:  # noqa: ANN001
    return res.json()["error"]["code"]


def test_buyer_routes_need_a_login_by_default(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = client.post("/api/shop/orders", json={"buyer_id": "buy_0001", "items": ITEMS})
    assert res.status_code == 401 and _code(res) == "LOGIN_REQUIRED"
    for path in ("/api/shop/claims", "/api/shop/inquiries", "/api/shop/ideas"):
        res = client.get(path, params={"buyer_id": "buy_0001"})
        assert res.status_code == 401 and _code(res) == "LOGIN_REQUIRED", path
    idea = client.post(
        "/api/shop/ideas", json={"buyer_id": "buy_0001", "kind": "event", "text": "행사"}
    )
    assert idea.status_code == 401 and _code(idea) == "LOGIN_REQUIRED"
    # Review Focus 4: 배포에서 SHOP_BUYER_AUTH를 안 넣어도 남의 이름으로 주문할 수 없다
    monkeypatch.delenv("SHOP_BUYER_AUTH")
    res = client.post("/api/shop/orders", json={"buyer_id": "buy_0001", "items": ITEMS})
    assert res.status_code == 401 and _code(res) == "LOGIN_REQUIRED"
    monkeypatch.setenv("SHOP_BUYER_AUTH", "something-else")  # 모르는 값도 required로 본다
    res = client.post("/api/shop/orders", json={"buyer_id": "buy_0001", "items": ITEMS})
    assert res.status_code == 401 and _code(res) == "LOGIN_REQUIRED"


def test_the_token_decides_the_buyer(client: TestClient) -> None:
    a, a_id = _signup(client, "a@example.com")
    _b, b_id = _signup(client, "b@example.com")
    res = client.post("/api/shop/orders", json={"buyer_id": b_id, "items": ITEMS}, headers=a)
    assert res.status_code == 403 and _code(res) == "BUYER_MISMATCH"
    assert _order(client, a)["buyer_id"] == a_id
    assert _order(client, a, buyer_id=a_id)["buyer_id"] == a_id


def test_admin_can_act_for_a_buyer(client: TestClient) -> None:
    res = client.post(
        "/api/shop/orders", json={"buyer_id": "buy_0001", "items": ITEMS}, headers=ADMIN
    )
    assert res.status_code == 201 and res.json()["data"]["buyer_id"] == "buy_0001"
    order_id = res.json()["data"]["order_id"]
    paid = client.post(
        f"/api/shop/orders/{order_id}/payments", json={"simulate": "success"}, headers=ADMIN
    )
    assert paid.status_code == 200
    missing = client.post("/api/shop/orders", json={"items": ITEMS}, headers=ADMIN)
    assert missing.status_code == 422 and _code(missing) == "BUYER_REQUIRED"
    # 다른 서비스 토큰은 구매자를 대신할 수 없다
    other = client.post(
        "/api/shop/orders", json={"buyer_id": "buy_0001", "items": ITEMS}, headers=SUPPORT
    )
    assert other.status_code == 403


def test_my_inquiries_and_claims_are_only_mine(client: TestClient) -> None:
    a, _a_id = _signup(client, "a@example.com")
    _b, b_id = _signup(client, "b@example.com")
    for path in ("/api/shop/inquiries", "/api/shop/claims", "/api/shop/ideas"):
        res = client.get(path, params={"buyer_id": b_id}, headers=a)
        assert res.status_code == 403 and _code(res) == "BUYER_MISMATCH", path
        mine = client.get(path, headers=a)
        assert mine.status_code == 200 and mine.json()["data"] == [], path
    detail = client.get("/api/shop/inquiries/1", params={"buyer_id": b_id}, headers=a)
    assert detail.status_code == 403 and _code(detail) == "BUYER_MISMATCH"
    # 남의 주문에 문의를 걸 수 없다(없는 주문과 같은 404)
    b_order = _order(client, ADMIN, buyer_id=b_id)
    res = client.post(
        "/api/shop/inquiries",
        json={"order_id": b_order["order_id"], "body": "배송이 늦어요"},
        headers=a,
    )
    assert res.status_code == 404


def test_order_reads_stay_open_for_axcellent(client: TestClient) -> None:
    order = _order(client, ADMIN, buyer_id="buy_0001")
    listed = client.get("/api/shop/orders", params={"buyer_id": "buy_0001"})
    assert listed.status_code == 200
    assert order["order_id"] in [o["order_id"] for o in listed.json()["data"]]
    assert client.get("/api/shop/orders").status_code == 200
    one = client.get(f"/api/shop/orders/{order['order_id']}")
    assert one.status_code == 200 and one.json()["data"]["buyer_id"] == "buy_0001"
    assert client.get("/api/shop/orders", headers=FINANCE).status_code == 200


def test_order_reads_can_be_closed(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    order = _order(client, ADMIN, buyer_id="buy_0001")
    monkeypatch.setenv("SHOP_ORDERS_PUBLIC_READ", "off")
    assert client.get("/api/shop/orders").status_code == 401
    assert client.get(f"/api/shop/orders/{order['order_id']}").status_code == 401
    assert client.get("/api/shop/orders", headers=FINANCE).status_code == 200
    one = client.get(f"/api/shop/orders/{order['order_id']}", headers=FINANCE)
    assert one.status_code == 200


def test_a_buyer_sees_only_own_orders(client: TestClient) -> None:
    a, a_id = _signup(client, "a@example.com")
    b, b_id = _signup(client, "b@example.com")
    mine = _order(client, a)
    theirs = _order(client, b)
    listed = client.get("/api/shop/orders", headers=a)
    assert listed.status_code == 200
    rows = listed.json()["data"]
    assert [o["order_id"] for o in rows] == [mine["order_id"]]
    assert {o["buyer_id"] for o in rows} == {a_id}
    assert client.get(f"/api/shop/orders/{mine['order_id']}", headers=a).status_code == 200
    other = client.get(f"/api/shop/orders/{theirs['order_id']}", headers=a)
    assert other.status_code == 404
    asked = client.get("/api/shop/orders", params={"buyer_id": b_id}, headers=a)
    assert asked.status_code == 403 and _code(asked) == "BUYER_MISMATCH"
    # 남의 주문은 결제·취소도 없는 주문과 같은 404다
    pay = client.post(
        f"/api/shop/orders/{theirs['order_id']}/payments", json={"simulate": "success"}, headers=a
    )
    assert pay.status_code == 404
    cancel = client.post(f"/api/shop/orders/{theirs['order_id']}/cancel", json={}, headers=a)
    assert cancel.status_code == 404
    assert client.post(f"/api/shop/orders/{theirs['order_id']}/cancel", json={}).status_code == 401
    own_pay = client.post(
        f"/api/shop/orders/{mine['order_id']}/payments", json={"simulate": "success"}, headers=a
    )
    assert own_pay.status_code == 200


@pytest.mark.parametrize("mode", ["required", "legacy"])
def test_unknown_tokens_are_rejected_even_on_open_reads(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("SHOP_BUYER_AUTH", mode)
    order = _order(client, ADMIN, buyer_id="buy_0001")
    nope = _bearer("not-a-real-token")
    for res in (
        client.get("/api/shop/orders", headers=nope),
        client.get(f"/api/shop/orders/{order['order_id']}", headers=nope),
        client.post(
            "/api/shop/orders", json={"buyer_id": "buy_0001", "items": ITEMS}, headers=nope
        ),
    ):
        assert res.status_code == 401 and _code(res) == "UNAUTHORIZED"
    # 로그아웃한 토큰도 알 수 없는 토큰이다
    a, _ = _signup(client, "a@example.com")
    client.post("/api/shop/auth/logout", headers=a)
    assert client.get("/api/shop/orders", headers=a).status_code == 401


def test_user_list_is_admin_only(client: TestClient) -> None:
    a, a_id = _signup(client, "a@example.com")
    _b, b_id = _signup(client, "b@example.com")
    assert client.get("/api/shop/users").status_code == 401
    assert client.get("/api/shop/users", headers=a).status_code == 401
    assert client.get("/api/shop/users", headers=SUPPORT).status_code == 403
    assert client.get("/api/shop/users", headers=ADMIN).status_code == 200
    # 한 명 조회·수정은 본인 또는 admin
    assert client.get(f"/api/shop/users/{a_id}", headers=a).status_code == 200
    other = client.get(f"/api/shop/users/{b_id}", headers=a)
    assert other.status_code == 403 and _code(other) == "BUYER_MISMATCH"
    assert client.get(f"/api/shop/users/{b_id}").status_code == 401
    assert client.get(f"/api/shop/users/{b_id}", headers=ADMIN).status_code == 200
    patch = client.patch(f"/api/shop/users/{b_id}", json={"gender": "female"}, headers=a)
    assert patch.status_code == 403
    done = client.patch(f"/api/shop/users/{b_id}", json={"gender": "female"}, headers=ADMIN)
    assert done.status_code == 200 and done.json()["data"]["gender"] == "female"


def _updated_at(db: Db, buyer_id: str) -> str:
    with db.tx() as conn:
        return conn.execute(
            "SELECT updated_at FROM buyers WHERE buyer_id = ?", (buyer_id,)
        ).fetchone()["updated_at"]


def _age(db: Db, buyer_id: str) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE buyers SET updated_at = '2000-01-01T00:00:00Z' WHERE buyer_id = ?", (buyer_id,)
        )


def test_me_reads_and_updates_profile(client: TestClient, db: Db) -> None:
    a, a_id = _signup(client, "a@example.com")
    assert client.get("/api/shop/me").status_code == 401
    assert _code(client.get("/api/shop/me")) == "LOGIN_REQUIRED"
    assert client.get("/api/shop/me", headers=ADMIN).status_code == 403
    me = client.get("/api/shop/me", headers=a)
    assert me.status_code == 200 and me.json()["data"]["buyer_id"] == a_id
    assert me.json()["data"]["job_group"] is None
    _age(db, a_id)
    res = client.patch("/api/shop/me", json={"job_group": "employee"}, headers=a)
    assert res.status_code == 200 and res.json()["data"]["job_group"] == "employee"
    assert client.get("/api/shop/me", headers=a).json()["data"]["job_group"] == "employee"
    assert _updated_at(db, a_id) != "2000-01-01T00:00:00Z"
    bad = client.patch("/api/shop/me", json={"job_group": "astronaut"}, headers=a)
    assert bad.status_code == 422
    assert client.patch("/api/shop/me", json={}, headers=a).status_code == 422
    # PATCH /users/{id}(연령대·성별)도 바꾼 시각을 적는다
    _age(db, a_id)
    res = client.patch(f"/api/shop/users/{a_id}", json={"age_band": "30s"}, headers=a)
    assert res.status_code == 200 and res.json()["data"]["age_band"] == "30s"
    assert _updated_at(db, a_id) != "2000-01-01T00:00:00Z"


def test_change_password_needs_the_current_one(client: TestClient) -> None:
    first, _ = _signup(client, "a@example.com")
    login = client.post(
        "/api/shop/auth/login", json={"email": "a@example.com", "password": PASSWORD}
    )
    other = _bearer(login.json()["data"]["token"])
    wrong = client.post(
        "/api/shop/me/password",
        json={"current": "wrong-password", "new": "new-horse-22"},
        headers=first,
    )
    assert wrong.status_code == 401
    ok = client.post(
        "/api/shop/me/password", json={"current": PASSWORD, "new": "new-horse-22"}, headers=first
    )
    assert ok.status_code == 200
    new_login = {"email": "a@example.com", "password": "new-horse-22"}
    assert client.post("/api/shop/auth/login", json=new_login).status_code == 200
    old_login = {"email": "a@example.com", "password": PASSWORD}
    assert client.post("/api/shop/auth/login", json=old_login).status_code == 401
    # 바꾼 세션은 남고 다른 세션은 끊긴다
    assert client.get("/api/shop/me", headers=first).status_code == 200
    assert client.get("/api/shop/me", headers=other).status_code == 401
    assert (
        client.post("/api/shop/me/password", json={"current": "x", "new": "y"}).status_code == 401
    )


def test_demo_login_gives_a_token_only_when_enabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Review Focus 4: SHOP_DEMO_LOGIN을 안 넣으면 비밀번호 없는 로그인은 없다
    assert client.post("/api/shop/sessions", json={"buyer_id": "buy_0002"}).status_code == 404
    demo = client.post("/api/shop/auth/demo-login", json={"buyer_id": "buy_0002"})
    assert demo.status_code == 404
    monkeypatch.setenv("SHOP_DEMO_LOGIN", "on")
    res = client.post("/api/shop/sessions", json={"buyer_id": "buy_0002"})
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["user"]["buyer_id"] == "buy_0002" and data["token"]
    assert _order(client, _bearer(data["token"]))["buyer_id"] == "buy_0002"
    missing = client.post("/api/shop/sessions", json={"buyer_id": "buy_9999"})
    assert missing.status_code == 404 and _code(missing) == "BUYER_NOT_FOUND"
