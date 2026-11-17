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
  사람 주문도 옛 shop_traffic과 같은 시각으로 발송·도착만 적는다(`--no-deliver-real`로 끈다).
- 그 뒤의 일: 매일 모드가 도착시킨 주문에 되채우기와 같은 비율로 클레임·후기를, 매일 모드가 넣은
  주문에 문의를 낸다. 결제 장애로 실패한 회원 일부는 결제 실패 문의를 남긴다(옛 `shop_traffic`처럼).
- 다시 켜도 안전: 날마다 계획·진행을 `--out/daily_state/<날짜>.json`에 적는다. 주문은 날짜·순번으로
  만든 멱등 키를 보내 다시 보내도 서버가 처음 주문을 돌려준다(두 번 넣지 않는다).
- 유입 출처: 주문마다 `src_source`·`src_medium`·`src_campaign`을 붙인다(직접 40%·네이버 검색 25%·
  유튜브 15%·인스타그램 10%·기타 10%, AI·IT 트렌드 때 유튜브 몫이 트렌드 배수만큼 커진다).
  (시드, 날짜, 순번)으로 정해 계획에 적어 두므로 다시 켜도 같은 값을 보낸다.
- 정답지: 하루가 끝나면 `--out/daily_answer_key.jsonl`에 그날 한 줄(계획한 장르·페르소나·출처 합계,
  트렌드 단계·배수, 실제로 넣은 건수)을 더한다. 로컬 전용이고 저장소·DB에 넣지 않는다.
- 되채우기 정답지(`--out/answer_key.json`)와 가상 회원이 없으면 멈춘다(회원을 같은 시드로 다시
  만들어 DB의 회원과 맞춰 본다). 같은 `--out`에 두 번째 프로세스가 뜨면 잠금 파일로 멈춘다.
- 재고: 기준(`SHOP_TRAFFIC_LOW_STOCK`, 기본 10) 아래로 내려간 상품은 `--restock-lead-h`(기본 24)
  시간 그대로 둔 뒤 `SHOP_TRAFFIC_REFILL_TO`(기본 200)개로 채운다(관리 API, shop_traffic과 같은 값).
- 상품 고르기: 되채우기의 인기 순위(`backfill_extra.json`)를 이어 쓰고 새 상품은 시드·상품 번호
  해시로 끼워, 상품이 늘거나 빠져도 다른 상품의 순서가 바뀌지 않는다. 품절 상품은 고르지 않는다.
- CRX-001: 심지 않은 책이 클레임 기준(최근 30일 판매 ≥5·클레임 수량 ≥10%·클레임 ≥2, 또는 불량 ≥3)에
  걸리게 될 클레임은 단순 변심으로 바꾸고, 그래도 걸리면 내지 않는다(되채우기와 같은 막기).
- 판매 서버가 401·403·408·429·5xx로 답하거나 연결이 끊기면 그 일은 다음 차례에 다시 한다.
  404·409·422만 끝난 것으로 친다.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
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
# 발송·도착·클레임·후기·문의를 보는 주문 기간(접수일 기준). 결제→도착 최대 약 6일 + 추석 연휴 밀림
# + 후기 14일보다 길고, CRX-001의 최근 30일 판매도 이 목록으로 센다
HORIZON_DAYS = 30
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
# 다시 하면 될 수 있는 응답(토큰 일시 문제·제한·서버 오류). 404·409·422만 끝난 것으로 친다
RETRY_STATUSES = frozenset({0, 401, 403, 408, 429})
RESTOCK_LOW = int(os.getenv("SHOP_TRAFFIC_LOW_STOCK", "10"))  # shop_traffic StockWatch와 같은 값
RESTOCK_TO = int(os.getenv("SHOP_TRAFFIC_REFILL_TO", "200"))
DEFAULT_RESTOCK_LEAD_H = 24.0
STOCK_FILE = "daily_stock.json"
LOCK_FILE = "daily.lock"
CRX_DAYS = 30
# 유입 출처(utm_source, utm_medium, 평소 몫). 데이터팀이 정한 가정 비율이다. 페르소나별 기울기는
# 두지 않는다(그날 합계가 이 비율에 그대로 수렴해야 정답지로 쓰기 쉽다)
SOURCE_MIX = (
    ("direct", "none", 0.40),
    ("naver", "search", 0.25),
    ("youtube", "video", 0.15),
    ("instagram", "social", 0.10),
    ("other", "referral", 0.10),
)
SOURCE_MEDIUM = {source: medium for source, medium, _share in SOURCE_MIX}
TREND_SOURCE = "youtube"  # 트렌드 배수(`trend_multiple`)만큼 가중치가 커지고 나머지는 다시 나눈다
TREND_CAMPAIGN = "ai_it_trend"  # 트렌드 기간의 유튜브·인스타그램 주문에만 붙는 캠페인 표시
CAMPAIGN_SOURCES = frozenset({"youtube", "instagram"})
CAMPAIGN_PHASES = frozenset({"rising", "peak", "decay"})

