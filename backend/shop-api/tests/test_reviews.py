"""구매 후기: 배송 완료된 본인 주문만, 한 번만. 공개 목록에는 신원이 없다."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

ADMIN = {"Authorization": "Bearer mock:admin"}
PRODUCT = "prd_0001"
PASSWORD = "correct-horse-1"
BODY = "읽기 좋은 책이에요. 추천합니다."


@pytest.fixture(autouse=True)
def _required(monkeypatch: pytest.MonkeyPatch) -> None:
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
    return {"Authorization": f"Bearer {data['token']}"}, data["user"]["buyer_id"]


def _paid_order(client: TestClient, headers: dict[str, str], deliver: bool = True) -> str:
    res = client.post(
        "/api/shop/orders", json={"items": [{"product_id": PRODUCT, "qty": 1}]}, headers=headers
    )
    assert res.status_code == 201, res.text
    order_id = res.json()["data"]["order_id"]
    paid = client.post(
        f"/api/shop/orders/{order_id}/payments", json={"simulate": "success"}, headers=headers
    )
    assert paid.status_code in (200, 201), paid.text
    if deliver:
        assert (
            client.post(f"/api/shop/admin/orders/{order_id}/delivered", json={}, headers=ADMIN)
        ).status_code == 200
    return order_id


def _review(client, headers, order_id, rating=5, body=BODY):  # noqa: ANN001, ANN202
    return client.post(
        f"/api/shop/products/{PRODUCT}/reviews",
        json={"order_id": order_id, "rating": rating, "body": body},
        headers=headers,
    )


def _set_age(db: Db, buyer_id: str, band: str) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE buyers SET age_band = ? WHERE buyer_id = ?", (band, buyer_id))


def test_only_delivered_buyers_can_review_once(client: TestClient) -> None:
    a, _ = _signup(client, "a@example.com")
    order_id = _paid_order(client, a, deliver=False)
    res = _review(client, a, order_id)
    assert res.status_code == 403 and res.json()["error"]["code"] == "NOT_REVIEWABLE"
    assert client.get("/api/shop/me/reviewable", headers=a).json()["data"] == []
    client.post(f"/api/shop/admin/orders/{order_id}/delivered", json={}, headers=ADMIN)
    reviewable = client.get("/api/shop/me/reviewable", headers=a).json()["data"]
    assert [(r["order_id"], r["product_id"]) for r in reviewable] == [(order_id, PRODUCT)]
    assert reviewable[0]["title"]
    assert _review(client, a, order_id).status_code == 201
    again = _review(client, a, order_id)
    assert again.status_code == 409 and again.json()["error"]["code"] == "REVIEW_EXISTS"
    assert client.get("/api/shop/me/reviewable", headers=a).json()["data"] == []
    mine = client.get("/api/shop/me/reviews", headers=a).json()["data"]
    assert len(mine) == 1 and mine[0]["order_id"] == order_id and mine[0]["product_id"] == PRODUCT


def test_others_cannot_review_my_order(client: TestClient) -> None:
    a, _ = _signup(client, "a@example.com")
    b, _ = _signup(client, "b@example.com")
    order_id = _paid_order(client, a)
    res = _review(client, b, order_id)
    assert res.status_code == 403 and res.json()["error"]["code"] == "NOT_REVIEWABLE"
    assert client.post(f"/api/shop/products/{PRODUCT}/reviews", json={}).status_code in (401, 422)


def test_public_list_hides_identity(client: TestClient, db: Db) -> None:
    a, a_id = _signup(client, "a@example.com")
    b, _ = _signup(client, "b@example.com")
    _set_age(db, a_id, "30s")
    assert _review(client, a, _paid_order(client, a)).status_code == 201
    assert _review(client, b, _paid_order(client, b)).status_code == 201
    res = client.get(f"/api/shop/products/{PRODUCT}/reviews")  # 토큰 없이
    assert res.status_code == 200
    items = res.json()["data"]["items"]
    assert sorted(i["author_label"] for i in items) == ["30대", "구매자"]
    assert res.json()["meta"] == {"total": 2, "page": 1, "size": 10}
    for item in items:
        assert set(item) == {"review_id", "rating", "body", "created_at", "author_label"}
    assert a_id not in res.text and "고양이" not in res.text and "buyer_id" not in res.text


def test_author_label_for_oldest_band(client: TestClient, db: Db) -> None:
    a, a_id = _signup(client, "a@example.com")
    _set_age(db, a_id, "50s_plus")
    _review(client, a, _paid_order(client, a))
    items = client.get(f"/api/shop/products/{PRODUCT}/reviews").json()["data"]["items"]
    assert items[0]["author_label"] == "50대 이상"


def test_summary_counts_and_average(client: TestClient) -> None:
    empty = client.get(f"/api/shop/products/{PRODUCT}/reviews").json()["data"]["summary"]
    assert empty["count"] == 0 and empty["avg"] == 0
    for n, rating in enumerate((5, 4, 4)):
        h, _ = _signup(client, f"u{n}@example.com")
        assert _review(client, h, _paid_order(client, h), rating=rating).status_code == 201
    res = client.get(f"/api/shop/products/{PRODUCT}/reviews?size=2&page=2").json()
    assert res["data"]["summary"] == {
        "count": 3,
        "avg": 4.33,
        "by_rating": {"1": 0, "2": 0, "3": 0, "4": 2, "5": 1},
    }
    assert len(res["data"]["items"]) == 1
    assert res["meta"] == {"total": 3, "page": 2, "size": 2}


@pytest.mark.parametrize(
    ("rating", "body"), [(0, BODY), (6, BODY), (5, "아홉글자입니다요"), (5, "가" * 1001)]
)
def test_rating_and_body_limits(client: TestClient, rating: int, body: str) -> None:
    a, _ = _signup(client, "a@example.com")
    order_id = _paid_order(client, a)
    assert _review(client, a, order_id, rating=rating, body=body).status_code == 422
    assert _review(client, a, order_id, body="가" * 1000).status_code == 201
