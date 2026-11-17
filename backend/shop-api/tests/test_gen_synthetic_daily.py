"""가상 회원 하루 흐름(scripts/gen_synthetic_daily.py). 실제 shop-api 앱(HTTP, required)에 붙인다.

되채우기는 작은 회원 수로 임시 DB 파일에 넣고, 정답지·상태는 임시 폴더에 쓴다. 네트워크 없음.
"""

import importlib.util
import json
import sys
from collections import Counter
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
ONE_STOCK = "SELECT stock FROM products WHERE product_id = 'prd_0105'"


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
        source, medium, _campaign = slot["src"]
        assert daily.SOURCE_MEDIUM[source] == medium
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
    # 출처별 수·몫(계획·실제)과 그날의 가정 비율
    assert sum(row["planned"]["by_source"].values()) == row["planned"]["orders"]
    assert sum(row["actual"]["by_source"].values()) == row["actual"]["placed"]
    assert abs(sum(row["source_mix"].values()) - 1) < 1e-3
    with db.tx() as conn:
        tagged = Counter(
            r[0]
            for r in conn.execute(
                "SELECT src_source FROM orders WHERE idempotency_key LIKE ?", (KEY,)
            )
        )
    assert dict(tagged) == row["actual"]["by_source"]
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


def _flaky(client: TestClient, bad: dict, status: int):
    """`bad["on"]`이면 쓰기(POST·PATCH)에 `status`로 답한다(토큰 문제·제한·서버 오류 흉내)."""
    good = _call(client)

    def call(method, path, body=None):
        if bad["on"] and method in ("POST", "PATCH"):
            return status, {"error": {"code": "X"}}
        return good(method, path, body)

    return call


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_retryable_errors_leave_work_pending(world, status: int) -> None:
    db, client, model, out = world
    bad = {"on": False}
    clock = [_kst(DAY, 0, 1)]
    runner = daily.Daily(
        _flaky(client, bad, status), model, out, clock=lambda: clock[0], log=lambda _: None,
        max_per_tick=50,
    )  # fmt: skip
    runner.tick()
    bad["on"] = True
    clock[0] = _kst(DAY, 23, 59)
    runner.tick()  # 쓰기가 모두 거부된다: 끝난 것으로 적지 않는다
    slots = _state(out)["slots"]
    live = [s for s in slots if s["status"] != "before_start"]
    assert live and not any(s["status"] in ("dropped", "done") for s in live)
    assert not _daily_orders(db)
    followups = json.loads((out / "daily_followups.json").read_text(encoding="utf-8"))
    assert not followups["done"]  # 발송·도착 표시도 다음 차례로
    bad["on"] = False
    for _ in range(5):
        runner.tick()
    slots = _state(out)["slots"]
    assert all(s["status"] in daily.TERMINAL for s in slots)
    assert len(_daily_orders(db)) == sum(1 for s in slots if s.get("order_id")) > 0
    with db.tx() as conn:
        marked = conn.execute(
            f"SELECT COUNT(*) FROM orders WHERE {synthetic_ids.BUYER_WHERE} "  # noqa: S608
            "AND delivered_at > ?",
            (daily._stamp(model.cutoff),),
        ).fetchone()[0]
    assert marked > 0  # 되채우기의 미도착 주문이 다음 차례에 도착으로 적혔다


def test_low_stock_is_restocked_after_the_lead_time(world) -> None:
    db, _client, _model, _out = world
    with db.tx() as conn:
        conn.execute("UPDATE products SET stock = 3 WHERE product_id = 'prd_0105'")
    clock = [_kst(DAY, 0, 1)]
    runner = _runner(world, clock, max_per_tick=1)
    runner.tick()
    with db.tx() as conn:
        stock = conn.execute(ONE_STOCK).fetchone()[0]
    assert stock <= 3  # 기준 아래로 둔다(품절이 잠깐은 보인다)
    clock[0] = _kst(DAY + timedelta(days=1), 0, 30)
    runner.tick()
    with db.tx() as conn:
        stock = conn.execute(ONE_STOCK).fetchone()[0]
    assert stock == daily.RESTOCK_TO