Call = Callable[..., tuple[int, dict]]


class DailyError(Exception):
    """매일 모드를 시작할 수 없다(되채우기 없음·토큰 없음 등)."""


class RetryLater(ConnectionError):
    """판매 서버가 지금은 받지 않았다(401·403·408·429·5xx). 다음 차례에 다시 한다."""


def _retry_if(status: int, what: str) -> None:
    if status in RETRY_STATUSES or status >= 500:
        raise RetryLater(f"{what} HTTP {status}")


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
    ranks: dict[str, list[str]] = field(default_factory=dict)  # 되채우기의 장르별 인기 순위
    planted: frozenset[str] = frozenset()  # 걸려야 하는 심은 책(CRX 막기에서 뺀다)
    backfill_claims: list[list[Any]] = field(default_factory=list)  # [상품, 시각, 수량, 사유]


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
    extra = _read_json(out / gen.BACKFILL_EXTRA, {}) or {}
    planted = extra.get("planted") or {
        k: [b["product_id"] for b in key.get("planted", {}).get(k, [])]
        for k in ("defect_cluster", "low_sales_single")
    }
    return Model(
        seed=config.seed if seed is None else seed,
        member_seed=config.seed,
        members=members,
        norms=norms,
        trend_start=window.trend_start,
        cutoff=window.cutoff,
        ranks=extra.get("shelf_ranks") or {},
        planted=frozenset(planted.get("defect_cluster") or []),
        backfill_claims=extra.get("claims") or [],
    )


def check_members(call: Call, model: Model) -> None:
    """DB에 가상 회원이 있고 다시 만든 회원과 같은지 몇 명만 맞춰 본다. 다르면 멈춘다."""
    members = model.members
    n = len(members)
    picks = sorted({0, n // 7, n // 5, n // 3, n // 2, (2 * n) // 3, (4 * n) // 5, n - 1})
    matched = 0
    for index in picks:
        member = members[index]
        status, body = call("GET", f"/api/shop/users/{member.buyer_id}")
        if status == 404:  # 삭제 요청으로 지워진 회원일 수 있다: 다른 회원으로 본다
            continue
        if status != 200:
            raise DailyError(f"가상 회원을 확인하지 못했다(HTTP {status} {_code(body)})")
        user = body.get("data") or {}
        found = (user.get("age_band"), user.get("gender"))
        if found == (member.age_band, member.gender):
            matched += 1
        elif found != (None, None):  # 탈퇴·삭제 집행은 속성을 비운다(다른 값이면 다른 DB)
            raise DailyError(
                f"{member.buyer_id}의 연령대·성별이 정답지로 다시 만든 회원과 다르다. "
                "--out이 이 DB의 되채우기 정답지인지 확인한다"
            )
    if not matched:
        raise DailyError("가상 회원이 판매 서버에 없다. 되채우기(gen_synthetic.py)를 먼저 넣는다")


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


def source_mix(info: dict[str, Any]) -> dict[str, float]:
    """그날 출처 몫. 평소 몫에서 유튜브만 트렌드 배수를 곱하고 합이 1이 되게 다시 나눈다
    (최고점 ×4.1이면 유튜브 0.15×4.1 / (0.85 + 0.615) ≈ 42%)."""
    multiple = float(info.get("trend_multiple") or 1.0)
    weights = {
        source: share * (multiple if source == TREND_SOURCE else 1.0)
        for source, _medium, share in SOURCE_MIX
    }
    total = sum(weights.values())
    return {source: w / total for source, w in weights.items()}


def pick_source(seed: int, day: str, k: int, info: dict[str, Any]) -> list[str | None]:
    """주문 한 건의 `[src_source, src_medium, src_campaign]`. (시드, 날짜, 순번)으로 정해 늘 같다.

    계획의 다른 뽑기와 따로 된 난수열이라 출처를 붙여도 장바구니·결제 계획은 그대로다.
    캠페인은 트렌드(rising·peak·decay) 때의 유튜브·인스타그램에만 붙고 나머지는 비운다.
    """
    rng = gen.Rng(f"{seed}:daily:{day}:{k}:src")
    source = rng.pick(source_mix(info).items())
    campaign = (
        TREND_CAMPAIGN
        if source in CAMPAIGN_SOURCES and info.get("trend_phase") in CAMPAIGN_PHASES
        else None
    )
    return [source, SOURCE_MEDIUM[source], campaign]


def source_tally(slots: list[dict[str, Any]]) -> dict[str, Any]:
    """출처별 주문 수·몫과 캠페인별 수(출처가 없는 옛 계획의 주문은 `unknown`)."""
    by_source = Counter((s.get("src") or ["unknown"])[0] for s in slots)
    campaigns = Counter(s["src"][2] for s in slots if s.get("src") and s["src"][2])
    total = sum(by_source.values())
    return {
        "by_source": dict(sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0]))),
        "source_shares": {
            k: round(v / total, 4) for k, v in sorted(by_source.items(), key=lambda kv: kv[0])
        }
        if total
        else {},
        "by_campaign": dict(sorted(campaigns.items())),
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


