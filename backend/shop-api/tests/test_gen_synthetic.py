"""가상 회원·주문 넣기(scripts/gen_synthetic.py). 작은 회원 수·임시 DB 파일, 네트워크 없음."""

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_shop_api import synthetic_ids
from ax_shop_api.accounts import Accounts
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.genres import GENRES
from ax_shop_api.lookups import Lookups
from ax_shop_api.seed import seed
from ax_shop_api.service import FREE_SHIPPING_FROM, SHIPPING_FEE, Shop

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_synthetic.py"
_spec = importlib.util.spec_from_file_location("gen_synthetic", SCRIPT)
assert _spec and _spec.loader
gen = importlib.util.module_from_spec(_spec)
sys.modules["gen_synthetic"] = gen  # dataclass가 모듈을 찾는다
_spec.loader.exec_module(gen)

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)  # 한국 시간 11/17 12:00
END = date(2026, 11, 15)
MEMBERS = 4000
TABLES = (
    "buyers",
    "accounts",
    "orders",
    "order_items",
    "payments",
    "claim_inbox",
    "inquiry_inbox",
    "reviews",
    "refunds",
    "outbox_events",
)
STATIONERY = ("writing", "notebook", "deco")


def _db(path: Path) -> Db:
    """시드(판매자·구매자 30명·상품 8개) + 장르마다 책 6권·문구 3개."""
    db = Db(str(path))
    seed(db)
    now = "2026-08-01T00:00:00Z"
    with db.tx() as conn:
        number = 100
        for genre in GENRES:
            stationery = genre in STATIONERY
            for i in range(3 if stationery else 6):
                number += 1
                name = f"{'AI 업무 ' if genre == 'it' and i < 2 else ''}{genre} 상품 {i + 1}"
                conn.execute(
                    "INSERT INTO products (product_id, seller_id, name, category_code, price, "
                    "stock, active, tenant_id, created_at, updated_at, genre_code) "
                    "VALUES (?, 'sel_0001', ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                    (
                        f"prd_{number:04d}",
                        name,
                        "stationery" if stationery else "book",
                        (5000 if stationery else 12000) + i * 1500,
                        40 + (number * 37) % 200,
                        TENANT_ID,
                        now,
                        now,
                        genre,
                    ),
                )
    return db


def gen_where() -> str:
    return synthetic_ids.BUYER_WHERE


def _run(db: Db, **changes) -> "gen.Result":
    config = gen.Config(members=MEMBERS, end=END, now=NOW, **changes)
    return gen.generate(db, config, log=lambda _: None)


def _counts(db: Db) -> dict[str, int]:
    with db.tx() as conn:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}


def _stock(db: Db) -> dict[str, int]:
    with db.tx() as conn:
        return dict(conn.execute("SELECT product_id, stock FROM products").fetchall())


def _key(result) -> dict:
    return {k: v for k, v in result.answer_key.items() if k != "generated_at"}


def test_same_seed_same_data(tmp_path: Path) -> None:
    a, b = _db(tmp_path / "a.db"), _db(tmp_path / "b.db")
    first, second = _run(a), _run(b)
    assert _counts(a) == _counts(b)
    assert _key(first) == _key(second)
    assert _counts(a)["orders"] > 300
    other = _run(_db(tmp_path / "c.db"), seed=7)
    assert other.answer_key["totals"] != first.answer_key["totals"]


def test_window_ends_the_day_before_generation_in_kst(tmp_path: Path) -> None:
    # 한국 시간 11/17 00:30에 만들면 끝 날짜는 11/16이고 마지막 1시간 안의 기록은 없다
    now = datetime(2026, 11, 16, 15, 30, tzinfo=UTC)
    assert gen.default_end(now) == date(2026, 11, 16)
    assert gen.default_end(NOW) == date(2026, 11, 16)
    db = _db(tmp_path / "w.db")
    result = gen.generate(db, gen.Config(members=MEMBERS, now=now), log=lambda _: None)
    config = result.answer_key["config"]
    assert (config["window_start"], config["window_end"]) == ("2026-08-19", "2026-11-16")
    limit = (now - timedelta(hours=1)).strftime(gen.STAMP)
    start = "2026-08-18T15:00:00Z"  # 8/19 0시(한국 시간)
    with db.tx() as conn:
        row = conn.execute(
            "SELECT MIN(created_at), MAX(created_at), MAX(updated_at), MAX(shipped_at), "
            "MAX(delivered_at) FROM orders"
        ).fetchone()
        stamps = [
            conn.execute(f"SELECT MAX(created_at) FROM {t}").fetchone()[0]
            for t in ("payments", "claim_inbox", "inquiry_inbox", "reviews")
        ]
        mine = f"SELECT MAX(created_at), MAX(updated_at) FROM buyers WHERE {gen_where()}"
        stamps += list(conn.execute(mine).fetchone())  # 시드 구매자는 시드를 넣은 시각이다
    assert row[0] >= start
    assert all(s <= limit for s in (*row[1:], *stamps) if s)
    with pytest.raises(ValueError):
        gen.build_window(gen.Config(end=date(2026, 11, 17)), NOW)