def test_daily_picks_skip_sold_out_and_keep_a_stable_rank(world) -> None:
    _db_, client, model, _out = world
    products = _products(client)
    assert model.ranks  # 되채우기 순위를 이어 쓴다
    novel = [p for p in products if p.genre == "novel"]
    order = [p.product_id for p in daily.DailyShelf.order("novel", novel, model.seed, model.ranks)]
    assert order == model.ranks["novel"]  # 되채우기 때 있던 상품은 그 순서 그대로
    new = gen.Product("prd_0999", "sel_0001", "새 소설", "book", 15000, 50, "novel")
    grown = daily.DailyShelf.order("novel", [*novel, new], model.seed, model.ranks)
    assert [p.product_id for p in grown if p.product_id != "prd_0999"] == order
    shrunk = daily.DailyShelf.order("novel", novel[1:], model.seed, model.ranks)
    assert [p.product_id for p in shrunk] == [pid for pid in order if pid != novel[0].product_id]
    # 같은 번호의 새 상품은 순위 목록이 없어도 늘 같은 자리
    again = daily.DailyShelf.order("novel", [*novel, new], model.seed, {})
    assert again == daily.DailyShelf.order("novel", [new, *novel], model.seed, {})
    stock = {p.product_id: (0 if p.genre == "it" and p.ai_title else p.stock) for p in products}
    shelf = daily.DailyShelf(products, model.seed, model.ranks, stock)
    rng = gen.Rng("t")
    picks = {shelf.pick("it", rng, 1.0).product_id for _ in range(200)}
    assert not {p.product_id for p in products if p.ai_title} & picks  # 품절 트렌드 책은 안 고른다


def test_crx_guard_changes_or_skips_claims_on_unplanted_books(world) -> None:
    _db_, client, model, out = world
    runner = _runner(world, [NOW])
    now = _kst(DAY, 12)
    order = {"items": [{"order_item_id": "oit_0001", "product_id": "prd_0103", "qty": 1}]}
    claim = {"items": [{"order_item_id": "oit_0001", "qty": 1}], "reason": "defect"}
    sold = Counter({"prd_0103": 10})
    data = {"done": {}, "claims": []}
    assert runner._crx_reason(claim, order, data, sold, now) == "defect"
    data["claims"] = [["prd_0103", daily._stamp(now - timedelta(days=2)), 1, "defect"]]
    # 두 번째 클레임이면 (판매 10, 수량 20%, 2건)으로 걸린다: 변심으로 바꿔도 걸리니 내지 않는다
    assert runner._crx_reason(claim, order, data, sold, now) is None
    data["claims"] = [["prd_0103", daily._stamp(now - timedelta(days=40)), 1, "defect"]]
    assert runner._crx_reason(claim, order, data, sold, now) == "defect"  # 30일 밖은 세지 않는다
    big = Counter({"prd_0103": 100})
    data["claims"] = [["prd_0103", daily._stamp(now - timedelta(days=1)), 1, "damaged"]] * 2
    assert runner._crx_reason(claim, order, data, big, now) == "change_of_mind"  # 불량 3건 막기
    planted = next(iter(model.planted))
    order["items"][0]["product_id"] = planted
    assert runner._crx_reason(claim, order, data, sold, now) == "defect"  # 심은 책은 그대로
    # 클레임 기록은 처음에 되채우기 클레임으로 채운다
    loaded = runner._load_followups(model.cutoff + timedelta(days=1))
    assert loaded["claims"] and len(loaded["claims"]) <= len(model.backfill_claims)


def test_first_day_after_backfill_spreads_the_missed_morning(world) -> None:
    _db_, _client, model, out = world
    first = model.cutoff.astimezone(gen.KST).date()  # 되채우기 끝 다음 날(11/16)
    clock = [_kst(first, 15)]
    runner = _runner(world, clock, max_per_tick=50)
    runner.tick()
    slots = _state(out, first)["slots"]
    assert not any(s["status"] == "before_start" for s in slots)
    deferred = [s for s in slots if s.get("deferred")]
    assert deferred and all(daily._parse(s["next_at"]) > clock[0] for s in deferred)
    assert max(daily._parse(s["next_at"]) for s in deferred) <= _kst(first + timedelta(days=1), 0)


