"""캣북스 가상 회원의 하루 흐름 넣기 (되채우기 `gen_synthetic.py` 뒤에 계속 도는 프로세스).

되채우기가 넣은 가상 회원(`buy_100001`~)이 오늘도 산다. 하루 계획은 되채우기와 같은 모형(페르소나·
요일·월급날·추석 주·AI 도서 트렌드 곡선을 앞으로 늘린 것)으로 한국 시간 하루마다 한 번 세우고,
그 계획을 하루 동안 조금씩(기본 10분마다 그때까지 된 몫만) 판매 서버 HTTP API로 넣는다.

    cd backend
    SHOP_ADMIN_TOKEN=... uv run python scripts/gen_synthetic_daily.py [--interval-s 600]
        [--seed 20261115] [--out ~/catbooks-synthetic-out] [--url http://127.0.0.1:8010] [--once]
    # 같은 것: uv run python scripts/gen_synthetic.py --daily ...

- 사람 주문과 같은 길: 주문·결제·취소·클레임·문의·후기를 공개 API(`POST /api/shop/orders` 등)로
  넣는다. 그래서 outbox 이벤트(`order.created`·`payment.*`·`order.cancelled`)는 서버의 기존 코드가
  같은 트랜잭션에 쓰고 서버의 발송기(요청마다 `flush`, 주기 루프의 `drain`)가 AXcellent로 보낸다.
  결제는 서버의 가짜 결제사·장애 주입·결제 감시를 그대로 탄다(장애를 켜면 가상 결제도 실패하고
  `shop.degraded`가 난다). DB 파일을 직접 열지 않는다.
- 본인 확인: `admin` 토큰(`SHOP_ADMIN_TOKEN`, 없으면 `AX_AGENT_TOKENS`의 `admin` 값)으로
  `buyer_id`를 대신한다. `SHOP_BUYER_AUTH=required`에서도 된다. 비밀번호를 쓰지 않는다.
- 발송·도착: 결제된 가상 주문(되채우기의 미도착 주문 포함)을 결제 12~30시간 뒤 발송, 그 뒤
  20~44시간(늦은 배송은 60~120시간) 뒤 도착으로 관리 API에 적는다(시각이 지난 것만).
- 그 뒤의 일: 매일 모드가 도착시킨 주문에 되채우기와 같은 비율로 클레임·후기를, 매일 모드가 넣은
  주문에 문의를 낸다. 결제 장애로 실패한 회원 일부는 결제 실패 문의를 남긴다(옛 `shop_traffic`처럼).
- 다시 켜도 안전: 날마다 계획·진행을 `--out/daily_state/<날짜>.json`에 적는다. 주문은 날짜·순번으로
  만든 멱등 키를 보내 다시 보내도 서버가 처음 주문을 돌려준다(두 번 넣지 않는다).
- 정답지: 하루가 끝나면 `--out/daily_answer_key.jsonl`에 그날 한 줄(계획한 장르·페르소나 합계,
  트렌드 단계·배수, 실제로 넣은 건수)을 더한다. 로컬 전용이고 저장소·DB에 넣지 않는다.
- 되채우기 정답지(`--out/answer_key.json`)와 가상 회원이 없으면 멈춘다(회원을 같은 시드로 다시
  만들어 DB의 회원과 맞춰 본다).
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_synthetic as gen  # noqa: E402

from ax_shop_api import synthetic_ids  # noqa: E402

KST = gen.KST
DEFAULT_URL = "http://127.0.0.1:8010"
DEFAULT_INTERVAL_S = 600.0
DEFAULT_MAX_PER_TICK = 10  # 한 번에 새로 넣는 주문 상한(오래 꺼졌다 켜져도 몰아 넣지 않는다)
MAX_LATE = timedelta(hours=6)  # 이보다 늦은 계획 주문은 넣지 않고 놓친 것으로 센다
HORIZON_DAYS = 21  # 발송·도착·클레임·후기·문의를 보는 주문 기간(접수일 기준)
FOLLOWUPS_PER_TICK = 200  # 한 번에 하는 발송·도착·클레임·후기·문의 상한
FAILED_PAY_INQUIRY = 0.3  # 결제 장애로 실패한 회원이 문의를 남기는 몫(옛 shop_traffic과 같다)
STATE_DIR = "daily_state"
FOLLOWUPS_FILE = "daily_followups.json"
ANSWER_LINES = "daily_answer_key.jsonl"
ORDERS_PAGE = 1000
IDEMPOTENCY_PREFIX = "synthetic-daily"
# 결제가 실패한 회원이 남기는 문의(옛 shop_traffic의 예시 문장)
FAILED_PAYMENT_INQUIRIES = (
    ("결제가 안 돼요", "결제 버튼을 눌렀는데 계속 오류가 나요. 카드 결제가 안 되는 건가요?"),
    ("결제 오류 문의", "결제하다가 오류가 났는데 돈이 빠져나간 건 아니죠? 확인 부탁드려요."),
    ("주문이 결제 실패로 나와요", "방금 주문했는데 결제 실패라고 떠요. 다시 해도 똑같아요."),
    ("선물용 책 결제", "내일까지 선물해야 하는데 결제가 계속 실패해요. 언제쯤 되나요?"),
    ("카드 결제 실패", "다른 카드로도 해 봤는데 둘 다 결제 오류가 나요. 사이트 문제인가요?"),
)
TERMINAL = frozenset({"done", "dropped", "missed", "before_start", "unfinished"})

Call = Callable[..., tuple[int, dict]]


class DailyError(Exception):
    """매일 모드를 시작할 수 없다(되채우기 없음·토큰 없음 등)."""


# --- HTTP -----------------------------------------------------------------------------------
def admin_token() -> str:
    """`SHOP_ADMIN_TOKEN`, 없으면 `AX_AGENT_TOKENS`의 `admin` 값. 값은 로그에 남기지 않는다."""
    token = os.getenv("SHOP_ADMIN_TOKEN", "").strip()
    if token:
        return token
    for item in os.getenv("AX_AGENT_TOKENS", "").split(","):
        name, sep, value = item.strip().partition("=")
        if sep and name.strip() == "admin" and value.strip():
            return value.strip()
    return ""


def http_caller(base: str, token: str, timeout: float = 15.0) -> Call:
    base = base.rstrip("/")

    def call(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        req = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:  # noqa: S310 - 설정한 주소
                return res.status, json.loads(res.read() or b"{}")
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read() or b"{}")
            except ValueError:
                return error.code, {}

    return call


def _code(body: dict) -> str | None:
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(gen.STAMP)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


# --- 모형(되채우기와 같은 회원) ------------------------------------------------------------
@dataclass
class Model:
    seed: int  # 매일 계획의 시드(기본: 되채우기 시드)
    member_seed: int
    members: list[gen.Member]
    norms: dict[str, float]
    trend_start: date
    cutoff: datetime  # 되채우기가 만든 기록의 끝. 이 뒤의 일만 매일 모드가 만든다


def load_model(out: Path, seed: int | None = None) -> Model:
    """되채우기 정답지의 설정으로 회원을 같은 시드로 다시 만든다(DB를 읽지 않는다)."""
    path = out / "answer_key.json"
    if not path.is_file():
        raise DailyError(
            f"되채우기 정답지가 없다({path}). gen_synthetic.py를 먼저 돌리거나 --out을 맞춘다"
        )
    key = json.loads(path.read_text(encoding="utf-8"))
    cfg = key["config"]
    made = _parse(key["generated_at"])
    config = gen.Config(
        members=int(cfg["members"]),
        seed=int(cfg["seed"]),
        end=date.fromisoformat(cfg["window_end"]),
        trend_start=date.fromisoformat(cfg["trend_start"]),
        now=made,
    )
    window = gen.build_window(config, made)
    members = gen.make_members(config.members, window, gen.Rng(f"{config.seed}:members"))
    norms = gen.calibrate_trend(members)
    return Model(
        seed=config.seed if seed is None else seed,
        member_seed=config.seed,
        members=members,
        norms=norms,
        trend_start=window.trend_start,
        cutoff=window.cutoff,
    )


def check_members(call: Call, model: Model) -> None:
    """DB에 가상 회원이 있고 다시 만든 회원과 같은지 몇 명만 맞춰 본다. 다르면 멈춘다."""
    members = model.members
    picks = sorted({0, len(members) // 3, len(members) // 2, len(members) - 1})
    for index in picks:
        member = members[index]
        status, body = call("GET", f"/api/shop/users/{member.buyer_id}")
        if status == 404:
            raise DailyError(
                f"가상 회원 {member.buyer_id}가 판매 서버에 없다. "
                "되채우기(gen_synthetic.py)를 먼저 넣는다"
            )
        if status != 200:
            raise DailyError(f"가상 회원을 확인하지 못했다(HTTP {status} {_code(body)})")
        user = body.get("data") or {}
        if (user.get("age_band"), user.get("gender")) != (member.age_band, member.gender):
            raise DailyError(
                f"{member.buyer_id}의 연령대·성별이 정답지로 다시 만든 회원과 다르다. "
                "--out이 이 DB의 되채우기 정답지인지 확인한다"
            )


# --- 하루 계획 -------------------------------------------------------------------------------
def trend_phase(days_since: float) -> str:
    """트렌드 곡선(`gen.trend_multiple`)의 단계."""
    if days_since < 0:
        return "before"
    weeks = days_since / 7
    if weeks < 2.6:
        return "rising"
    if weeks < 4:
        return "peak"
    return "decay" if gen.trend_multiple(days_since) - 1 >= 0.05 else "baseline"


def day_info(model: Model, day: date) -> dict[str, Any]:
    """그날의 효과(되채우기 `build_window`와 같은 계산을 기간 밖 날짜로 늘린 것)."""
    factor = gen.day_factor(day, gen.chuseok_week(day, day))
    since = (day - model.trend_start).days + 0.5
    multiple = gen.trend_multiple(since)
    return {
        "factor": factor,
        "weekend_factor": gen.weekend_factor(day, factor),
        "trend": multiple - 1,
        "trend_multiple": round(multiple, 4),
        "trend_days_since_start": since,
        "trend_phase": trend_phase(since),
    }


def to_products(rows: list[dict]) -> list[gen.Product]:
    return [
        gen.Product(
            r["product_id"],
            r["seller_id"],
            r["name"],
            r["category_code"],
            int(r["price"]),
            int(r["stock"]),
            r.get("genre_code"),
            bool(gen.AI_TITLE.search(r.get("name") or "")),
        )
        for r in sorted(rows, key=lambda r: r["product_id"])
        if r.get("active", True)
    ]


def _payment_plan(rng: gen.Rng) -> dict[str, Any]:
    """결제 전 취소·첫 결제 거절·거절 뒤(다시 결제·취소·그대로) — 되채우기와 같은 비율."""
    if rng.random() < gen.CANCEL_BEFORE_PAY:
        return {"pay": "cancel", "after_s": round(rng.uniform(120, 5400)), "method": "card"}
    decline = rng.random() < gen.PAY_DECLINE
    method = rng.pick(gen.METHODS)
    if not decline:
        return {"pay": "ok", "after_s": 0, "method": method}
    roll = rng.random()
    if roll < gen.DECLINE_RETRY_OK:
        return {"pay": "decline_retry", "after_s": round(rng.uniform(60, 900)), "method": method}
    if roll < gen.DECLINE_RETRY_OK + gen.DECLINE_CANCEL:
        return {"pay": "decline_cancel", "after_s": round(rng.uniform(120, 3600)), "method": method}
    return {"pay": "decline_fail", "after_s": 0, "method": method}


def plan_day(model: Model, day: date, products: list[gen.Product]) -> list[dict[str, Any]]:
    """그날 회원마다 포아송으로 주문 수를 뽑고 시각·장바구니·결제 계획을 정한다.

    같은 (시드, 날짜, 상품 목록)이면 같은 계획이다. 상품·재고는 계획을 세울 때의 목록으로 고른다.
    """
    info = day_info(model, day)
    rng = gen.Rng(f"{model.seed}:daily:{day.isoformat()}")
    shelf = gen.Shelf(products, gen.Rng(f"{model.seed}:shelf"))
    hour_cache: dict[tuple[str, float], list[float]] = {}
    times: list[tuple[datetime, int]] = []
    for m in model.members:
        if m.persona is gen.SMALL_GROUP:  # 작은 묶음(숨기기 확인용)은 되채우기의 한 건만 둔다
            continue
        f = info["weekend_factor"] if m.persona.weekend else info["factor"]
        lam = m.yearly / 365 * (f + f * info["trend"] * m.trend_coef)
        for _ in range(rng.poisson(lam)):
            key = (m.persona.hours, round(m.hour_strength, 2))
            hours = hour_cache.get(key) or hour_cache.setdefault(key, gen._hour_weights(*key))
            hour = min(23, bisect.bisect_right(hours, rng.random() * hours[-1]))
            moment = datetime(
                day.year, day.month, day.day, hour, rng.randrange(60), rng.randrange(60), tzinfo=KST
            ).astimezone(UTC)
            times.append((moment, m.index))
    times.sort()
    slots = []
    for k, (moment, index) in enumerate(times):
        member = model.members[index]
        srng = gen.Rng(f"{model.seed}:daily:{day.isoformat()}:{k}")
        lines = gen.basket(member, info["trend"], model.norms, shelf, srng)
        slots.append(
            {
                "k": k,
                "at": _stamp(moment),
                "buyer_id": member.buyer_id,
                "persona": member.persona.code,
                "lines": [[p.product_id, qty, p.genre] for p, qty in lines],
                **_payment_plan(srng),
                "inquiry_on_fail": srng.random() < FAILED_PAY_INQUIRY,
                "status": "planned",
            }
        )
    return slots


def plan_summary(slots: list[dict[str, Any]]) -> dict[str, Any]:
    units: Counter[str] = Counter()
    for s in slots:
        for _pid, qty, genre in s["lines"]:
            units[genre or "unknown"] += qty
    return {
        "orders": len(slots),
        "by_persona": dict(Counter(s["persona"] for s in slots).most_common()),
        "units_by_genre": dict(units.most_common()),
        "cancel_before_pay": sum(1 for s in slots if s["pay"] == "cancel"),
        "first_payment_declined": sum(1 for s in slots if s["pay"].startswith("decline")),
    }


# --- 상태 파일 --------------------------------------------------------------------------------
def _write_json(path: Path, data: Any) -> None:
    """다른 이름에 쓴 뒤 바꿔 끼운다(쓰다 꺼져도 앞 내용이 남는다)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