def _hash_fraction(*parts: object) -> float:
    digest = hashlib.sha256(":".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


class DailyShelf:
    """매일 모드의 상품 고르기(`gen.Shelf`와 같은 `pick`·`filler`, 가중치 모양도 같다).

    다른 점: (1) 장르 안 순위는 되채우기 순위(`Model.ranks`)를 그대로 잇고, 그때 없던 상품은
    시드·상품 번호 해시로 정한 자리에 끼운다. 상품이 늘거나 빠져도 남은 상품끼리의 순서가 같다.
    (2) 재고가 0인 상품은 고르지 않는다(트렌드 쏠림 포함). 되채우기 `Shelf`는 그대로 둔다.
    """

    def __init__(
        self,
        products: list[gen.Product],
        seed: int,
        ranks: dict[str, list[str]],
        stock: dict[str, int] | None = None,
    ) -> None:
        self.stock = {p.product_id: p.stock for p in products} if stock is None else stock
        by_genre: dict[str, list[gen.Product]] = {}
        for p in products:
            if p.genre in gen.GENRES:
                by_genre.setdefault(p.genre, []).append(p)
        self.by_genre = by_genre
        untagged = [p for p in products if p.genre is None and p.category == "book"]
        tagged = [p for g in gen.BOOK_GENRES for p in by_genre.get(g, [])]
        stationery = [p for p in products if p.genre is None and p.category == "stationery"] or [
            p for g in gen.STATIONERY_GENRES for p in by_genre.get(g, [])
        ]
        pools = {**by_genre, "_book": untagged or tagged or list(products),
                 "_stationery": stationery}  # fmt: skip
        self.pools: dict[str, tuple[list[gen.Product], list[float], list[float]]] = {}
        for key, pool in pools.items():
            order = self.order(key, pool, seed, ranks)
            base, hot = [], []
            for rank, p in enumerate(order):
                free = self.stock.get(p.product_id, 0) > 0
                base.append(max(1, p.stock) / (rank + 1) ** gen.POPULARITY_EXP if free else 0.0)
                boost = gen.AI_TITLE_BOOST if p.ai_title else 1.0
                hot.append(boost / (rank + 1) ** gen.TREND_POPULARITY_EXP if free else 0.0)
            self.pools[key] = (order, gen._cumulative(base), gen._cumulative(hot))
        cheap = [p for p in products if self.stock.get(p.product_id, 0) > 0]
        self.by_price = sorted(cheap, key=lambda p: (p.price, p.product_id))
        self.prices = [p.price for p in self.by_price]

    @staticmethod
    def order(
        key: str, pool: list[gen.Product], seed: int, ranks: dict[str, list[str]]
    ) -> list[gen.Product]:
        known = {pid: i for i, pid in enumerate(ranks.get(key) or [])}
        size = max(1, len(known))

        def score(p: gen.Product) -> tuple[float, str]:
            if p.product_id in known:
                return float(known[p.product_id]), p.product_id
            return _hash_fraction(seed, "rank", key, p.product_id) * size, p.product_id

        return sorted(pool, key=score)

    def pick(self, genre: str, rng: gen.Rng, hot: float) -> gen.Product | None:
        if self.by_genre.get(genre):
            key = genre
        else:
            key = "_stationery" if genre in gen.STATIONERY_GENRES else "_book"
        order, base, trend = self.pools[key]
        cumulative = trend if (genre == gen.TREND_GENRE and rng.random() < hot) else base
        if not order or not cumulative or cumulative[-1] <= 0:
            return None  # 이 장르는 다 품절
        return order[bisect.bisect_right(cumulative, rng.random() * cumulative[-1])]

    def filler(self, gap: int, rng: gen.Rng) -> gen.Product | None:
        start = bisect.bisect_left(self.prices, gap)
        choices = self.by_price[start : start + 30]
        return rng.choice(choices) if choices else None


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
    shelf = DailyShelf(products, model.seed, model.ranks)
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
        lines = gen.basket(member, info["trend"], model.norms, shelf, srng)  # type: ignore[arg-type]
        slots.append(
            {
                "k": k,
                "at": _stamp(moment),
                "buyer_id": member.buyer_id,
                "persona": member.persona.code,
                "lines": [[p.product_id, qty, p.genre] for p, qty in lines],
                **_payment_plan(srng),
                "inquiry_on_fail": srng.random() < FAILED_PAY_INQUIRY,
                "src": pick_source(model.seed, day.isoformat(), k, info),
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
        **source_tally(slots),
    }


# --- 상태 파일 --------------------------------------------------------------------------------
def _write_json(path: Path, data: Any) -> None:
    """다른 이름에 쓰고 디스크에 내린(fsync) 뒤 바꿔 끼운다(쓰다 꺼져도 앞 내용이 남는다)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=1))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read_json(path: Path, default: Any, log: Callable[[str], None] | None = None) -> Any:
    """깨진 파일은 `.corrupt-<시각>`으로 옮기고 `default`로 이어 간다(멈추며 되풀이하지 않게)."""
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError):
        aside = path.with_name(f"{path.name}.corrupt-{datetime.now(UTC):%Y%m%dT%H%M%S}")
        os.replace(path, aside)
        (log or print)(f"깨진 상태 파일을 옮기고 새로 시작한다: {path.name} → {aside.name}")
        return default


class InstanceLock:
    """같은 `--out`에 매일 모드가 하나만 돌게 하는 잠금(운영체제 파일 잠금, 꺼지면 풀린다)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+")
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise DailyError(f"다른 매일 모드가 이미 돌고 있다({path})") from error

    def release(self) -> None:
        self.file.close()


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
        restock_lead_h: float | None = DEFAULT_RESTOCK_LEAD_H,
        deliver_real: bool = True,
    ) -> None:
        self.deliver_real = deliver_real
        self.restock_lead = None if restock_lead_h is None else timedelta(hours=restock_lead_h)
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
        return _read_json(self._day_path(day), None, self.log)

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
        before = now - self.interval
        late = [s for s in slots if _parse(s["at"]) < before]
        day_start = gen._kst_midnight(day)
        if late and self.model.cutoff >= day_start - timedelta(hours=2):
            # 되채우기가 어제까지인 첫날: 지난 몫을 버리면 가짜 매출 하락이 되니, 남은 하루에 고르게
            # 나눠 넣는다(그날 합계는 맞고, 첫날의 시간대 모양만 평평해진다)
            end = min(gen._kst_midnight(day + timedelta(days=1)), now + timedelta(hours=12))
            step = (end - now) / (len(late) + 1)
            for i, slot in enumerate(late, start=1):
                slot["next_at"] = slot["late_from"] = _stamp(now + step * i)
                slot["deferred"] = True
        else:
            # 하루 중간에 다시 켠 둘째 날부터: 상태 파일이 없으면 지난 시각의 계획은 넣지 않는다
            for slot in late:
                slot["status"] = "before_start"
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

    def _restock(self, now: datetime) -> int:
        """기준 아래 상품을 `restock_lead` 동안 그대로 두었다가 관리 API로 채운다(shop_traffic과
        같은 기준·양). 품절이 잠깐은 보이고(트렌드 책 재입고 문의), 재고가 줄기만 하지 않는다."""
        if self.restock_lead is None:
            return 0
        path = self.out / STOCK_FILE
        low: dict[str, str] = _read_json(path, {}, self.log)
        low = {p: at for p, at in low.items() if self._stock.get(p, RESTOCK_LOW) < RESTOCK_LOW}
        done = 0
        try:
            for p in self._products:
                if p.stock >= RESTOCK_LOW:
                    continue
                since = low.setdefault(p.product_id, _stamp(now))
                if now - _parse(since) < self.restock_lead:
                    continue
                status, res = self.call(
                    "PATCH", f"/api/shop/products/{p.product_id}", {"stock": RESTOCK_TO}
                )
                _retry_if(status, "재고 보충")
                low.pop(p.product_id, None)
                if status == 200:
                    p.stock = self._stock[p.product_id] = int(res["data"]["stock"])
                    done += 1
        finally:
            _write_json(path, low)
        return done

    # 한 번 ----------------------------------------------------------------
    def tick(self) -> dict[str, int]:
        """그때까지 된 계획 주문을 넣고(최대 `max_per_tick`건), 발송·도착·그 뒤의 일을 한다."""
        now = self.clock()
        self._refresh_products()
        try:
            restocked = self._restock(now)
        except (ConnectionError, OSError, urllib.error.URLError) as error:
            self.log(f"재고 보충을 못 했다(다음에 다시): {error}")
            restocked = 0
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
        return {**budget, "restocked": restocked, **self._followups(now, current)}

    def _run_day(self, state: dict[str, Any], now: datetime, budget: dict[str, int]) -> None:
        day = date.fromisoformat(state["date"])
        for slot in state["slots"]:
            if slot["status"] in TERMINAL:
                continue
            due = _parse(slot.get("next_at") or slot["at"])
            if due > now:
                continue
            overdue = now - _parse(slot.get("late_from") or slot["at"]) > MAX_LATE
            if overdue and slot["status"] in ("planned", "placing"):
                # 너무 늦은 주문은 넣지 않는다(보내던 중이었으면 서버에 결제 대기로 남을 수 있다)
                slot["status"] = "missed"
                self._save_day(state)
                continue
            if overdue and slot["status"] == "open":
                slot["status"], slot["abandoned"] = "done", True  # 남은 결제 단계를 버린다
                self._save_day(state)
                continue
            if slot["status"] == "planned":
                if budget["placed"] >= self.max_per_tick:
                    continue
                budget["placed"] += 1
            try:
                self._advance(state, slot, now)
            except (ConnectionError, OSError, urllib.error.URLError) as error:
                self.log(f"{slot['buyer_id']} 주문 처리를 다음 차례로 미룬다: {error}")
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
        shelf = DailyShelf(self._products, self.model.seed, self.model.ranks, self._stock)
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
        if slot.get("src"):  # 계획에 적어 둔 출처(다시 보내도 같은 값)
            body.update(zip(("src_source", "src_medium", "src_campaign"), slot["src"], strict=True))
        status, res = self.call("POST", "/api/shop/orders", body)
        if status in (200, 201):
            return res["data"]
        if status == 409 and _code(res) == "OUT_OF_STOCK":
            return None
        _retry_if(status, "주문")
        self.log(f"{slot['buyer_id']} 주문 거부 HTTP {status} {_code(res)}")
        slot["status"], slot["error"] = "dropped", _code(res)
        return None

    def _advance(self, state: dict[str, Any], slot: dict[str, Any], now: datetime) -> None:
        if slot["status"] == "planned":
            lines = self._lines_now(state, slot)
            if not lines:
                slot["status"] = "dropped"
                return
            if "src" not in slot:  # 출처가 없던 옛 계획: 같은 (시드, 날짜, 순번)으로 정해 적는다
                slot["src"] = pick_source(state["seed"], state["date"], slot["k"], state["info"])
            slot["attempt"], slot["status"] = lines, "placing"
            # 보낸 줄·출처를 먼저 적는다(다시 켜면 같은 줄·출처로 다시 보낸다)
            self._save_day(state)
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
            if status == 404:
                slot["status"] = "done"
                return
            if status != 200:
                raise RetryLater(f"주문 조회 HTTP {status}")
            self._pay_step(slot, res["data"], now)

    def _pay_step(self, slot: dict[str, Any], order: dict, now: datetime) -> None:
        """서버의 주문 상태로 다음 결제 단계를 정한다(다시 켜도 결제를 두 번 하지 않는다)."""
        plan, status = slot["pay"], order["status"]
        payments = order.get("payments") or []
        created = _parse(order["created_at"])
        slot["result"] = status
        if slot.get("fail_inquiry_pending"):
            self._fail_inquiry(slot)
            return
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
        _retry_if(status, "결제")
        self.log(f"{slot['order_id']} 결제 거부 HTTP {status} {_code(res)}")
        slot["status"] = "done"
        return None

    def _cancel(self, slot: dict[str, Any]) -> None:
        status, res = self.call(
            "POST", f"/api/shop/orders/{slot['order_id']}/cancel", {"reason_code": "buyer"}
        )
        _retry_if(status, "취소")
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
        slot["status"], slot["fail_inquiry_pending"] = "open", True  # 문의가 끝나야 끝난다
        self._fail_inquiry(slot)

    def _fail_inquiry(self, slot: dict[str, Any]) -> None:
        rng = gen.Rng(f"{self.model.seed}:failinq:{slot['order_id']}")
        subject, text = rng.choice(FAILED_PAYMENT_INQUIRIES)
        status, res = self.call(
            "POST",
            "/api/shop/inquiries",
            {"buyer_id": slot["buyer_id"], "order_id": slot["order_id"], "subject": subject,
             "body": text},
        )  # fmt: skip
        _retry_if(status, "결제 실패 문의")
        slot["fail_inquiry"], slot["status"] = status, "done"
        slot.pop("fail_inquiry_pending", None)
        if status != 201:
            self.log(f"{slot['order_id']} 결제 실패 문의 HTTP {status} {_code(res)}")

    # 하루 끝 ---------------------------------------------------------------
    def _finalize(self, state: dict[str, Any], now: datetime) -> None:
        """그날 한 줄을 정답지에 더한다. 이미 적힌 날이면(적은 뒤 꺼진 경우) 다시 적지 않는다."""
        path = self.out / ANSWER_LINES
        written = path.read_text(encoding="utf-8") if path.is_file() else ""
        dates = set()
        for raw in written.splitlines():
            try:
                dates.add(json.loads(raw).get("date"))
            except (ValueError, AttributeError):
                continue  # 쓰다 끊긴 줄은 건너뛴다
        if state["date"] in dates:
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
            "source_mix": {k: round(v, 4) for k, v in source_mix(info).items()},
            "planned": {**source_tally(slots), **state["planned"]},
            "actual": {
                "placed": len(placed),
                **source_tally(placed),
                "by_status": dict(Counter(s.get("result") for s in placed).most_common()),
                "units_by_genre": dict(units.most_common()),
                "by_slot_status": dict(Counter(s["status"] for s in slots).most_common()),
                "skipped_lines": sum(s.get("skipped_lines", 0) for s in slots),
                "replaced_lines": sum(s.get("replaced_lines", 0) for s in slots),
                "failed_payment_inquiries": sum(1 for s in slots if s.get("fail_inquiry") == 201),
                "deferred_first_day": sum(1 for s in slots if s.get("deferred")),
                "abandoned_open": sum(1 for s in slots if s.get("abandoned")),
            },
            "followups": state.get("followups", {}),
        }
        self.out.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            if written and not written.endswith("\n"):
                f.write("\n")  # 끊긴 줄 뒤에 붙지 않게
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
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

    def _load_followups(self, now: datetime) -> dict[str, Any]:
        """`{"done": {키: 시각}, "claims": [[상품, 시각, 수량, 사유], ...]}`. 클레임 기록은 처음에
        되채우기 클레임(`backfill_extra.json`)으로 채우고 최근 30일만 남긴다(CRX-001 막기용)."""
        data = _read_json(self.out / FOLLOWUPS_FILE, {}, self.log)
        if "done" not in data:  # 옛 모양(키: 시각)
            data = {"done": data}
        if "claims" not in data:
            data["claims"] = [list(c) for c in self.model.backfill_claims]
        floor = now - timedelta(days=HORIZON_DAYS + 7)
        data["done"] = {k: v for k, v in data["done"].items() if _parse(v) >= floor}
        recent = now - timedelta(days=CRX_DAYS + 1)
        data["claims"] = [c for c in data["claims"] if _parse(c[1]) >= recent]
        return data

    def _followups(self, now: datetime, today: dict[str, Any] | None) -> dict[str, int]:
        path = self.out / FOLLOWUPS_FILE
        data = self._load_followups(now)
        horizon = now - timedelta(days=HORIZON_DAYS)
        counts: Counter[str] = Counter()
        try:
            orders = self._orders(horizon)
        except (ConnectionError, OSError, urllib.error.URLError) as error:
            self.log(f"발송·도착 확인을 못 했다(다음에 다시): {error}")
            return {}
        genres = {p.product_id: p.genre for p in self._products}
        sold: Counter[str] = Counter()  # 최근 30일 결제된 수량(사람 주문 포함), CRX-001 판매 수
        since = now - timedelta(days=CRX_DAYS)
        for order in orders:
            if order["status"] == "paid" and _parse(order["created_at"]) >= since:
                for item in order.get("items") or []:
                    sold[item["product_id"]] += int(item["qty"])
        actions = 0
        for order in orders:
            if actions >= FOLLOWUPS_PER_TICK:
                break
            if order["status"] != "paid":
                continue
            try:
                if synthetic_ids.is_synthetic(order["buyer_id"]):
                    did = self._followup(order, now, data, counts, genres, sold)
                elif self.deliver_real:
                    did = self._deliver_real(order, now, data["done"], counts)
                else:
                    continue
            except (ConnectionError, OSError, urllib.error.URLError) as error:
                self.log(f"{order['order_id']} 그 뒤의 일을 다음 차례로 미룬다: {error}")
                _write_json(path, data)
                break
            if did:
                actions += did
                _write_json(path, data)  # 한 일은 바로 적는다(다시 켜도 두 번 내지 않게)
        _write_json(path, data)
        if today is not None and counts:
            tally = Counter(today.get("followups") or {})
            tally.update(counts)
            today["followups"] = dict(tally)
            self._save_day(today)
        return dict(counts)

    def _deliver_real(
        self, order: dict, now: datetime, done: dict[str, str], counts: Counter[str]
    ) -> int:
        """사람(가상이 아닌) 주문의 발송·도착만 옛 shop_traffic과 같은 시각으로 적는다(판매자 정산이
        보류되지 않게). 도착 시각이 지난 주문만, 이미 적힌 값은 건드리지 않는다."""
        if order.get("delivered_at") or f"mark:{order['order_id']}" in done:
            return 0
        paid = [p["created_at"] for p in order.get("payments") or [] if p["status"] == "succeeded"]
        shipped, arrived = real_delivery_plan(
            order["order_id"], _parse(min(paid) if paid else order["created_at"])
        )
        if arrived > now:
            return 0
        base = f"/api/shop/admin/orders/{order['order_id']}"
        actions = 0
        if not order.get("shipped_at"):
            status, _ = self.call("POST", f"{base}/shipped", {"shipped_at": _stamp(shipped)})
            _retry_if(status, "발송")
            actions += 1
            if status != 200:
                done[f"mark:{order['order_id']}"] = _stamp(now)
                counts["real_mark_failed"] += 1
                return actions
        status, _ = self.call("POST", f"{base}/delivered", {"delivered_at": _stamp(arrived)})
        _retry_if(status, "도착")
        if status == 200:
            counts["real_delivered"] += 1
        else:
            done[f"mark:{order['order_id']}"] = _stamp(now)
            counts["real_mark_failed"] += 1
        return actions + 1

    def _crx_reason(
        self, claim: dict, order: dict, data: dict[str, Any], sold: Counter[str], now: datetime
    ) -> str | None:
        """심지 않은 책이 CRX-001에 걸리게 되면 단순 변심으로, 그래도 걸리면 None(내지 않음)."""
        by_item = {i["order_item_id"]: i["product_id"] for i in order.get("items") or []}
        lines = [(by_item.get(i["order_item_id"]), int(i["qty"])) for i in claim["items"]]
        since = now - timedelta(days=CRX_DAYS)
        seen: dict[str, Counter[str]] = {}
        for pid, at, qty, reason in data["claims"]:
            if _parse(at) >= since:
                c = seen.setdefault(pid, Counter())
                c["claims"] += 1
                c["qty"] += int(qty)
                c["defect"] += int(reason in gen.DEFECT_REASONS)

        def trips(reason: str) -> bool:
            for pid, qty in lines:
                if pid is None or pid in self.model.planted:
                    continue
                c = seen.get(pid, Counter())
                defect = c["defect"] + int(reason in gen.DEFECT_REASONS)
                if gen.crx_trips(sold[pid], c["claims"] + 1, c["qty"] + qty, defect):
                    return True
            return False

        if not trips(claim["reason"]):
            return claim["reason"]
        return None if trips("change_of_mind") else "change_of_mind"

    def _followup(
        self,
        order: dict,
        now: datetime,
        data: dict[str, Any],
        counts: Counter[str],
        genres: dict[str, str | None],
        sold: Counter[str],
    ) -> int:
        """주문 하나의 발송·도착·클레임·후기·문의. 한 일의 수를 돌려준다.

        401·403·408·429·5xx·연결 오류는 `RetryLater`로 올려 다음 차례에 다시 한다(끝난 것으로 적지
        않는다). 404·409·422만 서버가 거부한 것으로 적는다.
        """
        done: dict[str, str] = data["done"]
        plan = followup_plan(self.model, order, genres)
        if plan is None:
            return 0
        oid, actions = order["order_id"], 0
        base = f"/api/shop/admin/orders/{oid}"
        marking = f"mark:{oid}"  # 발송·도착 표시를 서버가 거부한 주문(다시 시도하지 않는다)
        if marking not in done and not order.get("shipped_at") and plan["shipped"] <= now:
            body = {"shipped_at": _stamp(plan["shipped"])}
            status, _ = self.call("POST", f"{base}/shipped", body)
            _retry_if(status, "발송")
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
            _retry_if(status, "도착")
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
                reason = self._crx_reason(claim, order, data, sold, now)
                if reason is None:
                    done[key] = _stamp(now)
                    counts["claim_skipped_crx"] += 1
                    actions += 1
                else:
                    if reason != claim["reason"]:  # 바꾼 사유에 맞는 문장·종류
                        swap = gen.Rng(f"{self.model.seed}:followup:{oid}:claim-cm")
                        text, evidence, unsealed, kind = gen.claim_details(reason, swap)
                        claim = {**claim, "reason": reason, "text": text, "evidence": evidence,
                                 "unsealed": unsealed, "type": kind}  # fmt: skip
                        counts["claim_changed_crx"] += 1
                    status, _ = self.call("POST", "/api/shop/claims", {
                        "buyer_id": buyer, "customer_name": f"가상회원{buyer[4:]}",
                        "order_id": oid, "type": claim["type"], "reason": claim["reason"],
                        "items": claim["items"], "unsealed": claim["unsealed"],
                        "evidence_count": claim["evidence"], "description": claim["text"],
                    })  # fmt: skip
                    _retry_if(status, "클레임")
                    done[key] = _stamp(now)
                    counts["claims" if status == 201 else "claim_refused"] += 1
                    actions += 1
                    if status == 201:
                        by_item = {i["order_item_id"]: i["product_id"] for i in order["items"]}
                        for item in claim["items"]:
                            pid = by_item.get(item["order_item_id"])
                            data["claims"].append([pid, _stamp(now), item["qty"], claim["reason"]])
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
                _retry_if(status, "후기")
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
                _retry_if(status, "문의")
                counts["inquiries" if status == 201 else "inquiry_refused"] += 1
            done[key] = _stamp(now)
            actions += 1
        return actions