def test_corrupt_state_is_moved_aside_and_rebuilt(world) -> None:
    _db_, _client, _model, out = world
    clock = [_kst(DAY, 0, 1)]
    _runner(world, clock).tick()
    path = out / "daily_state" / f"{DAY}.json"
    path.write_text('{"date": "2026-11-', encoding="utf-8")
    (out / "daily_followups.json").write_text("{", encoding="utf-8")
    _runner(world, clock).tick()
    assert json.loads(path.read_text(encoding="utf-8"))["date"] == DAY.isoformat()
    assert list((out / "daily_state").glob(f"{DAY}.json.corrupt-*"))
    assert list(out.glob("daily_followups.json.corrupt-*"))


def test_second_process_is_refused_by_the_lock(tmp_path: Path) -> None:
    lock = daily.InstanceLock(tmp_path / "daily.lock")
    with pytest.raises(daily.DailyError):
        daily.InstanceLock(tmp_path / "daily.lock")
    lock.release()
    daily.InstanceLock(tmp_path / "daily.lock").release()


def test_check_members_tolerates_a_deleted_member(world) -> None:
    _db_, client, model, _out = world
    good = _call(client)
    gone = model.members[0].buyer_id

    def call(method, path, body=None):
        if path.endswith(f"/users/{gone}"):
            return 404, {"error": {"code": "BUYER_NOT_FOUND"}}
        return good(method, path, body)

    daily.check_members(call, model)  # 한 명이 지워져도 시작한다


