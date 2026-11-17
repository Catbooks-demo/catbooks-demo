"""가상 회원 하루 흐름(scripts/gen_synthetic_daily.py). 실제 shop-api 앱(HTTP, required)에 붙인다.

되채우기는 작은 회원 수로 임시 DB 파일에 넣고, 정답지·상태는 임시 폴더에 쓴다. 네트워크 없음.
"""

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api import synthetic_ids
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.genres import GENRES
from ax_shop_api.main import create_app
from ax_shop_api.seed import seed

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "gen_synthetic_daily.py"
_spec = importlib.util.spec_from_file_location("gen_synthetic_daily", SCRIPT)
assert _spec and _spec.loader
daily = importlib.util.module_from_spec(_spec)
sys.modules["gen_synthetic_daily"] = daily
_spec.loader.exec_module(daily)
gen = daily.gen

NOW = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)  # 되채우기를 넣은 때(한국 시간 11/17 12:00)
END = date(2026, 11, 15)
DAY = date(2026, 11, 17)
MEMBERS = 4000
ADMIN = {"Authorization": "Bearer mock:admin"}
KEY = "synthetic-daily:%"


def _kst(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=gen.KST).astimezone(UTC)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv("SHOP_BUYER_AUTH", "required")
    monkeypatch.setattr(daily, "MAX_LATE", timedelta(days=2))  # 하루를 한 번에 몰아 시험한다
    events.reset_mock_bus()


def _db(path: Path) -> Db:
    """시드 + 장르마다 책 6권·문구 3개(gen_synthetic 시험과 같은 모양)."""
    db = Db(str(path))
    seed(db)
    with db.tx() as conn:
        number = 100
        for genre in GENRES:
            stationery = genre in ("writing", "notebook", "deco")
            for i in range(3 if stationery else 6):
                number += 1
                conn.execute(
                    "INSERT INTO products (product_id, seller_id, name, category_code, price, "
                    "stock, active, tenant_id, created_at, updated_at, genre_code) "
                    "VALUES (?, 'sel_0001', ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                    (
                        f"prd_{number:04d}",
                        f"{'AI 업무 ' if genre == 'it' and i < 2 else ''}{genre} 상품 {i + 1}",
                        "stationery" if stationery else "book",
                        (5000 if stationery else 12000) + i * 1500,
                        40 + (number * 37) % 200,
                        TENANT_ID,
                        "2026-08-01T00:00:00Z",
                        "2026-08-01T00:00:00Z",
                        genre,
                    ),
                )
    return db


@pytest.fixture(scope="module")
def backfilled(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """되채우기를 한 번 넣은 DB 파일과 정답지(시험마다 복사해 쓴다)."""
    base = tmp_path_factory.mktemp("backfill")
    db = _db(base / "shop.db")
    config = gen.Config(members=MEMBERS, end=END, now=NOW)
    gen.generate(db, config, log=lambda _: None, out=base / "out")
    db._conn.close()
    return base


@pytest.fixture
def world(backfilled: Path, tmp_path: Path):
    import shutil

    shutil.copy(backfilled / "shop.db", tmp_path / "shop.db")
    shutil.copytree(backfilled / "out", tmp_path / "out")
    db = Db(str(tmp_path / "shop.db"))
    client = TestClient(create_app(db))
    model = daily.load_model(tmp_path / "out")
    return db, client, model, tmp_path / "out"


def _call(client: TestClient):
    def call(method, path, body=None):
        res = client.request(method, path, json=body, headers=ADMIN)
        return res.status_code, res.json()

    return call


def _runner(world, clock: list[datetime], **kw):
    _db_, client, model, out = world
    return daily.Daily(_call(client), model, out, clock=lambda: clock[0], log=lambda _: None, **kw)


def _daily_orders(db: Db) -> list[dict]:
    with db.tx() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM orders WHERE idempotency_key LIKE ? ORDER BY order_id", (KEY,)
            )
        ]


def _state(out: Path, day: date = DAY) -> dict:
    return json.loads((out / "daily_state" / f"{day}.json").read_text(encoding="utf-8"))


def _products(client: TestClient) -> list:
    return daily.to_products(client.get("/api/shop/products").json()["data"])


