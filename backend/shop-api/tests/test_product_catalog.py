"""상품 소개·목록 검색/정렬/페이지·장르 목록·공개 베스트."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.genres import GENRES
from ax_shop_api.main import create_app

ADMIN = {"Authorization": "Bearer mock:admin"}
OLD_KEYS = {
    "product_id", "seller_id", "name", "category_code", "price", "stock", "active",
    "isbn13", "author", "publisher", "genre_code",
}  # fmt: skip


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("SHOP_BEST_CACHE_S", "0")  # 시험은 캐시를 끈다
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _patch(client: TestClient, product_id: str, **body: object) -> object:
    return client.patch(f"/api/shop/products/{product_id}", json=body, headers=ADMIN)


def _buy(client: TestClient, product_id: str, qty: int, pay: bool = True) -> None:
    body = {"buyer_id": "buy_0001", "items": [{"product_id": product_id, "qty": qty}]}
    order = client.post("/api/shop/orders", json=body).json()["data"]
    if pay:
        res = client.post(f"/api/shop/orders/{order['order_id']}/payments", json={})
        assert res.json()["data"]["status"] == "paid"


def test_product_list_without_params_is_unchanged(client: TestClient) -> None:
    res = client.get("/api/shop/products").json()
    assert isinstance(res["data"], list) and len(res["data"]) == 8
    assert res["meta"] == {}
    assert [p["product_id"] for p in res["data"]] == [f"prd_{n:04d}" for n in range(1, 9)]
    for item in res["data"]:
        assert set(item) == OLD_KEYS | {"description"}
        assert item["description"] is None


def test_description_and_genre_code_validation(client: TestClient) -> None:
    body = {"seller_id": "sel_0001", "name": "책", "category_code": "book", "price": 1, "stock": 1}
    assert client.post("/api/shop/products", json=body | {"genre_code": "소설"}, headers=ADMIN)\
        .status_code == 422  # fmt: skip
    long = client.post(
        "/api/shop/products", json=body | {"description": "가" * 4001}, headers=ADMIN
    )
    assert long.status_code == 422
    made = client.post(
        "/api/shop/products", json=body | {"genre_code": "novel", "description": " 소개 "},
        headers=ADMIN,
    ).json()["data"]  # fmt: skip
    assert made["genre_code"] == "novel" and made["description"] == "소개"
    cleared = _patch(client, made["product_id"], genre_code=None, description=None).json()["data"]
    assert cleared["genre_code"] is None and cleared["description"] is None


def test_search_matches_name_author_publisher(client: TestClient) -> None:
    _patch(client, "prd_0001", author="한강", publisher="Hanbit")

    def names(q: str) -> list[str]:
        res = client.get("/api/shop/products", params={"q": q}).json()["data"]
        return [p["product_id"] for p in res]

    assert names("한강") == ["prd_0001"]
    assert names("HAN") == ["prd_0001"]  # 출판사, 대소문자 무시
    assert names("노트") == ["prd_0004"]  # 이름
    assert names("%") == []  # 와일드카드는 글자 그대로다


def test_paging_and_sorting(client: TestClient) -> None:
    res = client.get(
        "/api/shop/products", params={"page": 1, "size": 3, "sort": "price_asc"}
    ).json()
    assert res["meta"] == {"total": 8, "page": 1, "size": 3}
    assert [p["price"] for p in res["data"]] == [7500, 9800, 13000]
    last = client.get("/api/shop/products", params={"page": 3, "size": 3}).json()
    assert len(last["data"]) == 2 and last["meta"]["total"] == 8
    desc = client.get("/api/shop/products", params={"sort": "price_desc"}).json()["data"]
    assert desc[0]["price"] == 31000 and isinstance(desc, list)
    newest = client.get("/api/shop/products", params={"sort": "new"}).json()["data"]
    assert len(newest) == 8
    assert client.get("/api/shop/products", params={"size": 61, "page": 1}).status_code == 422


def test_popular_sort_uses_paid_quantity(client: TestClient) -> None:
    _buy(client, "prd_0005", 3)
    first = client.get("/api/shop/products", params={"sort": "popular"}).json()["data"][0]
    assert first["product_id"] == "prd_0005"


def test_best_counts_paid_quantity_and_is_public(client: TestClient) -> None:
    _patch(client, "prd_0001", genre_code="it")
    _patch(client, "prd_0003", genre_code="essay")
    _buy(client, "prd_0001", 2)
    _buy(client, "prd_0003", 5)
    _buy(client, "prd_0002", 9, pay=False)  # 결제 안 된 주문은 세지 않는다
    res = client.get("/api/shop/products/best")  # 토큰 없음
    assert res.status_code == 200
    assert [(p["product_id"], p["sold_qty"]) for p in res.json()["data"]] == [
        ("prd_0003", 5), ("prd_0001", 2),
    ]  # fmt: skip
    only_it = client.get("/api/shop/products/best", params={"genre": "it"}).json()["data"]
    assert [p["product_id"] for p in only_it] == ["prd_0001"]
    assert client.get("/api/shop/products/best", params={"days": 94}).status_code == 422
    assert client.get("/api/shop/products/best", params={"limit": 0}).status_code == 422


def test_best_sold_quantities_are_cached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_BEST_CACHE_S", "300")
    _buy(client, "prd_0001", 1)
    assert len(client.get("/api/shop/products/best").json()["data"]) == 1
    _buy(client, "prd_0002", 1)
    assert len(client.get("/api/shop/products/best").json()["data"]) == 1  # 캐시
    assert len(client.get("/api/shop/products/best", params={"limit": 5}).json()["data"]) == 1


def test_unknown_genre_is_422_on_list_and_best(client: TestClient) -> None:
    assert client.get("/api/shop/products", params={"genre": "nope"}).status_code == 422
    assert client.get("/api/shop/products/best", params={"genre": "nope"}).status_code == 422
    assert client.get("/api/shop/products", params={"genre": ""}).status_code == 200


def test_cache_is_bounded_and_popular_works_with_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHOP_BEST_CACHE_S", "300")
    _buy(client, "prd_0005", 3)
    for limit in range(1, 30):
        client.get("/api/shop/products/best", params={"limit": limit})
    shop_cache = client.app.state.shop._sold_cache  # noqa: SLF001
    assert len(shop_cache) == 1  # limit는 키가 아니다
    first = client.get("/api/shop/products", params={"sort": "popular"}).json()["data"][0]
    assert first["product_id"] == "prd_0005"


def test_genres_list_counts_active_products(client: TestClient) -> None:
    _patch(client, "prd_0001", genre_code="it")
    _patch(client, "prd_0002", genre_code="it")
    _patch(client, "prd_0003", genre_code="essay")
    _patch(client, "prd_0003", active=False)
    rows = client.get("/api/shop/genres").json()["data"]
    assert [r["genre_code"] for r in rows] == list(GENRES)
    counts = {r["genre_code"]: r["product_count"] for r in rows}
    assert counts["it"] == 2 and counts["essay"] == 0 and counts["novel"] == 0
    assert rows[0]["name"] == "소설"
    filtered = client.get("/api/shop/products", params={"genre": "it"}).json()["data"]
    assert len(filtered) == 2