def test_claims_only_on_delivered_orders_with_valid_codes(tmp_path: Path) -> None:
    db = _db(tmp_path / "c.db")
    _run(db)
    with db.tx() as conn:
        claims = conn.execute(
            "SELECT c.*, o.delivered_at, o.status AS order_status FROM claim_inbox c "
            "JOIN orders o ON o.order_id = c.order_id"
        ).fetchall()
        rowids = {r[0]: r[1] for r in conn.execute("SELECT rowid, order_id FROM order_items")}
    assert len(claims) > 5
    for c in claims:
        assert c["order_status"] == "paid" and c["delivered_at"] is not None
        assert c["created_at"] > c["delivered_at"]
        assert c["reason"] in gen.REASON_CODES
        assert set(gen.REASON_CODES) == {
            "defect",
            "damaged",
            "wrong_item",
            "missing_item",
            "late_delivery",
            "change_of_mind",
            "other",
        }
        assert c["claim_type"] in ("return", "exchange")
        assert c["status"] == "received" and c["synced_at"] is None
        for item in json.loads(c["items"]):
            assert rowids[int(item["order_item_id"].removeprefix("oit_"))] == c["order_id"]
        if c["reason"] in ("defect", "damaged", "wrong_item"):
            assert c["evidence_count"] >= 1  # 화면도 이 사유에는 사진을 요구한다


def test_orders_follow_shop_rules(tmp_path: Path) -> None:
    db = _db(tmp_path / "r.db")
    _run(db)
    with db.tx() as conn:
        orders = conn.execute("SELECT * FROM orders").fetchall()
        items = {}
        for r in conn.execute("SELECT order_id, qty, unit_price FROM order_items"):
            items[r[0]] = items.get(r[0], 0) + r[1] * r[2]
        pays = {}
        for r in conn.execute("SELECT order_id, status, amount FROM payments ORDER BY rowid"):
            pays.setdefault(r[0], []).append((r[1], r[2]))
        genres = {
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT p.genre_code FROM order_items i "
                "JOIN products p ON p.product_id = i.product_id"
            )
        }
        bad_reviews = conn.execute(
            "SELECT COUNT(*) FROM reviews r JOIN orders o ON o.order_id = r.order_id "
            "WHERE o.delivered_at IS NULL OR r.created_at <= o.delivered_at"
        ).fetchone()[0]
        subjects = [r[0] for r in conn.execute("SELECT subject FROM inquiry_inbox")]
        images = conn.execute("SELECT COUNT(*) FROM inquiry_inbox WHERE images IS NOT NULL")
        images = images.fetchone()[0]
    assert genres <= {*GENRES, None}
    for o in orders:
        subtotal = items[o["order_id"]]
        fee = 0 if subtotal >= FREE_SHIPPING_FROM else SHIPPING_FEE
        assert (o["shipping_fee"], o["total_amount"]) == (fee, subtotal + fee)
        statuses = [s for s, _ in pays.get(o["order_id"], [])]
        assert all(amount == o["total_amount"] for _, amount in pays.get(o["order_id"], []))
        if o["status"] == "paid":
            assert statuses.count("succeeded") == 1 and statuses[-1] == "succeeded"
        else:  # 결제 전 취소·결제 실패: 성공한 결제가 없다(환불 없이 취소하지 않는다)
            assert o["status"] in ("cancelled", "payment_failed") and "succeeded" not in statuses
            assert o["shipped_at"] is None and o["delivered_at"] is None
        # 가상 취소는 모두 손님 취소다. 취소가 아닌 주문에는 취소 주체가 없다
        expected = "buyer" if o["status"] == "cancelled" else None
        assert o["cancel_reason_code"] == expected
        if o["delivered_at"]:
            assert o["shipped_at"] < o["delivered_at"]
    assert bad_reviews == 0 and images == 0
    prefixes = ("[주문·배송] ", "[결제·환불] ", "[상품] ", "[개인정보 삭제 요청] ")
    assert subjects and all(s.startswith(prefixes) and len(s) <= 100 for s in subjects)