def test_answer_lines_skip_torn_lines_and_never_repeat_a_day(world) -> None:
    _db_, _client, _model, out = world
    (out / "daily_answer_key.jsonl").write_text('{"date": "2026-11-1', encoding="utf-8")
    clock = [_kst(DAY, 0, 1)]
    runner = _runner(world, clock, max_per_tick=50)
    runner.tick()
    clock[0] = _kst(DAY, 23, 59)
    runner.tick()
    clock[0] = _kst(DAY + timedelta(days=1), 0, 30)
    runner.tick()
    state = _state(out)
    state["finalized"] = False  # 적은 뒤 상태를 저장하기 전에 꺼진 것처럼
    (out / "daily_state" / f"{DAY}.json").write_text(json.dumps(state), encoding="utf-8")
    runner.tick()
    rows = []
    for raw in (out / "daily_answer_key.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(raw))
        except ValueError:
            continue
    assert [r["date"] for r in rows] == [DAY.isoformat()]


@pytest.mark.parametrize("deliver_real", [True, False])
def test_human_orders_get_shop_traffic_delivery_marks(world, deliver_real: bool) -> None:
    db, client, model, out = world
    res = client.post(
        "/api/shop/orders",
        json={"buyer_id": "buy_0005", "items": [{"product_id": "prd_0101", "qty": 1}]},
        headers=ADMIN,
    )
    oid = res.json()["data"]["order_id"]
    paid = client.post(f"/api/shop/orders/{oid}/payments", json={}, headers=ADMIN).json()["data"]
    clock = [daily._parse(paid["created_at"]) + timedelta(days=4)]
    runner = daily.Daily(
        _call(client), model, out, clock=lambda: clock[0], log=lambda _: None,
        deliver_real=deliver_real,
    )  # fmt: skip
    runner.tick()
    order = client.get(f"/api/shop/orders/{oid}", headers=ADMIN).json()["data"]
    if not deliver_real:
        assert order["delivered_at"] is None
        return
    paid_at = daily._parse(paid["payments"][0]["created_at"])
    shipped, arrived = daily.real_delivery_plan(oid, paid_at)
    assert order["shipped_at"] == daily._stamp(shipped)
    assert order["delivered_at"] == daily._stamp(arrived)
    with db.tx() as conn:  # 사람 주문에는 클레임·후기·문의를 내지 않는다
        assert not conn.execute(
            "SELECT 1 FROM claim_inbox WHERE order_id = ? UNION SELECT 1 FROM reviews "
            "WHERE order_id = ?",
            (oid, oid),
        ).fetchone()


def _shares(model, info: dict, n: int = 20000) -> tuple[dict[str, float], Counter]:
    picks = [daily.pick_source(model.seed, DAY.isoformat(), k, info) for k in range(n)]
    sources = Counter(p[0] for p in picks)
    return {k: v / n for k, v in sources.items()}, Counter(p[2] for p in picks)


def test_source_shares_follow_the_mix_and_youtube_rises_with_the_trend(world) -> None:
    _db_, _client, model, _out = world
    base = daily.day_info(model, DAY + timedelta(days=120))
    assert base["trend_phase"] == "baseline"
    shares, campaigns = _shares(model, base)
    for source, _medium, share in daily.SOURCE_MIX:
        assert abs(shares[source] - share) < 0.015, source
    assert campaigns == Counter({None: 20000})  # 평소에는 캠페인 표시가 없다
    peak = daily.day_info(model, model.trend_start + timedelta(days=21))
    assert peak["trend_phase"] == "peak"
    hot, campaigns = _shares(model, peak)
    expected = 0.15 * gen.TREND_PEAK / (0.85 + 0.15 * gen.TREND_PEAK)
    assert abs(hot["youtube"] - expected) < 0.015
    assert hot["youtube"] > shares["youtube"] + 0.2
    # 나머지는 비율을 지킨 채 줄어든다
    assert abs(hot["direct"] / hot["naver"] - 0.40 / 0.25) < 0.15
    tagged = campaigns[daily.TREND_CAMPAIGN]
    assert tagged == round((hot["youtube"] + hot["instagram"]) * 20000)
    # (시드, 날짜, 순번)이 같으면 같은 출처
    assert daily.pick_source(model.seed, "2026-11-17", 7, peak) == daily.pick_source(
        model.seed, "2026-11-17", 7, peak
    )


def test_orders_carry_the_planned_source_and_a_replay_sends_the_same(world) -> None:
    db, client, model, out = world
    sent: list[dict] = []
    good = _call(client)

    def call(method, path, body=None):
        if method == "POST" and path == "/api/shop/orders":
            sent.append(body)
        return good(method, path, body)

    clock = [_kst(DAY, 0, 1)]
    runner = daily.Daily(
        call, model, out, clock=lambda: clock[0], log=lambda _: None, max_per_tick=50
    )
    runner.tick()
    clock[0] = _kst(DAY, 23, 59)
    runner.tick()
    state = _state(out)
    rows = {o["order_id"]: o for o in _daily_orders(db)}
    placed = [s for s in state["slots"] if s.get("order_id")]
    assert placed
    for slot in placed:
        row = rows[slot["order_id"]]
        assert [row["src_source"], row["src_medium"], row["src_campaign"]] == slot["src"]
    # 보낸 뒤 적기 전에 꺼진 것처럼 되돌리면 같은 출처로 다시 보낸다(멱등 키로 같은 주문)
    slot = placed[0]
    slot["status"] = "placing"
    oid = slot.pop("order_id")
    (out / "daily_state" / f"{DAY}.json").write_text(json.dumps(state), encoding="utf-8")
    sent.clear()
    again = daily.Daily(call, model, out, clock=lambda: clock[0], log=lambda _: None)
    again.tick()
    replay = [b for b in sent if b["idempotency_key"].endswith(f":{slot['k']}")]
    assert replay
    assert [replay[0][k] for k in ("src_source", "src_medium", "src_campaign")] == slot["src"]
    assert _state(out)["slots"][slot["k"]]["order_id"] == oid


def test_old_plans_without_a_source_get_one_before_placing(world) -> None:
    db, _client, model, out = world
    clock = [_kst(DAY, 0, 1)]
    runner = _runner(world, clock, max_per_tick=50)
    runner.tick()
    path = out / "daily_state" / f"{DAY}.json"
    state = _state(out)
    for slot in state["slots"]:  # 출처를 붙이기 전 버전이 세운 계획
        slot.pop("src")
    path.write_text(json.dumps(state), encoding="utf-8")
    clock[0] = _kst(DAY, 23, 59)
    runner.tick()
    rows = {o["order_id"]: o for o in _daily_orders(db)}
    placed = [s for s in _state(out)["slots"] if s.get("order_id")]
    assert placed
    for slot in placed:
        expected = daily.pick_source(model.seed, DAY.isoformat(), slot["k"], state["info"])
        assert slot["src"] == expected
        row = rows[slot["order_id"]]
        assert [row["src_source"], row["src_medium"], row["src_campaign"]] == expected
