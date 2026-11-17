"""시드 데모 상품·구매자 지우기(scripts/clean_seed.py). 임시 DB 파일, 네트워크 없음."""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.main import create_app
from ax_shop_api.seed import SEED_BUYER_IDS, seed

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "clean_seed.py"
_spec = importlib.util.spec_from_file_location("clean_seed", SCRIPT)
assert _spec and _spec.loader
clean_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(clean_seed)

ADMIN = {"Authorization": "Bearer mock:admin"}
NOW = "2026-11-01T00:00:00Z"
TABLES = (
    "orders", "order_items", "payments", "reviews", "claim_inbox", "inquiry_inbox",
    "idea_inbox", "coupons", "sessions", "buyers", "products", "sellers", "outbox_events",
)  # fmt: skip


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("SHOP_DEMO_LOGIN", "on")
    events.reset_mock_bus()


def _order(client: TestClient, buyer: str, items: list[tuple[str, int]], pay: bool) -> str:
    res = client.post(
        "/api/shop/orders",
        json={"buyer_id": buyer, "items": [{"product_id": p, "qty": q} for p, q in items]},
        headers=ADMIN,
    )
    assert res.status_code == 201, res.text
    order_id = res.json()["data"]["order_id"]
    if pay:
        assert client.post(
            f"/api/shop/orders/{order_id}/payments", json={}, headers=ADMIN
        ).status_code == 200  # fmt: skip
    return order_id


def _world(path: Path) -> tuple[Db, TestClient, dict]:
    """시드 + 실제 책 1권 + 시드 구매자 시험 주문(딸린 행 모두) + 실제 가입자·가상 회원 주문."""
    db = Db(str(path))
    seed(db)
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO products (product_id, seller_id, name, category_code, price, stock, "
            "active, tenant_id, created_at, updated_at, genre_code) VALUES "
            "('prd_0100', 'sel_0001', '실제 책', 'book', 15000, 50, 1, ?, ?, ?, 'novel')",
            (TENANT_ID, NOW, NOW),
        )
        conn.execute("UPDATE counters SET value = 100 WHERE name = 'product'")
        conn.execute(
            "INSERT INTO buyers (buyer_id, tenant_id, created_at) VALUES ('buy_100001', ?, ?)",
            (TENANT_ID, NOW),
        )
    client = TestClient(create_app(db))
    seed_order = _order(client, "buy_0001", [("prd_0001", 1), ("prd_0100", 2)], pay=True)
    _order(client, "buy_0002", [("prd_0002", 1)], pay=False)
    client.post(f"/api/shop/admin/orders/{seed_order}/shipped", json={}, headers=ADMIN)
    client.post(f"/api/shop/admin/orders/{seed_order}/delivered", json={}, headers=ADMIN)
    review = client.post(
        "/api/shop/products/prd_0001/reviews",
        json={"buyer_id": "buy_0001", "order_id": seed_order, "rating": 5,
              "body": "아주 좋은 책이었어요 추천해요"},
        headers=ADMIN,
    )  # fmt: skip
    assert review.status_code == 201, review.text
    claim = client.post(
        "/api/shop/claims",
        json={"buyer_id": "buy_0001", "customer_name": "데모", "order_id": seed_order,
              "type": "return", "reason": "change_of_mind"},
        headers=ADMIN,
    )  # fmt: skip
    assert claim.status_code == 201, claim.text
    inquiry = client.post(
        "/api/shop/inquiries",
        json={"buyer_id": "buy_0001", "order_id": seed_order, "body": "영수증 부탁해요"},
        headers=ADMIN,
    )
    assert inquiry.status_code == 201, inquiry.text
    assert client.post("/api/shop/sessions", json={"buyer_id": "buy_0003"}).status_code == 200
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO coupons (coupon_id, buyer_id, purpose_code, count, unit_value, "
            "total_value, idempotency_key, tenant_id, created_at) "
            "VALUES ('cpn_0001', 'buy_0004', 'campaign', 1, 1000, 1000, 'k1', ?, ?)",
            (TENANT_ID, NOW),
        )
    signup = client.post(
        "/api/shop/auth/signup",
        json={"email": "cat@example.com", "password": "correct-horse-1", "display_name": "고양이",
              "agree_terms": True, "agree_privacy": True},
    )  # fmt: skip
    real = signup.json()["data"]["user"]["buyer_id"]
    kept = {
        "real": _order(client, real, [("prd_0100", 1)], pay=True),
        # 가상 회원 주문이 시드 상품을 담았다: 그 상품은 지우지 않고 판매 중지한다
        "synthetic": _order(client, "buy_100001", [("prd_0003", 1), ("prd_0100", 1)], pay=True),
        "seed_order": seed_order,
        "real_buyer": real,
    }
    return db, client, kept