def test_no_outbox_events_and_existing_rows_untouched(tmp_path: Path) -> None:
    db = _db(tmp_path / "o.db")
    shop = Shop(db)
    real = shop.create_order("buy_0001", [("prd_0101", 1)])  # 서비스 경로: outbox 이벤트 1건
    before = _counts(db)
    _run(db)
    after = _counts(db)
    assert before["outbox_events"] == after["outbox_events"] == 1
    assert shop.get_order(real["order_id"])["status"] == "pending_payment"


def test_synthetic_buyers_use_their_own_id_range(tmp_path: Path) -> None:
    db = _db(tmp_path / "b.db")
    _run(db)
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT b.buyer_id, b.change_seq, b.age_band, b.gender, b.occupation, a.email, "
            "a.password_hash FROM buyers b JOIN accounts a ON a.buyer_id = b.buyer_id"
        ).fetchall()
        seeds = conn.execute(
            "SELECT COUNT(*) FROM buyers WHERE buyer_id < 'buy_1' AND change_seq IS NULL"
        ).fetchone()[0]
    assert len(rows) == MEMBERS and seeds == 30
    assert all(synthetic_ids.is_synthetic(r["buyer_id"]) for r in rows)
    assert min(r["buyer_id"] for r in rows) == "buy_100001"
    assert all(r["email"].endswith("@synthetic.catbooks.invalid") for r in rows)
    assert len({r["password_hash"] for r in rows}) == 1
    assert rows[0]["password_hash"].startswith("scrypt$")
    assert len({r["change_seq"] for r in rows}) == MEMBERS  # 고객 내보내기가 모두 새로 읽는다
    blank = [r for r in rows if r["age_band"] is None]
    assert all(r["gender"] is None and r["occupation"] is None for r in blank)
    assert 0.04 < len(blank) / MEMBERS < 0.08  # 6%는 연령·성별·직업을 비워 둔다
    filled = [r for r in rows if r["age_band"] is not None]
    assert all(r["gender"] and r["occupation"] for r in filled)
    # 실제 가입자는 가상 회원 구간을 건너뛰고 시드 다음 번호를 받는다
    _, user = Accounts(db).signup("real@example.com", "password-123", "실제", None, None, None)
    assert user["buyer_id"] == "buy_0031"


def test_refuses_existing_then_replace_restores_and_repeats(tmp_path: Path) -> None:
    db = _db(tmp_path / "x.db")
    stock = _stock(db)
    first = _run(db)
    counts, stock_after = _counts(db), _stock(db)
    assert stock_after != stock
    with pytest.raises(gen.SyntheticExistsError):
        _run(db)
    with pytest.raises(gen.SyntheticExistsError):
        _run(db, dry_run=True)
    preview = _run(db, replace=True, dry_run=True)  # 지운 셈 치고 계산만
    assert _counts(db) == counts and _key(preview) == _key(first)
    again = _run(db, replace=True)
    assert again.deleted and again.deleted["buyers"] == MEMBERS
    assert _counts(db) == counts and _stock(db) == stock_after
    assert _key(again) == _key(first)
    with db.tx() as conn:
        gen.delete_synthetic(conn)
    assert _stock(db) == stock  # 지우면 재고가 처음으로 돌아온다
    assert _counts(db)["buyers"] == 30


def _claims_by_product(db: Db, since: str) -> dict[str, dict[str, int]]:
    with db.tx() as conn:
        product_of = dict(conn.execute("SELECT rowid, product_id FROM order_items").fetchall())
        out: dict[str, dict[str, int]] = {}
        for c in conn.execute(
            "SELECT reason, items FROM claim_inbox WHERE created_at >= ?", (since,)
        ):
            for item in json.loads(c["items"]):
                row = out.setdefault(
                    product_of[int(item["order_item_id"][4:])], {"claims": 0, "qty": 0, "defect": 0}
                )
                row["claims"] += 1
                row["qty"] += item["qty"]
                row["defect"] += c["reason"] in gen.DEFECT_REASONS
    return out