def real_delivery_plan(order_id: str, paid_at: datetime) -> tuple[datetime, datetime]:
    """사람 주문의 발송·도착 시각. 옛 `shop_traffic.delivery_plan`과 같은 값(주문 번호로 정한다)."""
    rng = random.Random(order_id)  # noqa: S311 - 시연용 시각
    shipped = paid_at + timedelta(minutes=rng.randint(12 * 60, 30 * 60))
    arrived = shipped + timedelta(minutes=rng.randint(24 * 60, 48 * 60))
    return shipped, arrived


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
    parser.add_argument(
        "--restock-lead-h", type=float, default=DEFAULT_RESTOCK_LEAD_H,
        help="기준 아래 재고를 채우기 전에 두는 시간(음수면 채우지 않는다)",
    )  # fmt: skip
    parser.add_argument(
        "--no-deliver-real", action="store_true",
        help="사람 주문의 발송·도착 표시를 하지 않는다(기본은 shop_traffic처럼 적는다)",
    )  # fmt: skip
    args = parser.parse_args(argv)
    if args.interval_s <= 0 or args.max_per_tick < 1:
        parser.error("--interval-s는 0보다, --max-per-tick은 1 이상이어야 한다")
    token = admin_token()
    if not token:
        print("관리자 토큰이 없다(SHOP_ADMIN_TOKEN 또는 AX_AGENT_TOKENS의 admin)", file=sys.stderr)
        return 1
    call = http_caller(args.url, token)
    try:
        lock = InstanceLock(args.out / LOCK_FILE)
    except DailyError as error:
        print(error, file=sys.stderr)
        return 3
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

    lead = None if args.restock_lead_h < 0 else args.restock_lead_h
    daily = Daily(
        call, model, args.out, interval_s=args.interval_s, max_per_tick=args.max_per_tick,
        log=log, restock_lead_h=lead, deliver_real=not args.no_deliver_real,
    )  # fmt: skip
    log(f"가상 하루 흐름 시작: 회원 {len(model.members):,}명, {args.interval_s:g}초마다")
    while True:
        try:
            done = daily.tick()
            if any(done.values()):
                log("이번 차례: " + ", ".join(f"{k}={v}" for k, v in done.items() if v))
        except (ConnectionError, OSError, urllib.error.URLError) as error:
            log(f"판매 서버 오류(다음 차례에 다시): {error}")
        if args.once:
            lock.release()
            return 0
        time.sleep(args.interval_s)


if __name__ == "__main__":
    raise SystemExit(main())