def _counts(db: Db) -> dict[str, int]:
    with db.tx() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}  # noqa: S608


def _stock(db: Db, pid: str) -> int:
    with db.tx() as conn:
        return conn.execute("SELECT stock FROM products WHERE product_id = ?", (pid,)).fetchone()[0]


def test_dry_run_counts_and_changes_nothing(tmp_path: Path) -> None:
    db, _client, _kept = _world(tmp_path / "shop.db")
    before, stock = _counts(db), _stock(db, "prd_0100")
    counts = clean_seed.run(db, apply=False)
    assert counts["buyers"] == 30 and counts["orders"] == 2 and counts["order_items"] == 3
    assert counts["payments"] == 1 and counts["reviews"] == 1 and counts["claim_inbox"] == 1
    assert counts["inquiry_inbox"] == 1 and counts["coupons"] == 1 and counts["sessions"] == 1
    assert counts["products"] == 7 and counts["products_deactivated"] == ["prd_0003"]
    assert counts["stock_restored_units"] == 2 and counts["sellers_kept"] == 3
    assert _counts(db) == before and _stock(db, "prd_0100") == stock
    assert clean_seed.main(["--db", str(tmp_path / "shop.db"), "--dry-run"]) == 0
    assert _counts(db) == before


def test_removes_seed_rows_and_keeps_the_rest(tmp_path: Path) -> None:
    db, client, kept = _world(tmp_path / "shop.db")
    stock = _stock(db, "prd_0100")
    with db.tx() as conn:
        outbox = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
    assert clean_seed.main(["--db", str(tmp_path / "shop.db")]) == 0
    db = Db(str(tmp_path / "shop.db"))
    marks = ",".join("?" * len(SEED_BUYER_IDS))
    with db.tx() as conn:
        for table in ("buyers", "orders", "reviews", "claim_inbox", "inquiry_inbox", "coupons",
                      "sessions", "accounts"):  # fmt: skip
            left = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE buyer_id IN ({marks})",  # noqa: S608
                tuple(SEED_BUYER_IDS),
            ).fetchone()[0]
            assert left == 0, table
        for table in ("order_items", "payments"):
            left = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE order_id = ?",  # noqa: S608
                (kept["seed_order"],),
            ).fetchone()[0]
            assert left == 0, table
        products = dict(conn.execute("SELECT product_id, active FROM products").fetchall())
        sellers = [r[0] for r in conn.execute("SELECT seller_id FROM sellers ORDER BY 1")]
        orders = {r[0] for r in conn.execute("SELECT order_id FROM orders")}
        assert conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0] == outbox
    assert products == {"prd_0003": 0, "prd_0100": 1}
    assert sellers == ["sel_0001", "sel_0002", "sel_0003"]
    assert orders == {kept["real"], kept["synthetic"]}
    assert _stock(db, "prd_0100") == stock + 2  # 지운 시드 주문이 가져간 재고를 되돌렸다
    assert seed(db) is False  # 판매자가 남아 다시 넣지 않는다
    app = TestClient(create_app(db))
    assert app.get(f"/api/shop/orders/{kept['synthetic']}", headers=ADMIN).status_code == 200
    # 데모 로그인은 시드가 없어도 멈추지 않는다(없는 구매자는 404, 목록은 비었다)
    listed = app.get("/api/shop/auth/demo-buyers").json()["data"]
    assert not {b["buyer_id"] for b in listed} & SEED_BUYER_IDS
    assert app.post("/api/shop/auth/demo-login", json={"buyer_id": "buy_0001"}).status_code == 404
    del client


def test_signups_never_reuse_seed_numbers(tmp_path: Path) -> None:
    db = Db(str(tmp_path / "shop.db"))
    seed(db)
    with pytest.raises(clean_seed.NoCatalogError):  # 실제 상품이 없으면 지우지 않는다
        clean_seed.run(db, apply=True)
    assert clean_seed.main(["--db", str(tmp_path / "shop.db")]) == 1
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM buyers").fetchone()[0] == 30
    clean_seed.run(db, apply=True, force=True)
    client = TestClient(create_app(db))
    res = client.post(
        "/api/shop/auth/signup",
        json={"email": "new@example.com", "password": "correct-horse-1", "display_name": "새",
              "agree_terms": True, "agree_privacy": True},
    )  # fmt: skip
    assert res.json()["data"]["user"]["buyer_id"] == "buy_0031"


def test_memory_db_is_refused() -> None:
    with pytest.raises(ValueError):
        clean_seed.run(Db(":memory:"), apply=False)
    assert clean_seed.main(["--db", ":memory:"]) == 1