def _sold(db: Db, since: str) -> dict[str, int]:
    with db.tx() as conn:
        return dict(
            conn.execute(
                "SELECT i.product_id, SUM(i.qty) FROM orders o JOIN order_items i "
                "ON i.order_id = o.order_id WHERE o.status = 'paid' AND o.created_at >= ? "
                "GROUP BY i.product_id",
                (since,),
            ).fetchall()
        )


def _trips(sold: int, row: dict[str, int] | None) -> bool:
    row = row or {"claims": 0, "qty": 0, "defect": 0}
    rate_rule = sold >= 5 and row["qty"] / sold >= 0.10 and row["claims"] >= 2
    return rate_rule or row["defect"] >= 3


def test_planted_claim_books_and_small_group(tmp_path: Path) -> None:
    db = _db(tmp_path / "p.db")
    key = _run(db).answer_key
    planted = key["planted"]
    assert planted["defect_cluster"] and planted["low_sales_single"]
    assert planted["incidental_trips"] == []
    for since in ("2026-10-16T15:00:00Z", "2026-08-17T15:00:00Z"):  # 최근 30일, 기간 전체
        claims, sold = _claims_by_product(db, since), _sold(db, since)
        for book in planted["defect_cluster"]:
            pid = book["product_id"]
            assert _trips(sold.get(pid, 0), claims.get(pid)), pid
        for book in planted["low_sales_single"]:
            pid = book["product_id"]
            assert sold.get(pid, 0) < 5 and not _trips(sold.get(pid, 0), claims.get(pid)), pid
        marked = {b["product_id"] for b in planted["defect_cluster"] + planted["low_sales_single"]}
        others = [pid for pid in claims if pid not in marked]
        assert not [pid for pid in others if _trips(sold.get(pid, 0), claims[pid])]
    group = planted["small_group"]
    assert len(group["buyers"]) == 3
    summary = Lookups(db).sales_summary(
        datetime(2026, 8, 17, 15, tzinfo=UTC),
        datetime(2026, 11, 15, 15, tzinfo=UTC),
        "genre,age_band,gender,occupation",
    )
    rows = [
        r
        for r in summary["rows"]
        if (r["genre_code"], r["age_band"], r["gender"], r["occupation"])
        == ("language", "50s_plus", "female", "student")
    ]
    assert len(rows) == 1 and rows[0]["suppressed"] is True


def test_dry_run_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "d.db"
    db = _db(path)
    counts, stock = _counts(db), _stock(db)
    out = tmp_path / "out"
    monkeypatch.setattr(gen, "default_end", lambda now: END)
    code = gen.main(["--db", str(path), "--members", "300", "--dry-run", "--out", str(out)])
    assert code == 0
    assert _counts(db) == counts and _stock(db) == stock
    assert not out.exists()


def test_cli_writes_answer_key_outside_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "e.db"
    _db(path)
    out = tmp_path / "out"
    monkeypatch.setattr(gen, "default_end", lambda now: END)
    assert gen.main(["--db", str(path), "--members", "300", "--out", str(out)]) == 0
    key = json.loads((out / "answer_key.json").read_text(encoding="utf-8"))
    assert key["members"]["total"] == 300 and key["trend"]["start"] == "2026-10-08"
    assert set(key["by_genre"]) <= {*GENRES, "unknown"}
    lines = (out / "member_personas.csv").read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 301 and lines[0].startswith("buyer_id,persona")
    assert gen.main(["--db", str(path), "--members", "300", "--out", str(out)]) == 2  # 이미 있음


def test_bare_catalog_still_runs_with_warning(tmp_path: Path) -> None:
    db = Db(str(tmp_path / "bare.db"))
    seed(db)  # 장르 없는 시드 상품 8개뿐
    result = _run(db, dry_run=True)
    assert result.answer_key["totals"]["orders"] > 0
    assert any("장르가 붙은 책" in w for w in result.answer_key["warnings"])
    assert result.answer_key["catalog"]["products_by_genre"]["it"] == 0


