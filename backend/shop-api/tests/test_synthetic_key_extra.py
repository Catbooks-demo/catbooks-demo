"""덧붙임 정답지(scripts/synthetic_key_extra.py): 손으로 셀 수 있는 작은 DB로 lift·이탈을 맞춘다."""

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.seed import seed

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "synthetic_key_extra.py"
_spec = importlib.util.spec_from_file_location("synthetic_key_extra", SCRIPT)
assert _spec and _spec.loader
extra = importlib.util.module_from_spec(_spec)
sys.modules["synthetic_key_extra"] = extra
_spec.loader.exec_module(extra)

KEY = {
    "config": {
        "seed": 1,
        "members": 5,
        "window_start": "2026-08-18",
        "window_end": "2026-11-15",
        "trend_start": "2026-10-08",
        "cutoff_utc": "2026-11-15T15:00:00Z",
    }
}
GENRE_OF = {"prd_0201": "novel", "prd_0202": "it", "prd_0203": "essay", "prd_0204": None}
# (구매자, 시각, 상품들, 결제 성공)
ORDERS = [
    ("buy_100001", "2026-09-10T01:00:00Z", ["prd_0201", "prd_0202"], True),  # 한 주문에 둘
    ("buy_100002", "2026-08-20T01:00:00Z", ["prd_0201"], True),
    ("buy_100002", "2026-09-01T01:00:00Z", ["prd_0202"], True),  # 다른 주문으로 둘
    ("buy_100003", "2026-08-25T01:00:00Z", ["prd_0201", "prd_0204"], True),  # 장르 없는 줄
    ("buy_100003", "2026-11-01T01:00:00Z", ["prd_0201"], True),
    ("buy_100004", "2026-08-19T01:00:00Z", ["prd_0202"], True),
    ("buy_100004", "2026-08-22T01:00:00Z", ["prd_0203"], True),
    ("buy_100005", "2026-10-01T01:00:00Z", ["prd_0201", "prd_0203"], False),  # 결제 안 됨
    ("buy_0005", "2026-10-01T01:00:00Z", ["prd_0201", "prd_0203"], True),  # 사람 주문
    ("buy_100002", "2026-11-20T01:00:00Z", ["prd_0203"], True),  # 기간 뒤(매일 모드)
]


@pytest.fixture
def shop(tmp_path: Path) -> tuple[Path, Path]:
    db = Db(str(tmp_path / "shop.db"))
    seed(db)
    stamp = "2026-08-01T00:00:00Z"
    with db.tx() as conn:
        for pid, genre in GENRE_OF.items():
            conn.execute(
                "INSERT INTO products (product_id, seller_id, name, category_code, price, stock, "
                "active, tenant_id, created_at, updated_at, genre_code) "
                "VALUES (?, 'sel_0001', ?, 'book', 10000, 50, 1, ?, ?, ?, ?)",
                (pid, f"책 {pid}", TENANT_ID, stamp, stamp, genre),
            )
        for n in range(1, 6):
            conn.execute(
                "INSERT INTO buyers (buyer_id, tenant_id, created_at) VALUES (?, ?, ?)",
                (f"buy_10000{n}", TENANT_ID, stamp),
            )
        for i, (buyer, at, items, paid) in enumerate(ORDERS):
            oid = f"ord_9{i:06d}"
            conn.execute(
                "INSERT INTO orders (order_id, buyer_id, status, total_amount, tenant_id, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (oid, buyer, "paid" if paid else "pending_payment", 10000 * len(items),
                 TENANT_ID, at, at),
            )  # fmt: skip
            for pid in items:
                conn.execute(
                    "INSERT INTO order_items (order_id, product_id, seller_id, qty, unit_price, "
                    "tenant_id) VALUES (?, ?, 'sel_0001', 1, 10000, ?)",
                    (oid, pid, TENANT_ID),
                )
            conn.execute(
                "INSERT INTO payments (payment_id, order_id, amount, method_code, status, "
                "tenant_id, created_at) VALUES (?, ?, ?, 'card', ?, ?, ?)",
                (f"pay_9{i:06d}", oid, 10000 * len(items), "succeeded" if paid else "failed",
                 TENANT_ID, at),
            )  # fmt: skip
    db._conn.close()
    out = tmp_path / "out"
    out.mkdir()
    (out / "answer_key.json").write_text(json.dumps(KEY), encoding="utf-8")
    return tmp_path / "shop.db", out