def test_plan_is_deterministic_for_seed_and_date(world) -> None:
    _db_, client, model, _out = world
    products = _products(client)
    first = daily.plan_day(model, DAY, products)
    assert first == daily.plan_day(model, DAY, products)
    assert first != daily.plan_day(model, DAY + timedelta(days=1), products)
    other = daily.Model(**{**model.__dict__, "seed": model.seed + 1})
    assert first != daily.plan_day(other, DAY, products)
    assert len(first) >= 3
    times = [s["at"] for s in first]
    assert times == sorted(times)
    for slot in first:
        assert synthetic_ids.is_synthetic(slot["buyer_id"])
        assert _kst(DAY, 0) <= daily._parse(slot["at"]) < _kst(DAY + timedelta(days=1), 0)
        assert slot["lines"] and all(qty in (1, 2) for _p, qty, _g in slot["lines"])
    small = {m.buyer_id for m in model.members if m.persona is gen.SMALL_GROUP}
    assert not small & {s["buyer_id"] for s in first}
    # 트렌드 곡선은 기간 뒤에도 이어지고(최고점 뒤 반감) 결국 평소로 돌아온다
    info = daily.day_info(model, DAY)
    assert info["trend_phase"] == "decay" and 1 < info["trend_multiple"] < gen.TREND_PEAK
    late = daily.day_info(model, DAY + timedelta(days=120))
    assert late["trend_phase"] == "baseline" and late["trend_multiple"] < 1.05


def test_tick_places_at_most_batch_via_the_api_and_outbox(world) -> None:
    db, _client, _model, out = world
    with db.tx() as conn:
        before = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
    clock = [_kst(DAY, 0, 1)]
    runner = _runner(world, clock, max_per_tick=2)
    runner.tick()  # 하루 계획을 세운다(자정 직후라 넣을 것이 거의 없다)
    assert (out / "daily_state" / f"{DAY}.json").is_file()
    clock[0] = _kst(DAY, 23, 59)
    first = len(_daily_orders(db))
    done = runner.tick()
    orders = _daily_orders(db)
    assert done["placed"] <= 2 and 0 < len(orders) - first <= 2
    with db.tx() as conn:
        events_ = [json.loads(r[0]) for r in conn.execute("SELECT payload FROM outbox_events")]
        after = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
    assert after > before  # 서버 코드가 쓴 outbox(가상 되채우기는 하나도 쓰지 않았다)
    created = {e["data"]["order_id"] for e in events_ if e["type"] == "order.created"}
    assert {o["order_id"] for o in orders} <= created
    paid = [o for o in orders if o["status"] == "paid"]
    succeeded = {e["data"]["order_id"] for e in events_ if e["type"] == "payment.succeeded"}
    assert {o["order_id"] for o in paid} <= succeeded


def test_restart_resumes_without_double_placing(world) -> None:
    db, _client, _model, out = world
    clock = [_kst(DAY, 0, 1)]
    _runner(world, clock, max_per_tick=2).tick()
    clock[0] = _kst(DAY, 23, 59)
    _runner(world, clock, max_per_tick=2).tick()
    placed = len(_daily_orders(db))
    # 주문을 보낸 뒤 상태를 적기 전에 꺼진 것처럼 되돌린다: 다시 보내도 서버가 처음 주문을 돌려준다
    state = _state(out)
    slot = next(s for s in state["slots"] if s.get("order_id"))
    slot["status"] = "placing"
    slot.pop("order_id")
    path = out / "daily_state" / f"{DAY}.json"
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    again = _runner(world, clock, max_per_tick=2)  # 새 프로세스
    again.tick()
    orders = _daily_orders(db)
    keys = [o["idempotency_key"] for o in orders]
    assert len(keys) == len(set(keys))
    state = _state(out)
    with_order = [s for s in state["slots"] if s.get("order_id")]
    assert len(orders) == len(with_order) <= placed + 2
    assert {s["order_id"] for s in with_order} == {o["order_id"] for o in orders}
    for _ in range(10):  # 남은 몫을 끝까지 넣어도 계획한 수를 넘지 않는다
        again.tick()
    assert len(_daily_orders(db)) <= len(state["slots"])
    with db.tx() as conn:
        pays = conn.execute(
            "SELECT order_id, COUNT(*) FROM payments WHERE order_id IN "
            "(SELECT order_id FROM orders WHERE idempotency_key LIKE ?) GROUP BY order_id",
            (KEY,),
        ).fetchall()
    assert all(n <= 2 for _oid, n in pays)  # 첫 결제(거절이면 한 번 더)뿐