# --- 한 번 돌기 -------------------------------------------------------------------------------
class Daily:
    def __init__(
        self,
        call: Call,
        model: Model,
        out: Path,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        max_per_tick: int = DEFAULT_MAX_PER_TICK,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        log: Callable[[str], None] = print,
    ) -> None:
        self.call = call
        self.model = model
        self.out = out
        self.interval = timedelta(seconds=interval_s)
        self.max_per_tick = max_per_tick
        self.clock = clock
        self.log = log
        self.state_dir = out / STATE_DIR
        self._stock: dict[str, int] = {}
        self._products: list[gen.Product] = []

    # 상태 ------------------------------------------------------------------
    def _day_path(self, day: date) -> Path:
        return self.state_dir / f"{day.isoformat()}.json"

    def _load_day(self, day: date) -> dict[str, Any] | None:
        return _read_json(self._day_path(day), None)

    def _save_day(self, state: dict[str, Any]) -> None:
        _write_json(self._day_path(date.fromisoformat(state["date"])), state)

    def _open_days(self, today: date) -> list[dict[str, Any]]:
        """마감 안 한 날(오늘과 그 전 사흘 안). 오래된 상태 파일은 읽지 않는다."""
        days = []
        for back in range(3, -1, -1):
            state = self._load_day(today - timedelta(days=back))
            if state is None:
                continue
            if not state.get("finalized"):
                days.append(state)
        return days

    def _new_day(self, day: date, now: datetime) -> dict[str, Any]:
        slots = plan_day(self.model, day, self._products)
        # 하루 중간에 처음 켰으면 이미 지난 시각의 계획은 넣지 않는다(몰아 넣지 않게)
        before = now - self.interval
        for s in slots:
            if _parse(s["at"]) < before:
                s["status"] = "before_start"
        info = day_info(self.model, day)
        state = {
            "date": day.isoformat(),
            "seed": self.model.seed,
            "planned_at": _stamp(now),
            "info": info,
            "planned": plan_summary(slots),
            "slots": slots,
            "followups": {},
            "finalized": False,
        }
        self._save_day(state)
        started = sum(1 for s in slots if s["status"] == "planned")
        self.log(
            f"{day} 계획: 주문 {len(slots)}건(남은 {started}건), 트렌드 "
            f"{info['trend_phase']} ×{info['trend_multiple']}"
        )
        return state

    # 상품 ------------------------------------------------------------------
    def _refresh_products(self) -> None:
        status, body = self.call("GET", "/api/shop/products")
        if status != 200:
            raise ConnectionError(f"상품 목록을 읽지 못했다(HTTP {status})")
        self._products = to_products(body.get("data") or [])
        self._stock = {p.product_id: p.stock for p in self._products}

    # 한 번 ----------------------------------------------------------------
    def tick(self) -> dict[str, int]:
        """그때까지 된 계획 주문을 넣고(최대 `max_per_tick`건), 발송·도착·그 뒤의 일을 한다."""
        now = self.clock()
        self._refresh_products()
        if not self._products:
            self.log("판매 중인 상품이 없어 이번에는 넣지 않는다")
            return {"placed": 0}
        today = now.astimezone(KST).date()
        open_days = self._open_days(today)
        current = next((s for s in open_days if s["date"] == today.isoformat()), None)
        if current is None and self._load_day(today) is None:
            current = self._new_day(today, now)
            open_days.append(current)
        budget = {"placed": 0}
        for state in open_days:
            self._run_day(state, now, budget)
        return {**budget, **self._followups(now, current)}

    def _run_day(self, state: dict[str, Any], now: datetime, budget: dict[str, int]) -> None:
        day = date.fromisoformat(state["date"])
        for slot in state["slots"]:
            if slot["status"] in TERMINAL:
                continue
            due = _parse(slot.get("next_at") or slot["at"])
            if due > now:
                continue
            if slot["status"] == "planned":
                if now - _parse(slot["at"]) > MAX_LATE:
                    slot["status"] = "missed"
                    self._save_day(state)
                    continue
                if budget["placed"] >= self.max_per_tick:
                    continue
                budget["placed"] += 1
            try:
                self._advance(state, slot, now)
            except (ConnectionError, OSError, urllib.error.URLError) as error:
                self.log(f"{slot['buyer_id']} 주문 처리 중 연결 오류(다음에 이어서): {error}")
                self._save_day(state)
                return
            self._save_day(state)
        day_end = gen._kst_midnight(day + timedelta(days=1))
        if now >= day_end:
            stuck = [s for s in state["slots"] if s["status"] not in TERMINAL]
            if stuck and now < day_end + timedelta(days=1):
                return  # 거절 뒤 다시 결제 등 남은 일을 기다린다
            for s in stuck:
                s["status"] = "unfinished"
            self._finalize(state, now)

    # 주문 한 건 -------------------------------------------------------------
    def _lines_now(self, state: dict[str, Any], slot: dict[str, Any]) -> list[list[Any]]:
        """지금 재고로 담을 줄. 품절이면 같은 장르의 다른 상품으로 바꾸거나 뺀다(사람처럼)."""
        shelf = gen.Shelf(self._products, gen.Rng(f"{self.model.seed}:shelf"))
        swap = gen.Rng(f"{self.model.seed}:daily:{state['date']}:{slot['k']}:swap")
        hot = min(1.0, state["info"]["trend"] / (gen.TREND_PEAK - 1))
        chosen: list[list[Any]] = []
        taken = {pid for pid, _q, _g in slot["lines"]}
        skipped = replaced = 0
        for pid, qty, genre in slot["lines"]:
            if self._stock.get(pid, 0) >= qty:
                chosen.append([pid, qty, genre])
                continue
            other = None
            for _ in range(3 if genre else 0):
                pick = shelf.pick(genre, swap, hot)
                free = pick is not None and self._stock.get(pick.product_id, 0) >= qty
                if free and pick.product_id not in taken:
                    other = pick
                    break
            if other is None:
                skipped += 1
                continue
            replaced += 1
            taken.add(other.product_id)
            chosen.append([other.product_id, qty, other.genre])
        slot["skipped_lines"] = slot.get("skipped_lines", 0) + skipped
        slot["replaced_lines"] = slot.get("replaced_lines", 0) + replaced
        return chosen

    def _place(self, state: dict[str, Any], slot: dict[str, Any]) -> dict | None:
        """멱등 키로 주문을 넣는다. 다시 보내도 서버가 처음 주문을 돌려준다."""
        body = {
            "buyer_id": slot["buyer_id"],
            "items": [{"product_id": pid, "qty": qty} for pid, qty, _g in slot["attempt"]],
            "idempotency_key": f"{IDEMPOTENCY_PREFIX}:{state['date']}:{slot['k']}",
        }
        status, res = self.call("POST", "/api/shop/orders", body)
        if status in (200, 201):
            return res["data"]
        if status == 409 and _code(res) == "OUT_OF_STOCK":
            return None
        if status >= 500:
            raise ConnectionError(f"주문 HTTP {status}")
        self.log(f"{slot['buyer_id']} 주문 거부 HTTP {status} {_code(res)}")
        slot["status"], slot["error"] = "dropped", _code(res)
        return None

    def _advance(self, state: dict[str, Any], slot: dict[str, Any], now: datetime) -> None:
        if slot["status"] == "planned":
            lines = self._lines_now(state, slot)
            if not lines:
                slot["status"] = "dropped"
                return
            slot["attempt"], slot["status"] = lines, "placing"
            self._save_day(state)  # 보낸 줄을 먼저 적는다(다시 켜면 같은 줄로 다시 보낸다)
        if slot["status"] == "placing":
            order = self._place(state, slot)
            if order is None and slot["status"] == "placing":  # 그 사이 품절: 한 번 다시 고른다
                self._refresh_products()
                lines = self._lines_now(state, slot)
                if not lines:
                    slot["status"] = "dropped"
                    return
                slot["attempt"] = lines
                self._save_day(state)
                order = self._place(state, slot)
                if order is None:
                    if slot["status"] == "placing":
                        slot["status"] = "dropped"
                    return
            if order is None:
                return
            slot["order_id"], slot["status"] = order["order_id"], "open"
            for pid, qty, _g in slot["attempt"]:
                self._stock[pid] = self._stock.get(pid, 0) - qty
            self._save_day(state)
            self._pay_step(slot, order, now)
            return
        if slot["status"] == "open":
            status, res = self.call("GET", f"/api/shop/orders/{slot['order_id']}")
            if status != 200:
                raise ConnectionError(f"주문 조회 HTTP {status}")
            self._pay_step(slot, res["data"], now)

    def _pay_step(self, slot: dict[str, Any], order: dict, now: datetime) -> None:
        """서버의 주문 상태로 다음 결제 단계를 정한다(다시 켜도 결제를 두 번 하지 않는다)."""
        plan, status = slot["pay"], order["status"]
        payments = order.get("payments") or []
        created = _parse(order["created_at"])
        slot["result"] = status
        if status not in ("pending_payment", "payment_failed"):
            slot["status"] = "done"  # 결제됨·취소됨
            return
        if any(p["status"] == "timeout" for p in payments):
            slot["status"] = "done"  # 시간 초과: 서버가 다시 확인해 정한다
            return
        if plan == "cancel":
            due = created + timedelta(seconds=slot["after_s"])
            if now < due:
                slot["next_at"] = _stamp(due)
                return
            self._cancel(slot)
            return
        if not payments:
            simulate = "failure" if plan.startswith("decline") else None
            paid = self._pay(slot, simulate)
            if paid is None:
                return
            if plan in ("decline_retry", "decline_cancel"):
                slot["next_at"] = _stamp(now + timedelta(seconds=slot["after_s"]))
                if paid["status"] == "payment_failed":
                    return
            self._after_failure(slot, paid, planned=plan.startswith("decline"))
            return
        if status == "payment_failed" and plan == "decline_retry" and len(payments) == 1:
            paid = self._pay(slot, None)
            if paid is not None:
                self._after_failure(slot, paid, planned=False)
            return
        if status == "payment_failed" and plan == "decline_cancel" and len(payments) == 1:
            self._cancel(slot)
            return
        slot["status"] = "done"

    def _pay(self, slot: dict[str, Any], simulate: str | None) -> dict | None:
        body: dict[str, Any] = {"method_code": slot["method"]}
        if simulate:
            body["simulate"] = simulate
        status, res = self.call("POST", f"/api/shop/orders/{slot['order_id']}/payments", body)
        if status == 200:
            slot["result"] = res["data"]["status"]
            return res["data"]
        if status >= 500:
            raise ConnectionError(f"결제 HTTP {status}")
        self.log(f"{slot['order_id']} 결제 거부 HTTP {status} {_code(res)}")
        slot["status"] = "done"
        return None

    def _cancel(self, slot: dict[str, Any]) -> None:
        status, res = self.call(
            "POST", f"/api/shop/orders/{slot['order_id']}/cancel", {"reason_code": "buyer"}
        )
        if status >= 500:
            raise ConnectionError(f"취소 HTTP {status}")
        if status == 200:
            slot["result"] = res["data"]["status"]
        slot["status"] = "done"

    def _after_failure(self, slot: dict[str, Any], order: dict, *, planned: bool) -> None:
        """결제 장애로 실패했으면(계획한 거절이 아니면) 일부는 결제 실패 문의를 남긴다."""
        slot["status"] = "done"
        if order["status"] != "payment_failed" or planned or not slot["inquiry_on_fail"]:
            return
        if slot.get("fail_inquiry"):
            return
        rng = gen.Rng(f"{self.model.seed}:failinq:{slot['order_id']}")
        subject, text = rng.choice(FAILED_PAYMENT_INQUIRIES)
        status, res = self.call(
            "POST",
            "/api/shop/inquiries",
            {"buyer_id": slot["buyer_id"], "order_id": slot["order_id"], "subject": subject,
             "body": text},
        )  # fmt: skip
        slot["fail_inquiry"] = status
        if status != 201:
            self.log(f"{slot['order_id']} 결제 실패 문의 HTTP {status} {_code(res)}")

    # 하루 끝 ---------------------------------------------------------------
    def _finalize(self, state: dict[str, Any], now: datetime) -> None:
        """그날 한 줄을 정답지에 더한다. 이미 적힌 날이면(적은 뒤 꺼진 경우) 다시 적지 않는다."""
        path = self.out / ANSWER_LINES
        written = path.read_text(encoding="utf-8") if path.is_file() else ""
        if f'"date": "{state["date"]}"' in written:
            state["finalized"] = True
            self._save_day(state)
            return
        slots = state["slots"]
        placed = [s for s in slots if s.get("order_id")]
        units: Counter[str] = Counter()
        for s in placed:
            for _pid, qty, genre in s["attempt"]:
                units[genre or "unknown"] += qty
        info = state["info"]
        line = {
            "date": state["date"],
            "seed": state["seed"],
            "written_at": _stamp(now),
            "timezone_for_days": "Asia/Seoul (KST)",
            "trend": {
                "genre": gen.TREND_GENRE,
                "phase": info["trend_phase"],
                "multiple": info["trend_multiple"],
                "days_since_start": info["trend_days_since_start"],
            },
            "day_factor": round(info["factor"], 4),
            "planned": state["planned"],
            "actual": {
                "placed": len(placed),
                "by_status": dict(Counter(s.get("result") for s in placed).most_common()),
                "units_by_genre": dict(units.most_common()),
                "by_slot_status": dict(Counter(s["status"] for s in slots).most_common()),
                "skipped_lines": sum(s.get("skipped_lines", 0) for s in slots),
                "replaced_lines": sum(s.get("replaced_lines", 0) for s in slots),
                "failed_payment_inquiries": sum(1 for s in slots if s.get("fail_inquiry") == 201),
            },
            "followups": state.get("followups", {}),
        }
        self.out.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
        state["finalized"] = True
        self._save_day(state)
        self.log(f"{state['date']} 마감: 계획 {len(slots)}건, 넣음 {len(placed)}건")

    # 발송·도착·그 뒤의 일 ----------------------------------------------------
    def _orders(self, since: datetime) -> list[dict]:
        params: dict[str, Any] = {"since": _stamp(since), "limit": ORDERS_PAGE}
        out = []
        while True:
            status, body = self.call("GET", f"/api/shop/orders?{urllib.parse.urlencode(params)}")
            if status != 200:
                raise ConnectionError(f"주문 목록 HTTP {status}")
            out.extend(body.get("data") or [])
            cursor = (body.get("meta") or {}).get("next_cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    def _followups(self, now: datetime, today: dict[str, Any] | None) -> dict[str, int]:
        path = self.out / FOLLOWUPS_FILE
        done: dict[str, str] = _read_json(path, {})
        horizon = now - timedelta(days=HORIZON_DAYS)
        done = {k: v for k, v in done.items() if _parse(v) >= horizon - timedelta(days=7)}
        counts: Counter[str] = Counter()
        try:
            orders = self._orders(horizon)
        except (ConnectionError, OSError, urllib.error.URLError) as error:
            self.log(f"발송·도착 확인을 못 했다(다음에 다시): {error}")
            return {}
        genres = {p.product_id: p.genre for p in self._products}
        actions = 0
        for order in orders:
            if actions >= FOLLOWUPS_PER_TICK:
                break
            if order["status"] != "paid" or not synthetic_ids.is_synthetic(order["buyer_id"]):
                continue
            try:
                did = self._followup(order, now, done, counts, genres)
            except (ConnectionError, OSError, urllib.error.URLError) as error:
                self.log(f"{order['order_id']} 처리 중 연결 오류(다음에 다시): {error}")
                break
            if did:
                actions += did
                _write_json(path, done)  # 한 일은 바로 적는다(다시 켜도 두 번 내지 않게)
        _write_json(path, done)
        if today is not None and counts:
            tally = Counter(today.get("followups") or {})
            tally.update(counts)
            today["followups"] = dict(tally)
            self._save_day(today)
        return dict(counts)

    def _followup(
        self,
        order: dict,
        now: datetime,
        done: dict[str, str],
        counts: Counter[str],
        genres: dict[str, str | None],
    ) -> int:
        """주문 하나의 발송·도착·클레임·후기·문의. 한 일의 수를 돌려준다."""
        plan = followup_plan(self.model, order, genres)
        if plan is None:
            return 0
        oid, actions = order["order_id"], 0
        base = f"/api/shop/admin/orders/{oid}"
        marking = f"mark:{oid}"  # 발송·도착 표시를 서버가 거부한 주문(다시 시도하지 않는다)
        if marking not in done and not order.get("shipped_at") and plan["shipped"] <= now:
            body = {"shipped_at": _stamp(plan["shipped"])}
            status, _ = self.call("POST", f"{base}/shipped", body)
            if status >= 500:
                raise ConnectionError(f"발송 HTTP {status}")
            counts["shipped" if status == 200 else "ship_failed"] += 1
            actions += 1
            if status == 200:
                order["shipped_at"] = _stamp(plan["shipped"])
            else:
                done[marking] = _stamp(now)
        if (
            marking not in done
            and order.get("shipped_at")
            and not order.get("delivered_at")
            and plan["delivered"] <= now
        ):
            body = {"delivered_at": _stamp(plan["delivered"])}
            status, _ = self.call("POST", f"{base}/delivered", body)
            if status >= 500:
                raise ConnectionError(f"도착 HTTP {status}")
            counts["delivered" if status == 200 else "deliver_failed"] += 1
            actions += 1
            if status == 200:
                order["delivered_at"] = body["delivered_at"]
            else:
                done[marking] = _stamp(now)
        delivered = _parse(order["delivered_at"]) if order.get("delivered_at") else None
        buyer = order["buyer_id"]
        if delivered is not None and delivered > self.model.cutoff:
            claim = plan["claim"]
            key = f"claim:{oid}"
            if claim and key not in done and claim["at"] <= now and claim["at"] > delivered:
                status, _ = self.call("POST", "/api/shop/claims", {
                    "buyer_id": buyer, "customer_name": f"가상회원{buyer[4:]}", "order_id": oid,
                    "type": claim["type"], "reason": claim["reason"], "items": claim["items"],
                    "unsealed": claim["unsealed"], "evidence_count": claim["evidence"],
                    "description": claim["text"],
                })  # fmt: skip
                if status >= 500:
                    raise ConnectionError(f"클레임 HTTP {status}")
                done[key] = _stamp(now)
                counts["claims" if status == 201 else "claim_refused"] += 1
                actions += 1
            for review in plan["reviews"]:
                key = f"review:{oid}:{review['product_id']}"
                if key in done or review["at"] > now or review["at"] <= delivered:
                    continue
                status, res = self.call(
                    "POST",
                    f"/api/shop/products/{review['product_id']}/reviews",
                    {"buyer_id": buyer, "order_id": oid, "rating": review["rating"],
                     "body": review["body"]},
                )  # fmt: skip
                if status >= 500:
                    raise ConnectionError(f"후기 HTTP {status}")
                done[key] = _stamp(now)
                ok = status == 201 or _code(res) == "REVIEW_EXISTS"
                counts["reviews" if ok else "review_refused"] += 1
                actions += 1
        inquiry = plan["inquiry"]
        key = f"inquiry:{oid}"
        if inquiry and key not in done and inquiry["at"] <= now:
            if inquiry["kind"] != "delivery" or delivered is None or inquiry["at"] < delivered:
                status, res = self.call("POST", "/api/shop/inquiries", {
                    "buyer_id": buyer, "order_id": oid, "subject": inquiry["subject"],
                    "body": inquiry["message"],
                })  # fmt: skip
                if status == 429:
                    return actions  # 문의 제한: 다음에 다시
                if status >= 500:
                    raise ConnectionError(f"문의 HTTP {status}")
                counts["inquiries" if status == 201 else "inquiry_refused"] += 1
            done[key] = _stamp(now)
            actions += 1
        return actions


def followup_plan(
    model: Model, order: dict, genres: dict[str, str | None] | None = None
) -> dict[str, Any] | None:
    """결제된 주문의 발송·도착·클레임·후기·문의 계획. 주문 번호로 정해 늘 같다(부분마다 따로).

    되채우기가 정한 일은 다시 정하지 않는다: 클레임·후기는 되채우기 끝(`cutoff`) 뒤에 도착한 주문만,
    문의는 그 뒤에 접수된 주문만 낸다(`Daily._followup`이 거른다). 발송은 끝 뒤로만 적는다.
    """
    paid_list = [p["created_at"] for p in order.get("payments") or [] if p["status"] == "succeeded"]
    if not paid_list:
        return None
    oid = order["order_id"]
    paid = _parse(min(paid_list))
    created = _parse(order["created_at"])

    def rng(part: str) -> gen.Rng:
        return gen.Rng(f"{model.seed}:followup:{oid}:{part}")

    ship = rng("ship")
    day = created.astimezone(KST).date()
    trend = gen.trend_multiple((day - model.trend_start).days + 0.5) - 1
    shipped = gen._shift_holiday(paid + timedelta(hours=ship.uniform(*gen.SHIP_HOURS)), ship)
    if paid < model.cutoff:  # 되채우기가 미발송으로 남긴 주문: 끝 뒤로만 적는다
        shipped = max(shipped, model.cutoff + timedelta(minutes=1))
    late_p = gen.LATE_BASE + gen.LATE_TREND * min(1.0, trend / (gen.TREND_PEAK - 1))
    hours = gen.LATE_HOURS if ship.random() < late_p else gen.DELIVER_HOURS
    delivered = gen._shift_holiday(shipped + timedelta(hours=ship.uniform(*hours)), ship)
    shipped, delivered = shipped.replace(microsecond=0), delivered.replace(microsecond=0)
    promised = paid + timedelta(days=gen.PROMISED_DELIVERY_DAYS)
    late = delivered > promised
    items = order.get("items") or []
    claim, claimed = None, set()
    pick = rng("claim")
    if items and pick.random() < gen.CLAIM_RATE:
        reason = gen.claim_reason(late, pick)
        chosen = items if (len(items) > 1 and pick.random() < 0.2) else [pick.choice(items)]
        span = 6 if reason == "change_of_mind" else 12
        at = delivered + timedelta(hours=6) + timedelta(days=pick.uniform(0, span))
        text, evidence, unsealed, kind = gen.claim_details(reason, pick)
        claimed = {i["product_id"] for i in chosen}
        claim = {
            "at": at.replace(microsecond=0),
            "reason": reason,
            "type": kind,
            "items": [{"order_item_id": i["order_item_id"], "qty": i["qty"]} for i in chosen],
            "text": text,
            "evidence": evidence,
            "unsealed": unsealed,
        }
    reviews = []
    for item in items:
        pid = item["product_id"]
        write = rng(f"review:{pid}")
        if write.random() >= gen.REVIEW_RATE:
            continue
        at = delivered + timedelta(days=1) + timedelta(days=write.uniform(0, 13))
        reason = claim["reason"] if claim and pid in claimed else None
        rating, body = gen.review_text((genres or {}).get(pid), reason, write)
        reviews.append(
            {"product_id": pid, "at": at.replace(microsecond=0), "rating": rating, "body": body}
        )
    inquiry = None
    if created > model.cutoff:
        lines = [
            gen.Line(gen.Product(i["product_id"], i.get("seller_id", ""), i.get("title") or "",
                                 "book", int(i["unit_price"]), 0, None), int(i["qty"]))
            for i in items
        ]  # fmt: skip
        shadow = gen.Order(oid, None, created, 0, lines)  # type: ignore[arg-type]
        shadow.paid, shadow.promised = paid, promised
        shadow.delivered, shadow.late = delivered, late
        found = gen.plan_inquiry(shadow, rng("inquiry"), created + timedelta(days=60))
        if found is not None:
            inquiry = {"at": found.created, "kind": found.kind, "subject": found.subject,
                       "message": found.message}  # fmt: skip
    return {
        "shipped": shipped,
        "delivered": delivered,
        "claim": claim,
        "reviews": reviews,
        "inquiry": inquiry,
    }


# --- 실행 -----------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="가상 회원의 하루 주문을 판매 서버에 넣는다")
    parser.add_argument("--url", default=os.getenv("SHOP_API_URL", DEFAULT_URL))
    parser.add_argument("--interval-s", type=float, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--max-per-tick", type=int, default=DEFAULT_MAX_PER_TICK)
    parser.add_argument("--seed", type=int, help="하루 계획의 시드(기본: 되채우기 시드)")
    parser.add_argument("--out", type=Path, default=gen.DEFAULT_OUT, help="정답지·상태 폴더")
    parser.add_argument("--once", action="store_true", help="한 번만 돌고 끝낸다(cron·시험)")
    args = parser.parse_args(argv)
    if args.interval_s <= 0 or args.max_per_tick < 1:
        parser.error("--interval-s는 0보다, --max-per-tick은 1 이상이어야 한다")
    token = admin_token()
    if not token:
        print("관리자 토큰이 없다(SHOP_ADMIN_TOKEN 또는 AX_AGENT_TOKENS의 admin)", file=sys.stderr)
        return 1
    call = http_caller(args.url, token)
    try:
        model = load_model(args.out, args.seed)
        check_members(call, model)
    except (DailyError, ValueError, KeyError) as error:
        print(error, file=sys.stderr)
        return 2
    except (OSError, urllib.error.URLError) as error:
        print(f"판매 서버에 연결하지 못했다: {error}", file=sys.stderr)
        return 1

    def log(line: str) -> None:
        print(f"{datetime.now(KST):%m-%d %H:%M:%S} {line}", flush=True)

    daily = Daily(
        call, model, args.out, interval_s=args.interval_s, max_per_tick=args.max_per_tick, log=log
    )
    log(f"가상 하루 흐름 시작: 회원 {len(model.members):,}명, {args.interval_s:g}초마다")
    while True:
        try:
            done = daily.tick()
            if any(done.values()):
                log("이번 차례: " + ", ".join(f"{k}={v}" for k, v in done.items() if v))
        except (ConnectionError, OSError, urllib.error.URLError) as error:
            log(f"판매 서버 오류(다음 차례에 다시): {error}")
        if args.once:
            return 0
        time.sleep(args.interval_s)


if __name__ == "__main__":
    raise SystemExit(main())
