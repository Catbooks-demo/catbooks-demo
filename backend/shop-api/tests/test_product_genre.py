"""상품 장르 코드(`genre_code`): 목록은 한 곳(`genres.GENRES`), 저장·조회·지움, 옛 DB, 시드 없음."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.genres import GENRES, normalize_genre
from ax_shop_api.main import create_app

ADMIN = {"Authorization": "Bearer mock:admin"}
NEW = {"seller_id": "sel_0001", "name": "새 책", "category_code": "book", "price": 1, "stock": 1}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _create(client: TestClient, **extra: object) -> dict:
    res = client.post("/api/shop/products", json=NEW | extra, headers=ADMIN)
    assert res.status_code == 201, res.text
    return res.json()["data"]


def test_the_list_has_the_sixteen_agreed_codes_in_order() -> None:
    assert list(GENRES) == [
        "novel", "essay", "business", "self_help", "humanities", "society", "history",
        "science", "it", "language", "kids", "comics", "hobby",
        "writing", "notebook", "deco",
    ]  # fmt: skip
    assert GENRES["it"] == "AI·IT" and GENRES["deco"] == "다꾸" and len(GENRES) == 16


def test_normalize_trims_and_treats_blank_as_unknown() -> None:
    assert normalize_genre(" novel ") == "novel"
    assert normalize_genre(None) is None and normalize_genre("  ") is None
    with pytest.raises(ValueError):
        normalize_genre("fantasy")


@pytest.mark.parametrize("code", list(GENRES))
def test_every_code_is_saved_and_returned_everywhere(client: TestClient, code: str) -> None:
    made = _create(client, genre_code=code)
    assert made["genre_code"] == code
    got = client.get(f"/api/shop/products/{made['product_id']}").json()["data"]
    assert got["genre_code"] == code
    listed = {p["product_id"]: p for p in client.get("/api/shop/products").json()["data"]}
    assert listed[made["product_id"]]["genre_code"] == code


def test_without_a_genre_the_value_is_none_and_the_seed_has_none(client: TestClient) -> None:
    assert _create(client)["genre_code"] is None
    assert all(p["genre_code"] is None for p in client.get("/api/shop/products").json()["data"])


@pytest.mark.parametrize("bad", ["fantasy", "NOVEL", "소설", 3, ["novel"]])
def test_a_code_outside_the_list_is_rejected_and_nothing_is_saved(
    client: TestClient, bad: object
) -> None:
    res = client.post("/api/shop/products", json=NEW | {"genre_code": bad}, headers=ADMIN)
    assert res.status_code == 422, res.text
    assert all(p["name"] != "새 책" for p in client.get("/api/shop/products").json()["data"])


def test_blank_means_unknown(client: TestClient) -> None:
    assert _create(client, genre_code="   ")["genre_code"] is None


def test_patch_sets_changes_and_clears_the_genre(client: TestClient) -> None:
    pid = _create(client, genre_code="novel")["product_id"]
    url = f"/api/shop/products/{pid}"
    other = client.patch(url, json={"price": 20000}, headers=ADMIN).json()["data"]
    assert other["price"] == 20000 and other["genre_code"] == "novel"  # 칸을 안 보내면 그대로다
    changed = client.patch(url, json={"genre_code": "it"}, headers=ADMIN).json()["data"]
    assert changed["genre_code"] == "it"
    cleared = client.patch(url, json={"genre_code": None}, headers=ADMIN).json()["data"]
    assert cleared["genre_code"] is None
    assert client.patch(url, json={"genre_code": "x"}, headers=ADMIN).status_code == 422
    assert client.get(url).json()["data"]["genre_code"] is None


def test_only_product_writers_can_set_the_genre(client: TestClient) -> None:
    pid = _create(client)["product_id"]
    anyone = client.patch(f"/api/shop/products/{pid}", json={"genre_code": "novel"})
    assert anyone.status_code == 401
    assert client.get(f"/api/shop/products/{pid}").json()["data"]["genre_code"] is None


def test_seller_registration_accepts_the_genre_on_its_first_products(
    client: TestClient,
) -> None:
    body = {
        "name": "새 서점",
        "category_codes": ["book"],
        "products": [
            {"name": "책 A", "category_code": "book", "price": 1000, "stock": 5,
             "genre_code": "history"},
            {"name": "책 B", "category_code": "book", "price": 2000, "stock": 5},
        ],
    }  # fmt: skip
    res = client.post("/api/shop/sellers", json=body, headers=ADMIN)
    assert res.status_code == 201, res.text
    seller_id = res.json()["data"]["seller_id"]
    mine = client.get("/api/shop/products", params={"seller_id": seller_id}).json()["data"]
    by_name = {p["name"]: p for p in mine}
    assert by_name["책 A"]["genre_code"] == "history" and by_name["책 B"]["genre_code"] is None
    bad = {**body, "products": [{**body["products"][0], "genre_code": "x"}]}
    assert client.post("/api/shop/sellers", json=bad, headers=ADMIN).status_code == 422


def test_an_old_database_without_the_column_gets_it_on_start(tmp_path) -> None:
    path = str(tmp_path / "old.db")
    with Db(path).tx():  # 열을 만든다 (초기화)
        pass
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE products DROP COLUMN genre_code")
    conn.commit()
    conn.close()
    client = TestClient(create_app(Db(path)))
    assert client.get("/api/shop/products").json()["data"][0]["genre_code"] is None
    made = client.post("/api/shop/products", json=NEW | {"genre_code": "kids"}, headers=ADMIN)
    assert made.status_code == 201 and made.json()["data"]["genre_code"] == "kids"