def _build(path: Path, **kw) -> dict:
    conn = extra.open_readonly(str(path))
    try:
        return extra.build(conn, KEY, **kw)
    finally:
        conn.close()


def test_bundle_lift_and_support_by_hand(shop) -> None:
    path, _out = shop
    result = _build(path, min_buyers=1)
    b = result["bundles"]
    # 기간 안 결제한 가상 회원 4명: {novel,it} {novel,it} {novel} {it,essay}
    assert b["buyers"] == 4
    assert b["genre_buyers"] == {"it": 3, "novel": 3, "essay": 1}
    assert b["untagged_order_genres_skipped"] == 1
    pairs = {(r["a"], r["b"]): r for r in b["pairs"]}
    assert set(pairs) == {("it", "novel"), ("essay", "it")}  # 사람·미결제·기간 뒤 주문은 빠진다
    assert pairs[("it", "novel")]["buyers"] == 2
    assert pairs[("it", "novel")]["support"] == 0.5
    assert pairs[("it", "novel")]["lift"] == round(0.5 / (0.75 * 0.75), 4)  # 0.8889
    assert pairs[("essay", "it")]["lift"] == round(0.25 / (0.25 * 0.75), 4)  # 1.3333
    assert pairs[("essay", "it")]["confidence_a_to_b"] == 1.0
    assert [(r["a"], r["b"]) for r in b["top_by_lift"]] == [("essay", "it"), ("it", "novel")]
    assert [(r["a"], r["b"]) for r in b["top_by_support"]] == [("it", "novel"), ("essay", "it")]
    assert result["paid_orders"] == {"in_window": 7, "after_window": 1}
    strict = _build(path, min_buyers=2)["bundles"]["top_by_lift"]
    assert [(r["a"], r["b"]) for r in strict] == [("it", "novel")]


def test_repeat_churn_at_window_end_and_now(shop) -> None:
    path, _out = shop
    churn = _build(path)["repeat_churn"]
    end = churn["window_end"]
    # 2건 이상: 100002(마지막 9/1), 100003(11/1), 100004(8/22). 끝 전 60일(9/16) 안 주문이 없는 회원
    assert end["repeat_buyers"] == 3 and end["churned_repeat_buyers"] == 2
    assert end["churn_share"] == round(2 / 3, 4)
    assert end["buyers_with_paid_order"] == 4
    now = churn["now"]  # 기본 기준: 마지막 가상 주문(11/20)
    assert now["as_of_utc"] == "2026-11-20T01:00:00Z"
    assert now["repeat_buyers"] == 3 and now["churned_repeat_buyers"] == 1  # 100002가 돌아왔다
    later = _build(path, as_of=extra._parse("2027-02-01T00:00:00Z"))["repeat_churn"]["now"]
    assert later["churned_repeat_buyers"] == 3 and later["churn_share"] == 1.0


def test_main_writes_once_deterministically_and_leaves_the_db_alone(shop, capsys) -> None:
    path, out = shop
    before = path.read_bytes()
    assert extra.main(["--db", str(path), "--out", str(out), "--dry-run"]) == 0
    assert not (out / extra.OUT_FILE).exists()
    assert "lift" in capsys.readouterr().out
    assert extra.main(["--db", str(path), "--out", str(out)]) == 0
    first = (out / extra.OUT_FILE).read_text(encoding="utf-8")
    assert extra.main(["--db", str(path), "--out", str(out)]) == 0
    assert (out / extra.OUT_FILE).read_text(encoding="utf-8") == first
    assert path.read_bytes() == before
    conn = extra.open_readonly(str(path))
    with pytest.raises(Exception, match="readonly"):
        conn.execute("DELETE FROM orders")
    conn.close()
    assert extra.main(["--db", str(path), "--out", str(out / "none")]) == 2


def test_no_after_window_orders_means_no_now_section(shop) -> None:
    path, _out = shop
    assert "now" in _build(path)["repeat_churn"]  # 기간 뒤 가상 주문이 있다
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM payments WHERE created_at > '2026-11-16'")
    conn.commit()
    conn.close()
    assert "now" not in _build(path)["repeat_churn"]
