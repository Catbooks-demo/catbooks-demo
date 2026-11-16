"""상품의 책 정보(선택): ISBN-13·저자·출판사. 입력 확인, 저장·조회·지움, 옛 DB, 시드는 비어 있음."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.book_info import isbn13_check_digit, normalize_isbn13
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

ADMIN = {"Authorization": "Bearer mock:admin"}


def make_isbn(first12: str) -> str:
    return first12 + str(isbn13_check_digit(first12))


GOOD = make_isbn("978893643412")  # 확인 숫자를 맞춰 만든 값
GOOD2 = make_isbn("979116224148")
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


def test_a_known_isbn_passes_and_the_check_digit_is_computed() -> None:
    assert normalize_isbn13("978-0-306-40615-7") == "9780306406157"  # 널리 알려진 예시
    assert normalize_isbn13(f" {GOOD[:3]}-{GOOD[3:]} ") == GOOD
    assert normalize_isbn13("") is None and normalize_isbn13("   ") is None
    assert normalize_isbn13(None) is None


def test_create_with_book_info_returns_it_everywhere(client: TestClient) -> None:
    made = _create(client, isbn13=GOOD, author="  한강  ", publisher="문학동네")
    assert (made["isbn13"], made["author"], made["publisher"]) == (GOOD, "한강", "문학동네")
    got = client.get(f"/api/shop/products/{made['product_id']}").json()["data"]
    assert got["isbn13"] == GOOD and got["author"] == "한강"
    listed = {p["product_id"]: p for p in client.get("/api/shop/products").json()["data"]}
    assert listed[made["product_id"]]["publisher"] == "문학동네"


def test_hyphens_are_removed_before_saving(client: TestClient) -> None:
    hyphenated = f"{GOOD[:3]}-{GOOD[3:5]}-{GOOD[5:]}"
    assert _create(client, isbn13=hyphenated)["isbn13"] == GOOD


def test_without_book_info_the_values_are_none_and_the_seed_has_none(client: TestClient) -> None:
    made = _create(client)
    assert (made["isbn13"], made["author"], made["publisher"]) == (None, None, None)
    seeded = client.get("/api/shop/products").json()["data"][0]
    assert (seeded["isbn13"], seeded["author"], seeded["publisher"]) == (None, None, None)


@pytest.mark.parametrize(
    "bad",
    [
        GOOD[:12] + str((int(GOOD[12]) + 1) % 10),  # 확인 숫자가 틀림
        GOOD[:12],  # 12자리
        GOOD + "0",  # 14자리
        "97889364341AB",  # 숫자가 아님
        make_isbn("111893643412"),  # 978·979로 시작하지 않음
    ],
)
def test_a_wrong_isbn_is_rejected_with_422(client: TestClient, bad: str) -> None:
    res = client.post("/api/shop/products", json=NEW | {"isbn13": bad}, headers=ADMIN)
    assert res.status_code == 422, res.text
    created = client.get("/api/shop/products").json()["data"]
    assert all(p["name"] != "새 책" for p in created)  # 저장되지 않았다


def test_author_and_publisher_have_a_length_limit_and_blank_means_none(
    client: TestClient,
) -> None:
    too_long = "가" * 101
    for field in ("author", "publisher"):
        res = client.post("/api/shop/products", json=NEW | {field: too_long}, headers=ADMIN)
        assert res.status_code == 422
    blank = _create(client, author="   ", publisher="")
    assert blank["author"] is None and blank["publisher"] is None


def test_patch_sets_changes_and_clears_book_info(client: TestClient) -> None:
    pid = _create(client, isbn13=GOOD, author="한강", publisher="문학동네")["product_id"]
    url = f"/api/shop/products/{pid}"
    other = client.patch(url, json={"price": 20000}, headers=ADMIN).json()["data"]
    assert other["price"] == 20000 and other["isbn13"] == GOOD  # 칸을 안 보내면 그대로다
    changed = client.patch(url, json={"isbn13": GOOD2, "author": "새 저자"}, headers=ADMIN)
    assert changed.json()["data"]["isbn13"] == GOOD2
    assert changed.json()["data"]["publisher"] == "문학동네"
    cleared = client.patch(url, json={"isbn13": None, "publisher": ""}, headers=ADMIN)
    data = cleared.json()["data"]
    assert data["isbn13"] is None and data["publisher"] is None and data["author"] == "새 저자"
    wrong = client.patch(url, json={"isbn13": "123"}, headers=ADMIN)
    assert wrong.status_code == 422
    assert client.get(url).json()["data"]["author"] == "새 저자"


def test_only_product_writers_can_set_book_info(client: TestClient) -> None:
    pid = _create(client)["product_id"]
    anyone = client.patch(f"/api/shop/products/{pid}", json={"isbn13": GOOD})
    assert anyone.status_code == 401
    assert client.get(f"/api/shop/products/{pid}").json()["data"]["isbn13"] is None


def test_seller_registration_accepts_book_info_on_its_first_products(
    client: TestClient,
) -> None:
    body = {
        "name": "새 서점",
        "category_codes": ["book"],
        "products": [
            {"name": "책 A", "category_code": "book", "price": 1000, "stock": 5,
             "isbn13": GOOD, "author": "저자 A"},
            {"name": "책 B", "category_code": "book", "price": 2000, "stock": 5},
        ],
    }  # fmt: skip
    res = client.post("/api/shop/sellers", json=body, headers=ADMIN)
    assert res.status_code == 201, res.text
    seller_id = res.json()["data"]["seller_id"]
    mine = client.get("/api/shop/products", params={"seller_id": seller_id}).json()["data"]
    by_name = {p["name"]: p for p in mine}
    assert by_name["책 A"]["isbn13"] == GOOD and by_name["책 A"]["author"] == "저자 A"
    assert by_name["책 B"]["isbn13"] is None
    bad = {**body, "products": [{**body["products"][0], "isbn13": "123"}]}
    assert client.post("/api/shop/sellers", json=bad, headers=ADMIN).status_code == 422


def test_an_old_database_without_the_columns_gets_them_on_start(tmp_path) -> None:
    path = str(tmp_path / "old.db")
    first = Db(path)
    with first.tx():  # 열을 만든다 (초기화)
        pass
    conn = sqlite3.connect(path)
    for column in ("isbn13", "author", "publisher"):
        conn.execute(f"ALTER TABLE products DROP COLUMN {column}")
    conn.commit()
    conn.close()
    client = TestClient(create_app(Db(path)))
    product = client.get("/api/shop/products").json()["data"][0]
    assert (product["isbn13"], product["author"], product["publisher"]) == (None, None, None)
    made = client.post("/api/shop/products", json=NEW | {"isbn13": GOOD}, headers=ADMIN)
    assert made.status_code == 201 and made.json()["data"]["isbn13"] == GOOD