def _real_customer(db: Db) -> dict[str, str]:
    """실제 가입자 한 명의 주문·결제·도착·후기·클레임(가상 데이터와 섞여도 남아야 한다)."""
    from ax_shop_api import claim_inbox
    from ax_shop_api.reviews import Reviews

    _, user = Accounts(db).signup("real@example.com", "password-123", "실제", "30s", None, None)
    buyer = user["buyer_id"]
    shop = Shop(db)
    order = shop.create_order(buyer, [("prd_0102", 1)])
    shop.pay(order["order_id"], "card", None)
    shop.mark_delivered(order["order_id"])
    Reviews(db).create(buyer, "prd_0102", order["order_id"], 5, "아주 좋은 책이었어요 정말로")
    claim_inbox.add(
        db, digest="real-claim", context_id="ctx_shop_real", buyer_id=buyer,
        customer_name=buyer, order_id=order["order_id"], type_="return", reason="defect",
        items=None, unsealed=False, evidence_count=1, description="파본",
    )  # fmt: skip
    return {"buyer": buyer, "order": order["order_id"]}


def _real_rows(db: Db, buyer: str) -> tuple:
    with db.tx() as conn:
        return tuple(
            conn.execute(f"SELECT COUNT(*) FROM {t} WHERE buyer_id = ?", (buyer,)).fetchone()[0]
            for t in ("buyers", "accounts", "orders", "reviews", "claim_inbox")
        )


def test_replace_keeps_real_signup_and_moves_change_seq_forward(tmp_path: Path) -> None:
    db = _db(tmp_path / "s.db")
    real = _real_customer(db)
    assert real["buyer"] == "buy_0031"
    before = _real_rows(db, real["buyer"])
    assert before == (1, 1, 1, 1, 1)
    first = _run(db)
    planted = {b["product_id"] for k in ("defect_cluster", "low_sales_single")
               for b in first.answer_key["planted"][k]}  # fmt: skip
    assert "prd_0102" not in planted  # 실제 클레임이 있는 책은 심지 않는다
    assert first.answer_key["existing_non_synthetic"]["products_with_claims"] == 1
    with db.tx() as conn:
        top = conn.execute("SELECT MAX(change_seq) FROM buyers").fetchone()[0]
    _run(db, replace=True)
    assert _real_rows(db, real["buyer"]) == before
    with db.tx() as conn:
        low = conn.execute(f"SELECT MIN(change_seq) FROM buyers WHERE {gen_where()}").fetchone()[0]
        orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        counter = conn.execute("SELECT value FROM counters WHERE name = 'order'").fetchone()[0]
    assert low > top  # 고객 내보내기 커서가 다시 지운 뒤 넣은 회원을 읽는다
    # 번호표: 실제 1건 + 가상 두 번(첫 번째는 지웠다). 다음 주문은 그 다음 번호다
    assert counter == 1 + 2 * (orders - 1)
    assert Shop(db).create_order("buy_0001", [("prd_0101", 1)])["order_id"] == (
        f"ord_{counter + 1:04d}"
    )


def test_memory_db_is_refused_unless_dry_run() -> None:
    assert gen.main(["--db", ":memory:", "--members", "10"]) == 1


def test_list_users_real_only_and_traffic_skips_synthetic(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from ax_shop_api.main import create_app
    from ax_shop_api.storefront import Storefront

    db = _db(tmp_path / "u.db")
    _run(db)
    every = Storefront(db).list_users()
    assert len(every) == 30 + MEMBERS
    real = Storefront(db).list_users(real_only=True)
    assert [u["buyer_id"] for u in real] == [f"buy_{n:04d}" for n in range(1, 31)]
    assert len(Storefront(db).list_users(real_only=True, limit=5)) == 5
    client = TestClient(create_app(db))
    data = client.get("/api/shop/users?real_only=true&limit=1000").json()["data"]
    assert len(data) == 30
    assert client.get("/api/shop/users?limit=5000").status_code == 422

    script = Path(__file__).resolve().parents[2] / "scripts" / "shop_traffic.py"
    spec = importlib.util.spec_from_file_location("shop_traffic_for_synth", script)
    assert spec and spec.loader
    traffic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(traffic)
    paths = []

    def call(method, path, body=None, token=None):
        paths.append(path)
        res = client.request(method, path, json=body)
        return res.status_code, res.json()

    buyers = traffic.order_buyers(call)
    assert paths == ["/api/shop/users?real_only=true&limit=1000"]
    assert buyers and not any(synthetic_ids.is_synthetic(b) for b in buyers)
    leaked = traffic.order_buyers(lambda *a, **k: (200, {"data": [{"buyer_id": "buy_100001"}]}))
    assert leaked == []  # 서버가 옛 버전이라 다 줘도 가상 회원은 고르지 않는다