def test_only_synthetic_members_buy(world) -> None:
    db, client, _model, _out = world
    res = client.post(
        "/api/shop/auth/signup",
        json={"email": "cat@example.com", "password": "correct-horse-1", "display_name": "고양이",
              "agree_terms": True, "agree_privacy": True},
    )  # fmt: skip
    real = res.json()["data"]["user"]["buyer_id"]
    clock = [_kst(DAY, 0, 1)]
    runner = _runner(world, clock, max_per_tick=50)
    runner.tick()
    clock[0] = _kst(DAY, 23, 59)
    runner.tick()
    orders = _daily_orders(db)
    assert orders
    assert all(synthetic_ids.is_synthetic(o["buyer_id"]) for o in orders)
    with db.tx() as conn:
        mine = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE buyer_id = ? OR buyer_id IN "
            "(SELECT buyer_id FROM buyers WHERE buyer_id GLOB 'buy_00[0-9][0-9]')",
            (real,),
        ).fetchone()[0]
    assert mine == 0


def test_stock_never_goes_negative_and_sold_out_lines_are_skipped(world) -> None:
    db, _client, _model, out = world
    clock = [_kst(DAY, 0, 1)]
    runner = _runner(world, clock, max_per_tick=50)
    runner.tick()  # 재고가 있을 때 계획을 세운다
    with db.tx() as conn:  # 그 뒤 거의 모든 상품이 품절됐다
        conn.execute("UPDATE products SET stock = CASE WHEN rowid % 5 = 0 THEN 1 ELSE 0 END")
    clock[0] = _kst(DAY, 23, 59)
    runner.tick()
    with db.tx() as conn:
        low = conn.execute("SELECT MIN(stock) FROM products").fetchone()[0]
    assert low >= 0
    slots = _state(out)["slots"]
    touched = [s for s in slots if s["status"] != "before_start"]
    assert any(s["status"] == "dropped" or s.get("skipped_lines") for s in touched)


def test_day_closes_with_one_answer_line_and_orders_get_delivered(world) -> None:
    db, _client, _model, out = world
    clock = [_kst(DAY, 0, 1)]
    runner = _runner(world, clock, max_per_tick=50)
    runner.tick()
    clock[0] = _kst(DAY, 23, 59)
    runner.tick()
    clock[0] = _kst(DAY + timedelta(days=1), 0, 30)
    runner.tick()
    runner.tick()
    lines = (out / "daily_answer_key.jsonl").read_text(encoding="utf-8").splitlines()
    rows = [json.loads(x) for x in lines]
    assert [r["date"] for r in rows] == [DAY.isoformat()]
    row = rows[0]
    assert row["planned"]["orders"] == len(_state(out)["slots"])
    assert row["trend"]["phase"] == "decay" and row["actual"]["placed"] == len(_daily_orders(db))
    assert sum(row["planned"]["units_by_genre"].values()) > 0
    # 엿새 뒤에는 결제된 가상 주문(되채우기의 미도착 주문 포함)이 발송·도착으로 적힌다
    clock[0] = _kst(DAY + timedelta(days=7), 12)
    for _ in range(5):
        runner.tick()
    with db.tx() as conn:
        left = conn.execute(
            f"SELECT COUNT(*) FROM orders WHERE status = 'paid' AND delivered_at IS NULL "  # noqa: S608
            f"AND {synthetic_ids.BUYER_WHERE} AND created_at < ?",
            (daily._stamp(_kst(DAY, 23, 59)),),
        ).fetchone()[0]
        bad = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE delivered_at < shipped_at"
        ).fetchone()[0]
    assert left == 0 and bad == 0


def test_refuses_without_backfill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(daily.DailyError):
        daily.load_model(tmp_path / "nothing")
    db = _db(tmp_path / "empty.db")
    client = TestClient(create_app(db))
    model = daily.Model(seed=1, member_seed=1, members=gen.make_members(
        10, gen.build_window(gen.Config(end=END, now=NOW), NOW), gen.Rng("1:members")
    ), norms={}, trend_start=END, cutoff=NOW)  # fmt: skip
    with pytest.raises(daily.DailyError):
        daily.check_members(_call(client), model)
    monkeypatch.delenv("SHOP_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("AX_AGENT_TOKENS", raising=False)
    assert daily.main(["--out", str(tmp_path), "--once"]) == 1
    monkeypatch.setenv("SHOP_ADMIN_TOKEN", "x")
    assert daily.main(["--out", str(tmp_path / "nothing"), "--once"]) == 2
