"""캣북스 가상 회원·주문 넣기 (1단계: AXcellent 연결 시험용 데이터).

공식 통계로 정한 페르소나 15개에서 회원(기본 5만 명)을 만들고, 회원마다 구매 습관대로 90일 동안
산 결과를 판매 서버 DB에 바로 넣는다. 하루 주문 수는 정해 두지 않는다: 회원 각자의 주문(하루마다
포아송, 요일·시간대·월·월급날·추석·AI 도서 트렌드 효과)을 더한 결과다. 설계 근거는 팀 설계 문서
"캣북스 가상 주문 데이터"(2026-11-16)의 2~9절이다.

    cd backend
    SHOP_DB_PATH=.data/shop.db uv run python scripts/gen_synthetic.py --dry-run
    SHOP_DB_PATH=.data/shop.db uv run python scripts/gen_synthetic.py [--members 50000]
        [--seed 20261115] [--end 2026-11-15] [--trend-start 2026-10-08] [--out ./synthetic-out]
        [--replace]

- 기간: 끝 날짜(`--end`, 기본 오늘(한국 시간) 하루 전)까지 90일. 모든 시각은 과거이고 마지막 1시간
  안의 기록은 없다(결제 감시가 반응하지 않게).
- 행을 직접 넣는다. 서비스 경로(주문·결제·환불)를 거치지 않아 outbox 이벤트가 하나도 생기지 않는다
  (넣기 전후 outbox 건수가 다르면 되돌린다).
- 가상 회원은 `buy_100001`부터(`ax_shop_api.synthetic_ids`), 이메일은 `.invalid` 도메인, 비밀번호는
  아무도 모르는 값의 scrypt 해시 하나를 같이 쓴다(로그인할 수 없다).
- 이미 가상 회원이 있으면 멈춘다. `--replace`는 가상 회원 구간의 행과 그에 딸린 행만 지우고(재고는
  되돌린다) 다시 넣는다. 지우기·넣기가 한 트랜잭션이다.
- 정답지(`answer_key.json`)와 회원 표(`member_personas.csv`)는 `--out`(기본 `./synthetic-out`,
  커밋 안 함)에만 쓴다. DB·저장소에는 쓰지 않는다. `--dry-run`은 계산만 하고 아무것도 쓰지 않는다.
- 환불은 만들지 않는다. 판매 서버 규칙상 환불은 고객지원이 클레임을 판정한 뒤에만 한다. 그래서
  클레임은 모두 "접수됨"(AXcellent가 읽어 갈 차례)으로 넣는다. 취소는 결제 전 취소만 있다
  (결제된 주문은 환불 API 없이 취소하지 않는다).
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import random
import re
import secrets
import sqlite3
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ax_shop_api import synthetic_ids
from ax_shop_api.accounts import AGE_BANDS, GENDERS, OCCUPATIONS, hash_password
from ax_shop_api.claim_inbox import digest as claim_digest
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.genres import GENRES
from ax_shop_api.service import FREE_SHIPPING_FROM, PROMISED_DELIVERY_DAYS, SHIPPING_FEE
from ax_shop_api.support_client import CLAIM_CONTEXT_PREFIX

KST = timezone(timedelta(hours=9))
WINDOW_DAYS = 90
DEFAULT_SEED = 20261115
DEFAULT_MEMBERS = 50_000
DEFAULT_OUT = Path("synthetic-out")
TREND_LEAD_DAYS = 38  # 트렌드 시작 = 끝 날짜 − 38일 (11/15 끝이면 10/8)
NEW_MEMBER_SHARE = 0.10  # 기간 안에 가입한 회원
JITTER = 0.30  # 회원마다 ±30%
STAMP = "%Y-%m-%dT%H:%M:%SZ"

# --- 시간의 모양 (설계 3절) ----------------------------------------------------------------
# 월별 온라인 서적 거래액(국가데이터처, 2024·2025 평균, 억 원). 평균을 1로 맞춰 쓴다
MONTH_SALES = (2333.5, 2198, 2770, 1938, 1954.5, 1716, 2280, 2159, 2124, 1873, 2024.5, 2424)
MONTH_INDEX = {m + 1: v / (sum(MONTH_SALES) / 12) for m, v in enumerate(MONTH_SALES)}
# 요일 결제 비중(%), 월~일 (현대카드 스마일카드 2020). 평균을 1로 맞춰 쓴다
DOW_SHARE = (17, 19, 17, 13, 12, 10, 12)
DOW_INDEX = tuple(v / (100 / 7) for v in DOW_SHARE)
# 시간대(한국 시간 0~23시) 비중: 10시 5.8%·23시 6.3%(실제 값), 나머지는 잇는 곡선(가정)
HOUR_SHARE = (
    2.2,
    1.4,
    0.9,
    0.6,
    0.5,
    0.6,
    1.1,
    2.0,
    3.2,
    4.6,
    5.8,
    5.4,
    5.0,
    4.7,
    4.3,
    4.1,
    4.0,
    4.0,
    4.2,
    4.6,
    5.1,
    5.6,
    6.0,
    6.3,
)
PAYDAY, PAYDAY_FACTOR = 26, 1.15  # 월급 다음 날(26일) +15%
CHUSEOK_FACTOR = 0.75  # 추석 연휴가 낀 주(월~일) −25%
CHUSEOK = {  # 추석 연휴(한국 시간). 기간 안에 들 때만 효과가 있다
    2025: (date(2025, 10, 5), date(2025, 10, 7)),
    2026: (date(2026, 9, 24), date(2026, 9, 26)),
    2027: (date(2027, 9, 14), date(2027, 9, 16)),
}

# --- 트렌드 시나리오 (설계 6절) -----------------------------------------------------------
TREND_GENRE = "it"
TREND_PEAK = 4.1  # 3~4주 차 최고 배수(교보 AI 도서 +310%)
SPILLOVER = {"business": 0.20, "self_help": 0.10}  # 최고점에서 번지는 몫
TREND_LIFTS = {TREND_GENRE: TREND_PEAK - 1, **SPILLOVER}
AI_TITLE = re.compile(r"AI|인공지능|챗GPT|ChatGPT|GPT|생성형|머신러닝|딥러닝|LLM|프롬프트", re.I)

# --- 무엇이 팔리나 (설계 5절): 평상시 책 장르 비중(%) -------------------------------------
BOOK_SHARES = {
    "novel": 28.0,
    "business": 13.9,
    "essay": 13.0,
    "hobby": 8.8,
    "humanities": 6.6,
    "kids": 6.0,
    "self_help": 4.4,
    "comics": 4.0,
    "history": 3.5,
    "language": 3.5,
    "society": 2.5,
    "it": 2.9,
    "science": 2.9,
}
STATIONERY_GENRES = ("writing", "notebook", "deco")
BOOK_GENRES = tuple(g for g in GENRES if g not in STATIONERY_GENRES)
assert set(BOOK_SHARES) == set(BOOK_GENRES), "BOOK_SHARES는 genres.py 책 장르와 같아야 한다"
LIKE_BOOST = (4.0, 3.0, 2.0)  # 좋아하는 분야 1·2·3순위 가중
STATIONERY_ADDON = 0.14  # 주문에 문구를 함께 담는 몫(문구 좋아하는 유형 포함 전체 약 15%)
STATIONERY_MIX = {"writing": 0.35, "notebook": 0.35, "deco": 0.30}
BOOK_LINES = ((1, 0.70), (2, 0.22), (3, 0.06), (4, 0.02))  # 책 줄 수(문구·한 권 더 담기 전)
QTY_TWO = 0.05  # 같은 책 두 권
NUDGE_FROM, NUDGE_P = 38_000, 0.40  # 무료배송 기준 바로 아래면 한 개 더 담는다
# 장르 안 인기: 평소에는 재고에 비례(서점은 팔리는 만큼 들여 둔다)하고 순위로 조금 기운다.
# 트렌드 때 AI·IT는 앞 순위·AI 제목으로 쏠린다(인기 AI 책이 품절된다)
POPULARITY_EXP, TREND_POPULARITY_EXP, AI_TITLE_BOOST = 0.35, 1.6, 3.0

# --- 주문 뒤의 일 (설계 9절 가정) ---------------------------------------------------------
CANCEL_BEFORE_PAY = 0.018  # 결제 전 취소
PAY_DECLINE = 0.025  # 첫 결제 거절(PG_DECLINED)
DECLINE_RETRY_OK, DECLINE_CANCEL = 0.55, 0.25  # 거절 뒤 다시 결제 성공·취소(나머지는 실패로 남음)
METHODS = (("card", 0.70), ("easy_pay", 0.20), ("transfer", 0.08), ("phone", 0.02))
SHIP_HOURS, DELIVER_HOURS, LATE_HOURS = (12, 30), (20, 44), (60, 120)  # 약속은 결제 + 3일
LATE_BASE, LATE_TREND = 0.04, 0.04  # 늦은 배송(트렌드 최고점에서 +4%p)
CLAIM_RATE = 0.03  # 배송 완료 주문의 클레임
REVIEW_RATE = 0.07  # 배송 완료 품목의 후기
INQUIRY_RATE = 0.02  # 주문의 문의(재입고 문의는 따로)
RESTOCK_INQUIRY_P = 0.35  # 트렌드 책 품절을 겪은 회원이 재입고를 묻는 몫
CLAIM_REASONS = (  # 사유 코드(판매 서버 ClaimIn 그대로)
    ("change_of_mind", 0.32),
    ("defect", 0.18),
    ("damaged", 0.14),
    ("wrong_item", 0.10),
    ("missing_item", 0.08),
    ("late_delivery", 0.08),
    ("other", 0.10),
)
REASON_CODES = tuple(r for r, _ in CLAIM_REASONS)
DEFECT_REASONS = ("defect", "damaged", "wrong_item", "missing_item")  # 고객지원 defect_count
EVIDENCE_REASONS = ("defect", "damaged", "wrong_item")  # 화면이 사진을 요구하는 사유
# CRX-001(재무)·book_issue_stats(고객지원)가 보는 기준
CRX_MIN_SOLD, CRX_MIN_RATE, CRX_MIN_CLAIMS, CRX_MIN_DEFECTS = 5, 0.10, 2, 3
MIN_GROUP_BUYERS = 5  # 판매 집계가 숫자를 숨기는 묶음 크기(lookups.MIN_GROUP_BUYERS)


@dataclass(frozen=True)
class Persona:
    code: str
    name: str
    share: float  # 회원 비중(%)
    yearly: float  # 연 주문 수
    likes: tuple[str, ...]
    hours: str
    sensitivity: (
        float  # AI 트렌드 반응(없음 0 · 낮음 0.2 · 중간 0.5 · 높음 1.0 · 아주 높음 1.6~1.8)
    )
    ages: tuple[tuple[str | None, float], ...]
    genders: tuple[tuple[str | None, float], ...]
    occupation: str | None
    weekend: bool = False
    addon: float = STATIONERY_ADDON
    stationery_lines: float = 0.0  # 책 대신 문구를 고르는 줄의 몫(문구 수집가)


def _one(value: str | None) -> tuple[tuple[str | None, float], ...]:
    return ((value, 1.0),)


PERSONAS: tuple[Persona, ...] = (
    Persona(
        "book_lover",
        "대학생 책벌레",
        8,
        0.82,
        ("novel", "essay", "self_help"),
        "night23",
        0.5,
        _one("20s"),
        _one("female"),
        "student",
    ),
    Persona(
        "job_seeker",
        "취업 준비생",
        5,
        0.65,
        ("self_help", "it", "business"),
        "afternoon_night",
        1.0,
        _one("20s"),
        _one("male"),
        "student",
    ),
    Persona(
        "new_worker",
        "사회초년생",
        7,
        0.73,
        ("novel", "essay"),
        "after_work",
        0.2,
        _one("20s"),
        _one("female"),
        "employee",
        addon=0.30,
    ),
    Persona(
        "worker_m30",
        "30대 직장인 남",
        10,
        0.73,
        ("business", "it", "self_help"),
        "am10_lunch",
        1.0,
        _one("30s"),
        _one("male"),
        "employee",
    ),
    Persona(
        "worker_f30",
        "30대 직장인 여",
        10,
        0.73,
        ("novel", "essay", "hobby"),
        "night",
        0.2,
        _one("30s"),
        _one("female"),
        "employee",
    ),
    Persona(
        "parent",
        "육아하는 부모",
        7,
        0.65,
        ("kids", "hobby", "novel"),
        "late_night",
        0.2,
        (("30s", 0.6), ("40s", 0.4)),
        _one("female"),
        "other",
    ),
    Persona(
        "worker_m40",
        "40대 직장인 남",
        9,
        0.57,
        ("business", "it", "history"),
        "am10",
        1.6,
        _one("40s"),
        _one("male"),
        "employee",
    ),
    Persona(
        "worker_f40",
        "40대 직장인 여",
        6,
        0.57,
        ("novel", "essay", "humanities"),
        "night",
        0.2,
        _one("40s"),
        _one("female"),
        "employee",
    ),
    Persona(
        "self_employed",
        "자영업자",
        5,
        0.49,
        ("business", "self_help", "hobby"),
        "morning",
        1.8,
        (("40s", 0.55), ("50s_plus", 0.45)),
        _one("male"),
        "self_employed",
    ),
    Persona(
        "man50",
        "50대 남",
        6,
        0.49,
        ("humanities", "history", "business"),
        "morning",
        1.0,
        _one("50s_plus"),
        _one("male"),
        "employee",
    ),
    Persona(
        "woman50",
        "50대 이상 여",
        8,
        0.49,
        ("novel", "essay", "hobby"),
        "day",
        0.2,
        _one("50s_plus"),
        _one("female"),
        "other",
    ),
    Persona(
        "retired",
        "은퇴 세대",
        5,
        0.24,
        ("humanities", "essay", "society"),
        "morning",
        0.03,
        _one("50s_plus"),
        (("female", 0.5), ("male", 0.5)),
        "no_answer",
    ),
    Persona(
        "teen",
        "10대 학생",
        4,
        0.73,
        ("comics", "novel", "language"),
        "night_weekend",
        0.5,
        _one("10s"),
        (("female", 0.5), ("male", 0.5)),
        "student",
        weekend=True,
    ),
    Persona(
        "stationery_fan",
        "문구 수집가",
        4,
        0.98,
        ("novel",),
        "night",
        0.0,
        (("20s", 0.5), ("30s", 0.5)),
        _one("female"),
        "employee",
        addon=0.0,
        stationery_lines=0.55,
    ),
    Persona("blank", "프로필 비움", 6, 0.49, (), "even", 0.5, _one(None), _one(None), None),
)
# 판매 집계의 "5명 미만 숨기기"를 시험하는 작은 묶음(50대 이상 여성 학생 × 외국어). 3명만 둔다
SMALL_GROUP = Persona(
    "late_student",
    "만학도(작은 묶음 확인용)",
    0,
    0.49,
    ("language", "humanities"),
    "day",
    0.0,
    _one("50s_plus"),
    _one("female"),
    "student",
)
SMALL_GROUP_SIZE = 3
assert abs(sum(p.share for p in PERSONAS) - 100) < 1e-9
for _p in (*PERSONAS, SMALL_GROUP):  # 코드는 accounts.py 값 그대로(비움은 None)
    assert all(a is None or a in AGE_BANDS for a, _ in _p.ages), _p.code
    assert all(g is None or g in GENDERS for g, _ in _p.genders), _p.code
    assert _p.occupation is None or _p.occupation in OCCUPATIONS, _p.code
    assert all(g in BOOK_GENRES for g in _p.likes), _p.code

HOUR_TILTS: dict[str, dict[int, float]] = {
    "night23": {21: 1.6, 22: 2.0, 23: 2.4, 0: 1.6},
    "afternoon_night": {13: 1.5, 14: 1.5, 15: 1.5, 16: 1.4, 20: 1.4, 21: 1.6, 22: 1.6, 23: 1.5},
    "after_work": {19: 1.8, 20: 2.0, 21: 2.0, 22: 1.8, 23: 1.5},
    "am10_lunch": {9: 1.6, 10: 2.0, 11: 1.8, 12: 2.0, 13: 1.6},
    "night": {20: 1.6, 21: 1.8, 22: 2.0, 23: 2.0, 0: 1.4},
    "late_night": {21: 1.8, 22: 2.2, 23: 2.4, 0: 2.0, 1: 1.5},
    "am10": {9: 1.8, 10: 2.4, 11: 1.8},
    "morning": {7: 1.6, 8: 1.8, 9: 2.0, 10: 2.0, 11: 1.6},
    "day": {9: 1.5, 10: 1.6, 11: 1.5, 13: 1.4, 14: 1.5, 15: 1.5, 16: 1.4},
    "night_weekend": {20: 1.6, 21: 1.8, 22: 2.0, 23: 1.8},
    "even": {},
}


def _persona_weights() -> dict[str, dict[str, float]]:
    """유형별 책 장르 가중치. 좋아하는 분야를 올린 뒤, 회원 전체 합이 `BOOK_SHARES`가 되게
    장르마다 같은 보정값을 곱한다(반복 비례 맞춤). 유형 사이의 차이는 그대로 남는다."""
    every = (*PERSONAS, SMALL_GROUP)

    def raw(p: Persona, fix: dict[str, float]) -> dict[str, float]:
        w = {g: BOOK_SHARES[g] * fix[g] for g in BOOK_GENRES}
        for rank, genre in enumerate(p.likes):
            w[genre] *= LIKE_BOOST[min(rank, len(LIKE_BOOST) - 1)]
        total = sum(w.values())
        return {g: v / total for g, v in w.items()}

    fix = dict.fromkeys(BOOK_GENRES, 1.0)
    for _ in range(60):
        agg = dict.fromkeys(BOOK_GENRES, 0.0)
        for p in PERSONAS:
            for g, v in raw(p, fix).items():
                agg[g] += p.share * p.yearly * (1 - p.stationery_lines) * v
        total = sum(agg.values())
        for g in BOOK_GENRES:
            fix[g] *= (BOOK_SHARES[g] / 100) / (agg[g] / total)
    return {p.code: raw(p, fix) for p in every}


PERSONA_WEIGHTS = _persona_weights()


# --- 자료 모양 ------------------------------------------------------------------------------
@dataclass
class Product:
    product_id: str
    seller_id: str
    name: str
    category: str
    price: int
    stock: int
    genre: str | None
    ai_title: bool = False


@dataclass
class Member:
    index: int
    buyer_id: str
    persona: Persona
    age_band: str | None
    gender: str | None
    occupation: str | None
    joined: datetime
    join_day: int  # 기간 안 가입이면 그날 번호, 기간 전 가입이면 −1
    yearly: float
    weights: dict[str, float]
    sensitivity: float
    hour_strength: float
    trend_coef: float = 0.0
    orders: list[Order] = field(default_factory=list)


@dataclass
class Line:
    product: Product
    qty: int
    rowid: int | None = None


@dataclass
class Payment:
    payment_id: str
    status: str
    failure_code: str | None
    created: datetime
    method: str


@dataclass
class Order:
    order_id: str
    member: Member
    created: datetime
    day: int
    lines: list[Line]
    status: str = "pending_payment"
    payments: list[Payment] = field(default_factory=list)
    updated: datetime | None = None
    paid: datetime | None = None
    promised: datetime | None = None
    shipped: datetime | None = None
    delivered: datetime | None = None
    late: bool = False
    forced: bool = False

    @property
    def subtotal(self) -> int:
        return sum(line.qty * line.product.price for line in self.lines)

    @property
    def shipping_fee(self) -> int:
        return 0 if self.subtotal >= FREE_SHIPPING_FROM else SHIPPING_FEE

    @property
    def total(self) -> int:
        return self.subtotal + self.shipping_fee


@dataclass
class Claim:
    order: Order
    lines: list[int]
    type: str
    reason: str
    created: datetime
    unsealed: bool
    evidence: int
    description: str
    planted: str | None = None


@dataclass
class Review:
    review_id: str
    order: Order
    line: int
    rating: int
    body: str
    created: datetime


@dataclass
class Inquiry:
    order: Order
    kind: str
    subject: str
    message: str
    created: datetime


@dataclass
class Stockout:
    at: datetime
    product: Product
    member: Member
    trend: bool
    inquiry: bool = False


@dataclass
class Config:
    members: int = DEFAULT_MEMBERS
    seed: int = DEFAULT_SEED
    end: date | None = None
    trend_start: date | None = None
    now: datetime | None = None
    replace: bool = False
    dry_run: bool = False


@dataclass
class Window:
    start: date
    end: date
    start_utc: datetime
    end_utc: datetime  # 끝 날짜 다음 날 0시(한국 시간)
    cutoff: datetime  # 이 시각 뒤의 기록은 만들지 않는다
    trend_start: date
    days: list[date]
    factor: list[float]  # 날마다 월·요일·월급날·추석 효과
    weekend_factor: list[float]  # 주말에 더 사는 유형(10대)
    trend: list[float]  # 트렌드 배수 − 1
    chuseok_week: tuple[date, date] | None

    def day_of(self, moment: datetime) -> int:
        return (moment.astimezone(KST).date() - self.start).days


class SyntheticExistsError(Exception):
    """가상 회원이 이미 있다(`--replace`를 주면 지우고 다시 넣는다)."""


class Rng(random.Random):
    def jitter(self) -> float:
        return self.uniform(1 - JITTER, 1 + JITTER)

    def pick(self, pairs: Any) -> Any:
        """`(값, 가중치)` 묶음에서 하나."""
        pairs = list(pairs)
        total = sum(w for _, w in pairs)
        target = self.random() * total
        for value, weight in pairs:
            target -= weight
            if target < 0:
                return value
        return pairs[-1][0]

    def poisson(self, lam: float) -> int:
        if lam <= 0:
            return 0
        limit, k, p = math.exp(-lam), 0, 1.0
        while True:
            p *= self.random()
            if p <= limit:
                return k
            k += 1


def _stamp(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(UTC).strftime(STAMP)


def _kst_midnight(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=KST).astimezone(UTC)


def trend_multiple(days_since: float) -> float:
    """트렌드 시작 뒤 경과 일수 → AI·IT 주문 배수. 1주 ×1.5 → 2주 ×3 → 3~4주 ×4.1 → 반감 2주."""
    if days_since < 0:
        return 1.0
    w = days_since / 7
    if w < 1:
        return 1 + 0.5 * w
    if w < 2:
        return 1.5 + 1.5 * (w - 1)
    if w < 2.6:
        return 3 + (TREND_PEAK - 3) * (w - 2) / 0.6
    if w < 4:
        return TREND_PEAK
    return 1 + (TREND_PEAK - 1) * 0.5 ** ((w - 4) / 2)


def default_end(now: datetime) -> date:
    """기본 끝 날짜: 만드는 날(한국 시간)의 하루 전."""
    return now.astimezone(KST).date() - timedelta(days=1)


def chuseok_week(start: date, end: date) -> tuple[date, date] | None:
    """기간 안에 추석 연휴가 낀 주(월~일). 없으면 None."""
    for first, last in CHUSEOK.values():
        monday = first - timedelta(days=first.weekday())
        sunday = monday + timedelta(days=6)
        if monday <= end and sunday >= start and first <= last:
            return monday, sunday
    return None


def build_window(config: Config, now: datetime) -> Window:
    end = config.end or default_end(now)
    if end >= now.astimezone(KST).date():
        raise ValueError("끝 날짜(--end)는 어제(한국 시간)나 그 전이어야 해요")
    start = end - timedelta(days=WINDOW_DAYS - 1)
    trend_start = config.trend_start or end - timedelta(days=TREND_LEAD_DAYS)
    if not start <= trend_start <= end:
        raise ValueError("트렌드 시작(--trend-start)은 기간 안이어야 해요")
    days = [start + timedelta(days=i) for i in range(WINDOW_DAYS)]
    week = chuseok_week(start, end)
    factor = []
    for day in days:
        value = MONTH_INDEX[day.month] * DOW_INDEX[day.weekday()]
        if day.day == PAYDAY:
            value *= PAYDAY_FACTOR
        if week and week[0] <= day <= week[1]:
            value *= CHUSEOK_FACTOR
        factor.append(value)
    tilt = [1, 1, 1, 1, 1, 1.6, 1.6]
    norm = sum(d * t for d, t in zip(DOW_INDEX, tilt, strict=True)) / 7
    weekend = [f * tilt[d.weekday()] / norm for f, d in zip(factor, days, strict=True)]
    trend = [trend_multiple((d - trend_start).days + 0.5) - 1 for d in days]
    end_utc = _kst_midnight(end + timedelta(days=1))
    cutoff = min(end_utc, now - timedelta(hours=1))
    return Window(
        start,
        end,
        _kst_midnight(start),
        end_utc,
        cutoff,
        trend_start,
        days,
        factor,
        weekend,
        trend,
        week,
    )


def _holidays() -> set[date]:
    out: set[date] = set()
    for first, last in CHUSEOK.values():
        out |= {first + timedelta(days=i) for i in range((last - first).days + 1)}
    return out


HOLIDAYS = _holidays()


# --- DB 읽기·지우기 ------------------------------------------------------------------------
_SYN_ORDERS = f"SELECT order_id FROM orders WHERE {synthetic_ids.BUYER_WHERE}"  # noqa: S608


def synthetic_count(conn: sqlite3.Connection) -> int:
    sql = f"SELECT COUNT(*) FROM buyers WHERE {synthetic_ids.BUYER_WHERE}"  # noqa: S608
    return conn.execute(sql).fetchone()[0]


def synthetic_stock(conn: sqlite3.Connection) -> dict[str, int]:
    """가상 주문이 가져간 재고(취소된 주문은 이미 되돌려졌다). 지울 때 상품별로 되돌린다."""
    rows = conn.execute(
        "SELECT i.product_id, SUM(i.qty) AS qty FROM order_items i "
        f"WHERE i.order_id IN ({_SYN_ORDERS} AND status != 'cancelled') "  # noqa: S608
        "GROUP BY i.product_id"
    ).fetchall()
    return {r["product_id"]: r["qty"] for r in rows}


def delete_synthetic(conn: sqlite3.Connection) -> dict[str, int]:
    """가상 회원 구간의 행과 딸린 행만 지우고 재고를 되돌린다. 지운 건수를 돌려준다."""
    for product_id, qty in synthetic_stock(conn).items():
        conn.execute(
            "UPDATE products SET stock = stock + ? WHERE product_id = ?", (qty, product_id)
        )
    where = synthetic_ids.BUYER_WHERE
    steps = (
        (
            "return_sync",
            f"claim_id IN (SELECT claim_id FROM returns WHERE order_id IN ({_SYN_ORDERS}))",
        ),
        ("returns", f"order_id IN ({_SYN_ORDERS})"),
        ("refunds", f"order_id IN ({_SYN_ORDERS})"),
        (
            "pg_fake_records",
            f"payment_id IN (SELECT payment_id FROM payments WHERE order_id IN ({_SYN_ORDERS}))",
        ),
        ("payment_attempts", f"order_id IN ({_SYN_ORDERS})"),
        ("payments", f"order_id IN ({_SYN_ORDERS})"),
        ("order_items", f"order_id IN ({_SYN_ORDERS})"),
        ("reviews", where),
        ("claim_inbox", where),
        ("inquiry_inbox", where),
        ("idea_inbox", where),
        ("sessions", where),
        ("accounts", where),
        ("orders", where),
        ("coupons", where),
        ("buyers", where),
    )
    counts = {}
    for table, condition in steps:
        counts[table] = conn.execute(f"DELETE FROM {table} WHERE {condition}").rowcount  # noqa: S608
    return counts


def load_products(conn: sqlite3.Connection, replace: bool, dry_run: bool) -> list[Product]:
    rows = conn.execute(
        "SELECT product_id, seller_id, name, category_code, price, stock, genre_code "
        "FROM products WHERE active = 1 ORDER BY product_id"
    ).fetchall()
    back = synthetic_stock(conn) if (replace and dry_run) else {}  # 계산만: 지운 셈 친다
    return [
        Product(
            r["product_id"],
            r["seller_id"],
            r["name"],
            r["category_code"],
            r["price"],
            r["stock"] + back.get(r["product_id"], 0),
            r["genre_code"],
            bool(AI_TITLE.search(r["name"] or "")),
        )
        for r in rows
    ]


def _counter(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
    return row[0] if row else 0


# --- 상품 고르기 ----------------------------------------------------------------------------
class Shelf:
    """장르별 상품 묶음과 인기 가중치. 장르 안 순위는 시드로 섞어 정한다(앞 순위가 많이 팔린다)."""

    def __init__(self, products: list[Product], rng: Rng) -> None:
        self.products = products
        self.by_genre: dict[str, list[Product]] = defaultdict(list)
        for p in products:
            if p.genre in GENRES:
                self.by_genre[p.genre].append(p)
        self.untagged_books = [p for p in products if p.genre is None and p.category == "book"]
        tagged_books = [p for g in BOOK_GENRES for p in self.by_genre.get(g, [])]
        self.book_fallback = self.untagged_books or tagged_books or list(products)
        self.stationery_fallback = [
            p for p in products if p.genre is None and p.category == "stationery"
        ] or [p for g in STATIONERY_GENRES for p in self.by_genre.get(g, [])]
        self.pools: dict[str, tuple[list[Product], list[float], list[float]]] = {}
        for key, pool in [
            *self.by_genre.items(),
            ("_book", self.book_fallback),
            ("_stationery", self.stationery_fallback),
        ]:
            order = list(pool)
            rng.shuffle(order)
            base = [max(1, p.stock) / (rank + 1) ** POPULARITY_EXP for rank, p in enumerate(order)]
            hot = [
                (1 / (rank + 1) ** TREND_POPULARITY_EXP) * (AI_TITLE_BOOST if p.ai_title else 1.0)
                for rank, p in enumerate(order)
            ]
            self.pools[key] = (order, _cumulative(base), _cumulative(hot))
        by_price = sorted(products, key=lambda p: (p.price, p.product_id))
        self.by_price = by_price
        self.prices = [p.price for p in by_price]
        self.fallback_lines = 0

    def pool_key(self, genre: str) -> str:
        if self.by_genre.get(genre):
            return genre
        self.fallback_lines += 1
        return "_stationery" if genre in STATIONERY_GENRES else "_book"

    def pick(self, genre: str, rng: Rng, hot: float) -> Product | None:
        key = self.pool_key(genre)
        order, base, trend = self.pools[key]
        if not order:
            return None
        cumulative = trend if (genre == TREND_GENRE and rng.random() < hot) else base
        return order[bisect.bisect_right(cumulative, rng.random() * cumulative[-1])]

    def filler(self, gap: int, rng: Rng) -> Product | None:
        """무료배송까지 모자란 금액 이상인 싼 상품 중 하나."""
        start = bisect.bisect_left(self.prices, gap)
        choices = self.by_price[start : start + 30]
        return rng.choice(choices) if choices else None


def _cumulative(weights: list[float]) -> list[float]:
    out, total = [], 0.0
    for w in weights:
        total += w
        out.append(total)
    return out


def _hour_weights(profile: str, strength: float) -> list[float]:
    tilt = HOUR_TILTS[profile]
    return _cumulative([HOUR_SHARE[h] * (1 + (tilt.get(h, 1.0) - 1) * strength) for h in range(24)])


# --- 회원 ------------------------------------------------------------------------------------
def make_members(count: int, window: Window, rng: Rng) -> list[Member]:
    personas = [(p, p.share) for p in PERSONAS]
    members: list[Member] = []
    signup_hours: dict[str, list[float]] = {}
    small_from = count - SMALL_GROUP_SIZE if count >= 50 else count
    for index in range(count):
        persona = SMALL_GROUP if index >= small_from else rng.pick(personas)
        age = rng.pick(persona.ages)
        gender = rng.pick(persona.genders)
        new = index < small_from and rng.random() < NEW_MEMBER_SHARE
        hours = signup_hours.get(persona.hours) or signup_hours.setdefault(
            persona.hours, _hour_weights(persona.hours, 1.0)
        )
        hour = min(23, bisect.bisect_right(hours, rng.random() * hours[-1]))
        if new:
            join_day = rng.randrange(WINDOW_DAYS)
            day = window.days[join_day]
            joined = datetime(
                day.year, day.month, day.day, hour, rng.randrange(60), rng.randrange(60), tzinfo=KST
            ).astimezone(UTC)
            if joined > window.cutoff:
                joined = window.cutoff - timedelta(hours=1 + rng.random() * 20)
                join_day = window.day_of(joined)
        else:
            join_day = -1
            back = timedelta(days=rng.uniform(1, 1095), hours=rng.uniform(0, 23))
            joined = window.start_utc - back
        weights = {g: v * rng.jitter() for g, v in PERSONA_WEIGHTS[persona.code].items()}
        total = sum(weights.values())
        weights = {g: v / total for g, v in weights.items()}
        activity = min(8.0, max(0.05, rng.gammavariate(1.0, 1.0)))  # 상위 20%가 주문의 절반쯤
        members.append(
            Member(
                index=index,
                buyer_id=synthetic_ids.buyer_id(index),
                persona=persona,
                age_band=age,
                gender=gender,
                occupation=persona.occupation,
                joined=joined.replace(microsecond=0),
                join_day=join_day,
                yearly=persona.yearly * rng.jitter() * activity,
                weights=weights,
                sensitivity=persona.sensitivity * rng.jitter(),
                hour_strength=rng.jitter(),
            )
        )
    return members


def calibrate_trend(members: list[Member]) -> dict[str, float]:
    """장르마다 회원 전체의 반응이 설계 배수가 되도록 나눌 값(수요 가중 평균 민감도)."""
    norms = {}
    for genre in TREND_LIFTS:
        demand = sum(m.yearly * m.weights[genre] for m in members)
        sens = sum(m.yearly * m.weights[genre] * m.sensitivity for m in members)
        norms[genre] = sens / demand if sens > 0 and demand > 0 else 1.0
    for m in members:
        m.trend_coef = sum(
            m.weights[g] * lift * m.sensitivity / norms[g] for g, lift in TREND_LIFTS.items()
        ) / (TREND_PEAK - 1)
    return norms


def genre_boosts(member: Member, trend: float, norms: dict[str, float]) -> dict[str, float]:
    """그날 회원의 장르 가중치(트렌드 반영)."""
    w = dict(member.weights)
    if trend > 0:
        scale = trend / (TREND_PEAK - 1)
        for genre, lift in TREND_LIFTS.items():
            w[genre] *= 1 + lift * scale * member.sensitivity / norms[genre]
    return w


def order_times(members: list[Member], window: Window, rng: Rng) -> list[tuple[datetime, int]]:
    """회원마다 포아송으로 주문 수를 뽑고 날·시각을 정한다(정렬된 (시각, 회원 번호) 목록)."""
    prefix: dict[bool, tuple[list[float], list[float]]] = {}
    for weekend in (False, True):
        factor = window.weekend_factor if weekend else window.factor
        f_pre, g_pre = [0.0], [0.0]
        for f, t in zip(factor, window.trend, strict=True):
            f_pre.append(f_pre[-1] + f)
            g_pre.append(g_pre[-1] + f * t)
        prefix[weekend] = (f_pre, g_pre)
    hour_cache: dict[tuple[str, float], list[float]] = {}
    latest = window.cutoff - timedelta(minutes=30)
    out: list[tuple[datetime, int]] = []
    for m in members:
        if m.persona is SMALL_GROUP:  # 작은 묶음은 기간 앞쪽에 한 건씩(외국어 책 한 권)
            d = window.days[rng.randrange(5, 60)]
            moment = datetime(
                d.year, d.month, d.day, rng.randrange(9, 18), rng.randrange(60), tzinfo=KST
            ).astimezone(UTC)
            out.append((moment, m.index))
            continue
        f_pre, g_pre = prefix[m.persona.weekend]
        first = max(0, m.join_day)
        f_part = f_pre[-1] - f_pre[first]
        g_part = (g_pre[-1] - g_pre[first]) * m.trend_coef
        lam = m.yearly / 365 * (f_part + g_part)
        for _ in range(rng.poisson(lam)):
            u = rng.random() * (f_part + g_part)
            if u < f_part:
                day = bisect.bisect_right(f_pre, f_pre[first] + u) - 1
            else:
                day = bisect.bisect_right(g_pre, g_pre[first] + (u - f_part) / m.trend_coef) - 1
            day = min(max(day, first), WINDOW_DAYS - 1)
            key = (m.persona.hours, round(m.hour_strength, 2))
            hours = hour_cache.get(key) or hour_cache.setdefault(key, _hour_weights(*key))
            hour = min(23, bisect.bisect_right(hours, rng.random() * hours[-1]))
            d = window.days[day]
            moment = datetime(
                d.year, d.month, d.day, hour, rng.randrange(60), rng.randrange(60), tzinfo=KST
            ).astimezone(UTC)
            if moment < m.joined:
                moment = m.joined + timedelta(minutes=rng.uniform(3, 90))
            if window.start_utc <= moment <= latest:
                out.append((moment.replace(microsecond=0), m.index))
    out.sort()
    return out


# --- 주문과 그 뒤 ----------------------------------------------------------------------------
@dataclass
class Simulation:
    window: Window
    members: list[Member]
    products: list[Product]
    shelf: Shelf
    orders: list[Order] = field(default_factory=list)
    stockouts: list[Stockout] = field(default_factory=list)
    dropped: int = 0  # 담은 상품이 모두 품절이라 만들지 못한 주문
    claims: list[Claim] = field(default_factory=list)
    reviews: list[Review] = field(default_factory=list)
    inquiries: list[Inquiry] = field(default_factory=list)
    planted: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    norms: dict[str, float] = field(default_factory=dict)
    stock_start: dict[str, int] = field(default_factory=dict)
    sold_out_at: dict[str, datetime] = field(default_factory=dict)
    claims_adjusted: int = 0  # 심지 않은 책이 걸리지 않게 사유를 바꾸거나 뺀 클레임


def _shift_holiday(moment: datetime, rng: Rng) -> datetime:
    """추석 연휴에는 발송·배송하지 않는다: 연휴 다음 날 오전으로 민다."""
    day = moment.astimezone(KST).date()
    if day not in HOLIDAYS:
        return moment
    while day in HOLIDAYS:
        day += timedelta(days=1)
    return datetime(day.year, day.month, day.day, 9, tzinfo=KST).astimezone(UTC) + timedelta(
        hours=rng.uniform(0, 9)
    )


def assemble_orders(
    sim: Simulation,
    times: list[tuple[datetime, int]],
    rng: Rng,
    life: Rng,
    next_order: int,
    next_payment: int,
) -> None:
    window, shelf = sim.window, sim.shelf
    stock = {p.product_id: p.stock for p in sim.products}
    sim.stock_start = dict(stock)
    asked: set[tuple[str, str]] = set()
    payment_no = next_payment
    for moment, index in times:
        member = sim.members[index]
        day = window.day_of(moment)
        trend = window.trend[day]
        weights = genre_boosts(member, trend, sim.norms)
        hot = min(1.0, trend / (TREND_PEAK - 1))
        wanted: list[Product] = []
        forced = member.persona is SMALL_GROUP

        for _ in range(0 if forced else rng.pick(BOOK_LINES)):
            if member.persona.stationery_lines and rng.random() < member.persona.stationery_lines:
                genre = rng.pick({"deco": 0.4, "notebook": 0.35, "writing": 0.25}.items())
            else:
                genre = rng.pick(weights.items())
            _add(wanted, shelf.pick(genre, rng, hot))
        if forced:
            _add(wanted, shelf.pick("language", rng, 0.0))
        elif rng.random() < member.persona.addon:
            _add(wanted, shelf.pick(rng.pick(STATIONERY_MIX.items()), rng, 0.0))
        subtotal = sum(p.price for p in wanted)
        if not forced and NUDGE_FROM <= subtotal < FREE_SHIPPING_FROM and rng.random() < NUDGE_P:
            _add(wanted, shelf.filler(FREE_SHIPPING_FROM - subtotal, rng))
        lines = []
        missed = []
        for product in wanted:
            qty = 2 if (not forced and rng.random() < QTY_TWO) else 1
            if stock[product.product_id] < qty:
                missed.append(product)
                continue
            lines.append(Line(product, qty))
        if lines:
            order = Order(
                f"ord_{next_order + len(sim.orders) + 1:04d}",
                member,
                moment,
                day,
                lines,
                forced=forced,
            )
            payment_no = _settle(order, window, life, payment_no)
            if order.status != "cancelled":  # 취소된 주문은 재고를 돌려놓았다
                for line in lines:
                    stock[line.product.product_id] -= line.qty
                    if stock[line.product.product_id] == 0:
                        sim.sold_out_at.setdefault(line.product.product_id, moment)
            sim.orders.append(order)
            member.orders.append(order)
        else:
            sim.dropped += 1
        for product in missed:
            trend_title = product.genre == TREND_GENRE and trend > 0
            out = Stockout(moment, product, member, trend_title)
            sim.stockouts.append(out)
            key = (member.buyer_id, product.product_id)
            if trend_title and key not in asked and life.random() < RESTOCK_INQUIRY_P:
                target = next((o for o in reversed(member.orders) if o.paid), None)
                if target is not None:
                    asked.add(key)
                    out.inquiry = True
                    _restock_inquiry(sim, target, product, moment, life)
    for p in sim.products:
        p.stock = stock[p.product_id]
    group = [m for m in sim.members if m.persona is SMALL_GROUP]
    if not group:
        sim.warnings.append(
            "회원이 50명 미만이라 '5명 미만 숨기기' 확인용 작은 묶음을 심지 않았어요"
        )
    elif any(not m.orders for m in group):
        sim.warnings.append("작은 묶음 회원 일부의 주문을 만들지 못했어요(외국어 책 재고 없음)")
    sim.planted["small_group"] = {
        "attributes": {
            "age_band": SMALL_GROUP.ages[0][0],
            "gender": SMALL_GROUP.genders[0][0],
            "occupation": SMALL_GROUP.occupation,
        },
        "buyers": [m.buyer_id for m in group if m.orders],
        "genre_code": "language" if shelf.by_genre.get("language") else None,
    }


def _add(wanted: list[Product], product: Product | None) -> None:
    if product is not None and product not in wanted:
        wanted.append(product)


def _settle(order: Order, window: Window, rng: Rng, payment_no: int) -> int:
    """결제·취소·발송·도착을 정한다. 다음 결제 번호를 돌려준다."""

    def pay(status: str, code: str | None, at: datetime) -> None:
        nonlocal payment_no
        payment_no += 1
        order.payments.append(Payment(f"pay_{payment_no:04d}", status, code, at, rng.pick(METHODS)))

    created = order.created
    if not order.forced and rng.random() < CANCEL_BEFORE_PAY:
        order.status = "cancelled"
        order.updated = created + timedelta(minutes=rng.uniform(2, 90))
        order.updated = min(order.updated, window.cutoff)
        return payment_no
    first = created + timedelta(seconds=rng.uniform(10, 180))
    if not order.forced and rng.random() < PAY_DECLINE:
        pay("failed", "PG_DECLINED", first)
        roll = rng.random()
        if roll < DECLINE_RETRY_OK:
            again = first + timedelta(seconds=rng.uniform(60, 900))
            pay("succeeded", None, again)
            order.paid = again
        elif roll < DECLINE_RETRY_OK + DECLINE_CANCEL:
            order.status = "cancelled"
            order.updated = min(first + timedelta(minutes=rng.uniform(2, 60)), window.cutoff)
            return payment_no
        else:
            order.status, order.updated = "payment_failed", first
            return payment_no
    else:
        pay("succeeded", None, first)
        order.paid = first
    order.status, order.updated = "paid", order.paid
    order.promised = order.paid + timedelta(days=PROMISED_DELIVERY_DAYS)
    shipped = _shift_holiday(order.paid + timedelta(hours=rng.uniform(*SHIP_HOURS)), rng)
    if shipped > window.cutoff:
        return payment_no
    order.shipped = shipped
    trend = window.trend[min(order.day, WINDOW_DAYS - 1)]
    late_p = LATE_BASE + LATE_TREND * min(1.0, trend / (TREND_PEAK - 1))
    hours = LATE_HOURS if (not order.forced and rng.random() < late_p) else DELIVER_HOURS
    delivered = _shift_holiday(shipped + timedelta(hours=rng.uniform(*hours)), rng)
    if delivered <= window.cutoff:
        order.delivered = delivered
    order.late = delivered > order.promised
    return payment_no


# --- 문장 틀 -----------------------------------------------------------------------------------
CLAIM_TEXT = {
    "defect": (
        "책 중간 {a}~{b}쪽이 인쇄가 안 된 채로 왔어요.",
        "제본이 풀려서 낱장이 떨어져요.",
        "같은 쪽이 두 번 묶여 있고 몇 쪽이 빠져 있어요.",
        "표지가 처음부터 찢어져 있었어요.",
    ),
    "damaged": (
        "택배 상자가 젖어서 책이 물에 불었어요.",
        "모서리가 크게 찌그러져서 왔어요.",
        "포장 비닐이 뜯겨 있고 표지에 긁힌 자국이 있어요.",
    ),
    "wrong_item": ("주문한 상품과 다른 상품이 왔어요.", "같은 책의 개정 전 판이 왔어요."),
    "missing_item": ("상자에 주문한 상품 하나가 빠져 있어요.", "두 개 주문했는데 하나만 왔어요."),
    "late_delivery": (
        "약속한 날보다 며칠 늦게 와서 필요한 날을 놓쳤어요.",
        "선물하려던 날이 지나서 받았어요.",
    ),
    "change_of_mind": ("비슷한 책이 집에 있어서 반품하고 싶어요.", "생각했던 내용과 달라요.", ""),
    "other": ("주문할 때 수량을 잘못 골랐어요.", "포장 상태가 좋지 않았어요."),
}
PLANTED_TEXT = (
    "{a}쪽부터 {b}쪽까지 백지예요. 파본 같아요.",
    "{a}~{b}쪽 인쇄가 번져서 읽을 수가 없어요(파본).",
    "중간 {a}쪽 근처가 거꾸로 제본돼 있어요.",
)
REVIEW_TEXT = {
    5: (
        "정말 재미있게 읽었어요. 주변에도 추천하고 싶어요.",
        "배송도 빠르고 상태도 깨끗했어요.",
        "기대 이상이었어요. 한 번 더 읽을 생각이에요.",
        "선물했는데 받는 사람이 아주 좋아했어요.",
    ),
    4: (
        "전체적으로 만족해요. 중간이 조금 늘어지지만 좋았어요.",
        "내용이 알차요. 다음 권도 살게요.",
        "포장이 꼼꼼해서 좋았어요.",
    ),
    3: ("무난했어요. 기대만큼은 아니었어요.", "괜찮지만 가격에 비해 조금 아쉬워요."),
    2: ("생각했던 내용과 달라서 아쉬웠어요.", "상품 상태가 조금 아쉬웠어요."),
    1: ("받은 상품에 문제가 있어서 실망했어요.", "상태가 너무 안 좋아서 실망했어요."),
}
IT_REVIEW = (
    "요즘 AI를 공부하는데 업무에 바로 써먹을 수 있었어요.",
    "AI 도구 쓰는 법이 실제 예시로 나와 있어서 좋아요.",
)
RATINGS = ((5, 0.55), (4, 0.28), (3, 0.10), (2, 0.04), (1, 0.03))


def _restock_inquiry(
    sim: Simulation, order: Order, product: Product, moment: datetime, rng: Rng
) -> None:
    at = moment + timedelta(minutes=rng.uniform(5, 120))
    if at > sim.window.cutoff - timedelta(hours=1):
        return
    title = product.name[:50]
    sim.inquiries.append(
        Inquiry(
            order,
            "restock",
            f"[상품] 「{title}」 재입고 문의",
            "사려던 책이 품절이라 주문에서 빠졌어요. 다시 들어오면 살게요. 언제쯤 재입고되나요?",
            at,
        )
    )


def _within(start: datetime, span_days: float, rng: Rng, cutoff: datetime) -> datetime | None:
    at = (start + timedelta(days=rng.uniform(0, span_days))).replace(microsecond=0)
    return at if at <= cutoff - timedelta(hours=1) else None


# --- 심어 둔 정답 (클레임) -------------------------------------------------------------------
def _sold(orders: list[Order], since: datetime | None = None) -> Counter[str]:
    out: Counter[str] = Counter()
    for o in orders:
        if o.status == "paid" and (since is None or o.created >= since):
            for line in o.lines:
                out[line.product.product_id] += line.qty
    return out


def plant_claim_books(sim: Simulation, rng: Rng) -> set[str]:
    """(가) 불량 클레임이 몰린 책 2~3권, (나) 적게 팔린 책에 클레임 1건(걸리면 안 됨)."""
    window = sim.window
    recent = window.end_utc - timedelta(days=30)
    sold_all, sold_30 = _sold(sim.orders), _sold(sim.orders, recent)
    early, late = window.end_utc - timedelta(days=45), window.cutoff - timedelta(days=2)
    delivered: dict[str, list[tuple[Order, int]]] = defaultdict(list)
    for o in sim.orders:
        if o.delivered is None or o.forced:
            continue
        for i, line in enumerate(o.lines):
            delivered[line.product.product_id].append((o, i))
    books = [p for p in sim.products if p.category == "book" and p.genre != TREND_GENRE]

    def eligible(pid: str) -> list[tuple[Order, int]]:
        seen, out = set(), []
        for o, i in delivered[pid]:
            if early <= o.delivered <= late and o.member.buyer_id not in seen:
                seen.add(o.member.buyer_id)
                out.append((o, i))
        return out

    candidates = [
        p
        for p in books
        if CRX_MIN_SOLD <= sold_30[p.product_id] <= 40 and len(eligible(p.product_id)) >= 4
    ]
    if len(candidates) < 2:
        candidates = [
            p for p in books if len(eligible(p.product_id)) >= 3 and sold_30[p.product_id] <= 30
        ]
        if candidates:
            sim.warnings.append("불량 클레임을 심을 책이 적어 기준을 낮춰 골랐어요")
    chosen = rng.sample(candidates, min(3, len(candidates)))
    if len(chosen) < 2:
        sim.warnings.append(
            f"불량 클레임이 몰린 책을 {len(chosen)}권만 심었어요(배송 완료 주문 부족)"
        )
    cluster = max(window.end_utc - timedelta(days=10), window.start_utc)
    planted_a = []
    for product in sorted(chosen, key=lambda p: p.product_id):
        targets = eligible(product.product_id)
        rng.shuffle(targets)
        need = max(4, math.ceil(0.12 * sold_30[product.product_id]))
        made = 0
        for k, (order, i) in enumerate(targets[: min(6, need)]):
            start = max(order.delivered + timedelta(hours=12), cluster)
            span = max(0.0, (window.cutoff - timedelta(hours=2) - start).total_seconds() / 86400)
            at = _within(start, min(span, 8), rng, window.cutoff)
            if at is None:
                continue
            reason = "damaged" if k == 3 else "defect"
            a = rng.randrange(40, 200)
            text = rng.choice(PLANTED_TEXT).format(a=a, b=a + rng.randrange(8, 24))
            kind = "exchange" if rng.random() < 0.6 else "return"
            sim.claims.append(
                Claim(
                    order,
                    [i],
                    kind,
                    reason,
                    at,
                    False,
                    rng.randint(1, 3),
                    text,
                    planted="defect_cluster",
                )
            )
            made += 1
        if made < CRX_MIN_DEFECTS:
            sim.warnings.append(f"{product.product_id}에 불량 클레임을 {made}건만 심었어요")
        planted_a.append(
            {
                "product_id": product.product_id,
                "name": product.name,
                "genre_code": product.genre,
                "claims": made,
            }
        )
    taken = {p["product_id"] for p in planted_a}
    lows = [
        p
        for p in books
        if p.product_id not in taken
        and 1 <= sold_all[p.product_id] <= 4
        and any(
            o.delivered <= window.cutoff - timedelta(days=1) for o, _ in delivered[p.product_id]
        )
    ]
    lows.sort(
        key=lambda p: (
            not any(o.delivered >= recent for o, _ in delivered[p.product_id]),
            p.product_id,
        )
    )
    pool = lows[:12]
    planted_b = []
    for product in sorted(rng.sample(pool, min(2, len(pool))), key=lambda p: p.product_id):
        settled = [
            t
            for t in delivered[product.product_id]
            if t[0].delivered <= window.cutoff - timedelta(days=1)
        ]
        order, i = max(settled, key=lambda t: t[0].delivered)
        at = _within(order.delivered + timedelta(hours=12), 4, rng, window.cutoff)
        if at is None:
            at = window.cutoff - timedelta(hours=1, minutes=rng.randrange(5, 50))
            at = max(at, order.delivered + timedelta(minutes=30))
        text = rng.choice(CLAIM_TEXT["defect"]).format(a=50, b=58)
        sim.claims.append(
            Claim(
                order,
                [i],
                "exchange",
                "defect",
                at.replace(microsecond=0),
                False,
                1,
                text,
                planted="low_sales_single",
            )
        )
        planted_b.append(
            {
                "product_id": product.product_id,
                "name": product.name,
                "genre_code": product.genre,
                "claims": 1,
            }
        )
    if not planted_b:
        sim.warnings.append("적게 팔린 책(1~4권)이 없어 '걸리면 안 되는 책'을 심지 못했어요")
    sim.planted["defect_cluster"] = planted_a
    sim.planted["low_sales_single"] = planted_b
    return taken | {p["product_id"] for p in planted_b}


def crx_trips(sold: int, claims: int, qty: int, defects: int) -> bool:
    """CRX-001·book_issue_stats 기준: (판매 ≥5, 클레임 수량/판매 ≥10%, 클레임 ≥2) 또는 불량 ≥3."""
    rate_rule = sold >= CRX_MIN_SOLD and qty / sold >= CRX_MIN_RATE and claims >= CRX_MIN_CLAIMS
    return rate_rule or defects >= CRX_MIN_DEFECTS


class _CrxGuard:
    """무작위 클레임이 심지 않은 책을 기준에 걸리게 하지 않도록 상품별로 센다(최근 30일·기간)."""

    def __init__(self, sim: Simulation) -> None:
        self.recent = sim.window.end_utc - timedelta(days=30)
        self.sold = {"window": _sold(sim.orders), "last_30d": _sold(sim.orders, self.recent)}
        self.seen: dict[str, dict[str, Counter[str]]] = {k: defaultdict(Counter) for k in self.sold}

    def _periods(self, at: datetime) -> tuple[str, ...]:
        return ("window", "last_30d") if at >= self.recent else ("window",)

    def would_trip(self, lines: list[Line], reason: str, at: datetime) -> bool:
        defect = int(reason in DEFECT_REASONS)
        for line in lines:
            pid = line.product.product_id
            for period in self._periods(at):
                c = self.seen[period][pid]
                if crx_trips(self.sold[period][pid], c["claims"] + 1, c["qty"] + line.qty,
                             c["defect"] + defect):  # fmt: skip
                    return True
        return False

    def add(self, lines: list[Line], reason: str, at: datetime) -> None:
        for line in lines:
            for period in self._periods(at):
                c = self.seen[period][line.product.product_id]
                c["claims"] += 1
                c["qty"] += line.qty
                c["defect"] += int(reason in DEFECT_REASONS)


def _claim_kind(reason: str, rng: Rng) -> str:
    if reason in ("missing_item", "wrong_item"):
        return "exchange" if rng.random() < 0.7 else "return"
    if reason in ("defect", "damaged"):
        return "exchange" if rng.random() < 0.4 else "return"
    return "return"


def random_claims(sim: Simulation, rng: Rng, exclude: set[str]) -> int:
    """배송 완료 주문에 클레임을 낸다.

    심지 않은 책이 기준에 걸리게 될 클레임은 사유를 단순 변심으로 바꾸고, 그래도 걸리면 내지 않는다
    (정답지가 심은 책만 가리키게). 그렇게 바꾸거나 뺀 수를 돌려준다.
    """
    window = sim.window
    planted_orders = {id(c.order) for c in sim.claims}
    guard = _CrxGuard(sim)
    adjusted = 0
    for order in sim.orders:
        if order.delivered is None or order.forced or id(order) in planted_orders:
            continue
        if rng.random() >= CLAIM_RATE:
            continue
        lines = [i for i, line in enumerate(order.lines) if line.product.product_id not in exclude]
        if not lines:
            continue
        reasons = [(r, w) for r, w in CLAIM_REASONS if r != "late_delivery" or order.late]
        reason = "late_delivery" if (order.late and rng.random() < 0.5) else rng.pick(reasons)
        picked = lines if (len(lines) > 1 and rng.random() < 0.2) else [rng.choice(lines)]
        span = 6 if reason == "change_of_mind" else 12
        at = _within(order.delivered + timedelta(hours=6), span, rng, window.cutoff)
        if at is None:
            continue
        claimed = [order.lines[i] for i in picked]
        if guard.would_trip(claimed, reason, at):
            adjusted += 1
            reason = "change_of_mind"
            if guard.would_trip(claimed, reason, at):
                continue
        guard.add(claimed, reason, at)
        text = rng.choice(CLAIM_TEXT[reason]).format(a=rng.randrange(20, 200), b=0)
        text = text.replace("~0쪽", "쪽")
        evidence = rng.randint(1, 3) if reason in EVIDENCE_REASONS else 0
        unsealed = reason == "change_of_mind" and rng.random() < 0.3
        kind = _claim_kind(reason, rng)
        sim.claims.append(Claim(order, picked, kind, reason, at, unsealed, evidence, text))
    return adjusted


def reviews_and_inquiries(sim: Simulation, rng: Rng, next_review: int) -> None:
    window = sim.window
    claimed = {(id(c.order), i): c for c in sim.claims for i in c.lines}
    raw: list[tuple[datetime, Order, int, int, str]] = []
    for order in sim.orders:
        if order.delivered is not None:
            for i, line in enumerate(order.lines):
                if rng.random() >= REVIEW_RATE:
                    continue
                at = _within(order.delivered + timedelta(days=1), 13, rng, window.cutoff)
                if at is None:
                    continue
                claim = claimed.get((id(order), i))
                if claim and claim.reason in DEFECT_REASONS:
                    rating = rng.choice((1, 1, 2))
                elif claim:
                    rating = rng.choice((2, 3))
                else:
                    rating = rng.pick(RATINGS)
                body = rng.choice(REVIEW_TEXT[rating])
                if rating >= 4 and line.product.genre == TREND_GENRE and rng.random() < 0.5:
                    body = rng.choice(IT_REVIEW)
                raw.append((at, order, i, rating, body))
        _maybe_inquiry(sim, order, rng)
    raw.sort(key=lambda r: (r[0], r[1].order_id, r[2]))
    for n, (at, order, i, rating, body) in enumerate(raw, start=1):
        sim.reviews.append(Review(f"rev_{next_review + n:04d}", order, i, rating, body, at))


def _maybe_inquiry(sim: Simulation, order: Order, rng: Rng) -> None:
    if order.paid is None or order.forced:
        return
    cutoff = sim.window.cutoff
    late_ask = order.late and rng.random() < 0.10
    if not late_ask and rng.random() >= INQUIRY_RATE:
        return
    kind = (
        "delivery"
        if late_ask
        else rng.pick((("delivery", 0.55), ("receipt", 0.37), ("deletion", 0.04), (None, 0.04)))
    )
    if kind is None:
        return
    if kind == "delivery":
        start = order.promised if order.late else order.paid + timedelta(days=1)
        until = order.delivered or cutoff
        span = max(0.05, (until - start).total_seconds() / 86400)
        at = _within(start, span, rng, cutoff)
        days = 0 if at is None else max(1, (at - order.created).days)
        subject = rng.choice(("[주문·배송] 배송이 언제 오나요?", "[주문·배송] 배송 조회가 안 돼요"))
        message = rng.choice(
            (
                f"주문한 지 {days}일이 지났는데 아직 받지 못했어요. 언제쯤 도착할까요?",
                "배송 조회에서 며칠째 같은 단계예요. 확인 부탁드려요.",
            )
        )
    elif kind == "receipt":
        at = _within(order.paid + timedelta(hours=1), 10, rng, cutoff)
        subject = "[결제·환불] 영수증 확인 부탁드려요"
        message = rng.choice(
            (
                f"회사 경비 처리용으로 카드 영수증이 필요해요. "
                f"결제 금액 {order.total:,}원이 맞는지 확인해 주세요.",
                "현금영수증이 발급됐는지 확인하고 싶어요.",
            )
        )
    else:
        at = _within(order.created + timedelta(days=1), 20, rng, cutoff)
        subject = "[개인정보 삭제 요청] 회원 정보 삭제를 요청해요"
        message = "더 이상 이용하지 않아서 제 회원 정보를 지워 주세요. 법으로 남겨야 하는 주문 기록은 괜찮아요."  # noqa: E501
    if at is not None:
        sim.inquiries.append(Inquiry(order, kind, subject, message, at))


# --- 전체 ------------------------------------------------------------------------------------
def simulate(
    conn: sqlite3.Connection, config: Config, now: datetime, log: Callable[[str], None]
) -> Simulation:
    window = build_window(config, now)
    seed = config.seed
    products = load_products(conn, config.replace, config.dry_run)
    if not products:
        raise ValueError("판매 중인 상품이 없어요. 상품을 먼저 넣어 주세요 (load_catalog)")
    shelf = Shelf(products, Rng(f"{seed}:shelf"))
    log(f"회원 {config.members:,}명 만드는 중…")
    members = make_members(config.members, window, Rng(f"{seed}:members"))
    sim = Simulation(window, members, products, shelf)
    sim.norms = calibrate_trend(members)
    _catalog_warnings(sim)
    times = order_times(members, window, Rng(f"{seed}:times"))
    log(f"주문 {len(times):,}건 조립 중(재고·결제·배송)…")
    next_order, next_payment = _counter(conn, "order"), _counter(conn, "payment")
    life = Rng(f"{seed}:life")
    assemble_orders(sim, times, Rng(f"{seed}:lines"), life, next_order, next_payment)
    exclude = plant_claim_books(sim, Rng(f"{seed}:planted"))
    sim.claims_adjusted = random_claims(sim, Rng(f"{seed}:claims"), exclude)
    sim.claims.sort(key=lambda c: (c.created, c.order.order_id))
    reviews_and_inquiries(sim, Rng(f"{seed}:after"), _counter(conn, "review"))
    sim.inquiries.sort(key=lambda q: (q.created, q.order.order_id))
    return sim


def _catalog_warnings(sim: Simulation) -> None:
    by_genre = sim.shelf.by_genre
    tagged = sum(len(by_genre.get(g, [])) for g in BOOK_GENRES)
    missing = [g for g in BOOK_GENRES if not by_genre.get(g)]
    if tagged < len(BOOK_GENRES) * 3:
        sim.warnings.append(
            f"장르가 붙은 책이 {tagged}권뿐이에요. 장르 없는 책으로 대신 채워서 장르별 결과"
            "(트렌드 포함)가 거의 보이지 않아요. load_catalog로 실제 책 목록을 먼저 넣어 주세요"
        )
    if missing:
        sim.warnings.append("상품이 없는 책 장르: " + ", ".join(missing))
    if not by_genre.get(TREND_GENRE):
        sim.warnings.append("AI·IT(it) 책이 없어 트렌드가 판매 기록에 나타나지 않아요")


# --- 정답지 ------------------------------------------------------------------------------------
def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 3) if b else None


def answer_key(sim: Simulation, config: Config, now: datetime) -> dict[str, Any]:
    w = sim.window
    paid = [o for o in sim.orders if o.status == "paid"]
    status = Counter(o.status for o in sim.orders)
    kst_day = {o.order_id: o.created.astimezone(KST).date() for o in sim.orders}

    def week_of(d: date) -> date:
        return d - timedelta(days=d.weekday())

    by_day: dict[date, Counter[str]] = {d: Counter() for d in w.days}
    by_week: dict[date, Counter[str]] = defaultdict(Counter)
    genre_units: Counter[str] = Counter()
    for o in sim.orders:
        d = kst_day[o.order_id]
        by_day[d]["orders"] += 1
        by_week[week_of(d)]["orders"] += 1
        if o.status == "paid":
            by_day[d]["paid_orders"] += 1
            by_day[d]["revenue"] += o.total
            by_week[week_of(d)]["paid_orders"] += 1
            by_week[week_of(d)]["revenue"] += o.total
            for line in o.lines:
                g = line.product.genre or "unknown"
                by_day[d]["units"] += line.qty
                by_day[d][f"units:{g}"] += line.qty
                by_week[week_of(d)]["units"] += line.qty
                by_week[week_of(d)][f"units:{g}"] += line.qty
                genre_units[g] += line.qty
    book_units = sum(v for g, v in genre_units.items() if g in BOOK_GENRES)

    # 트렌드: 평소(트렌드 전, 추석 주 뺌)와 최고점(3~4주 차) 하루 평균
    trig = w.trend_start
    chu = w.chuseok_week

    def base_day(d: date) -> bool:
        return d < trig and not (chu and chu[0] <= d <= chu[1])

    # 최고점 = 모델 배수가 꼭대기(×4.1)에 있는 날(3~4주 차)
    peak_days = [d for d, t in zip(w.days, w.trend, strict=True) if t + 1 >= TREND_PEAK - 0.05]
    base_days = [d for d in w.days if base_day(d)]
    factor = dict(zip(w.days, w.factor, strict=True))

    def lift(genre: str) -> dict[str, float | None]:
        key = f"units:{genre}"
        raw_b = sum(by_day[d][key] for d in base_days) / max(1, len(base_days))
        raw_p = sum(by_day[d][key] for d in peak_days) / max(1, len(peak_days))
        adj_b = sum(by_day[d][key] / factor[d] for d in base_days) / max(1, len(base_days))
        adj_p = sum(by_day[d][key] / factor[d] for d in peak_days) / max(1, len(peak_days))
        return {
            "baseline_units_per_day": round(raw_b, 2),
            "peak_units_per_day": round(raw_p, 2),
            "raw_multiple": _ratio(raw_p, raw_b),
            "seasonally_adjusted_multiple": _ratio(adj_p, adj_b),
        }

    def mix(days: list[date]) -> dict[str, Any]:
        span = set(days)
        buyers, units, occ, units_4050m = set(), 0, Counter(), 0
        buyers_4050m = set()
        for o in paid:
            if kst_day[o.order_id] not in span:
                continue
            q = sum(line.qty for line in o.lines if line.product.genre == TREND_GENRE)
            if not q:
                continue
            m = o.member
            buyers.add(m.buyer_id)
            units += q
            occ[m.occupation or "unknown"] += q
            if m.gender == "male" and m.age_band in ("40s", "50s_plus"):
                buyers_4050m.add(m.buyer_id)
                units_4050m += q
        return {
            "it_buyers": len(buyers),
            "it_units": units,
            "male_40_50_buyer_share": _ratio(len(buyers_4050m), len(buyers)),
            "male_40_50_unit_share": _ratio(units_4050m, units),
            "occupation_unit_share": {k: _ratio(v, units) for k, v in sorted(occ.items())},
        }

    before, at_peak = mix(base_days), mix(peak_days)
    occ_lift = {
        k: _ratio(at_peak["occupation_unit_share"].get(k) or 0, v)
        for k, v in before["occupation_unit_share"].items()
        if v
    }
    weekly_it = []
    base_week_units = lift(TREND_GENRE)["baseline_units_per_day"] * 7
    for monday in sorted(by_week):
        units = by_week[monday][f"units:{TREND_GENRE}"]
        weekly_it.append(
            {
                "week_start": monday.isoformat(),
                "it_units": units,
                "multiple_vs_baseline": _ratio(units, base_week_units),
            }
        )

    # 20대 여성 소설·에세이 몫
    def share_novel_essay(pred: Callable[[Member], bool]) -> float | None:
        top = total = 0
        for o in paid:
            if not pred(o.member):
                continue
            for line in o.lines:
                if line.product.genre in BOOK_GENRES:
                    total += line.qty
                    top += line.qty if line.product.genre in ("novel", "essay") else 0
        return _ratio(top, total)

    f20 = share_novel_essay(lambda m: m.age_band == "20s" and m.gender == "female")
    everyone = share_novel_essay(lambda m: True)

    # 추석 주·월급날
    chuseok = None
    if chu:

        def per_day(lo: date, hi: date) -> float | None:
            days = [d for d in w.days if lo <= d <= hi]
            return sum(by_day[d]["orders"] for d in days) / len(days) if days else None

        inside = per_day(*chu)
        around = [
            x
            for x in (
                per_day(chu[0] - timedelta(days=14), chu[0] - timedelta(days=1)),
                per_day(chu[1] + timedelta(days=1), chu[1] + timedelta(days=14)),
            )
            if x is not None
        ]
        chuseok = {
            "week": [chu[0].isoformat(), chu[1].isoformat()],
            "model_factor": CHUSEOK_FACTOR,
            "orders_per_day": round(inside or 0, 1),
            "neighbor_weeks_orders_per_day": round(sum(around) / len(around), 1)
            if around
            else None,  # noqa: E501
            "ratio": _ratio(inside or 0, sum(around) / len(around)) if around else None,
        }
    paydays = [d for d in w.days if d.day == PAYDAY]
    payday = {"days": [d.isoformat() for d in paydays], "model_factor": PAYDAY_FACTOR}

    crx = crx_table(sim)
    planted_ids = {
        p["product_id"]
        for k in ("defect_cluster", "low_sales_single")
        for p in sim.planted.get(k, [])
    }
    for key in ("defect_cluster", "low_sales_single"):
        for p in sim.planted.get(key, []):
            p["stats"] = crx.get(p["product_id"])
            p["expected_crx_001"] = key == "defect_cluster"
    incidental = sorted(
        pid
        for pid, row in crx.items()
        if pid not in planted_ids and (row["trips_last_30d"] or row["trips_window"])
    )

    suppressed = suppressed_groups(sim)
    members = sim.members
    expected = sorted((m.yearly for m in members), reverse=True)
    top20 = sum(expected[: max(1, len(expected) // 5)]) / max(1e-9, sum(expected))
    trend_titles = Counter()
    for o in paid:
        if kst_day[o.order_id] >= trig:
            for line in o.lines:
                if line.product.genre == TREND_GENRE:
                    trend_titles[line.product.product_id] += line.qty
    names = {p.product_id: p for p in sim.products}
    claims_by = Counter(c.reason for c in sim.claims)
    delivered = [o for o in sim.orders if o.delivered]
    delivered_lines = sum(len(o.lines) for o in delivered)
    return {
        "note": "가상 데이터 정답지(로컬 전용). 저장소·DB에 넣지 않는다.",
        "generated_at": _stamp(now),
        "config": {
            "seed": config.seed,
            "members": config.members,
            "window_start": w.start.isoformat(),
            "window_end": w.end.isoformat(),
            "trend_start": w.trend_start.isoformat(),
            "cutoff_utc": _stamp(w.cutoff),
            "timezone_for_days": "Asia/Seoul (KST)",
        },
        "warnings": sim.warnings,
        "catalog": {
            "active_products": len(sim.products),
            "products_by_genre": {g: len(sim.shelf.by_genre.get(g, [])) for g in GENRES}
            | {"untagged_books": len(sim.shelf.untagged_books)},
            "genre_fallback_lines": sim.shelf.fallback_lines,
        },
        "members": {
            "total": len(members),
            "joined_in_window": sum(1 for m in members if m.join_day >= 0),
            "profile_blank": sum(1 for m in members if m.age_band is None),
            "by_persona": dict(Counter(m.persona.code for m in members).most_common()),
            "age_band": dict(Counter(m.age_band or "blank" for m in members).most_common()),
            "gender": dict(Counter(m.gender or "blank" for m in members).most_common()),
            "occupation": dict(Counter(m.occupation or "blank" for m in members).most_common()),
            "top20_share_of_expected_orders": round(top20, 3),
            "buyers_with_orders": len({o.member.buyer_id for o in sim.orders}),
        },
        "totals": {
            "orders": len(sim.orders),
            "orders_per_day": round(len(sim.orders) / WINDOW_DAYS, 1),
            "by_status": dict(status),
            "cancel_rate": _ratio(status["cancelled"], len(sim.orders)),
            "payments": dict(Counter(p.status for o in sim.orders for p in o.payments)),
            "paid_units": sum(line.qty for o in paid for line in o.lines),
            "items_per_order": _ratio(sum(len(o.lines) for o in sim.orders), len(sim.orders)),
            "units_per_order": _ratio(
                sum(x.qty for o in sim.orders for x in o.lines), len(sim.orders)
            ),
            "paid_revenue": sum(o.total for o in paid),
            "avg_order_value": _ratio(sum(o.total for o in paid), len(paid)),
            "free_shipping_share": _ratio(sum(1 for o in paid if o.shipping_fee == 0), len(paid)),
            "stationery_order_share": _ratio(
                sum(
                    1
                    for o in sim.orders
                    if any(x.product.category == "stationery" for x in o.lines)
                ),
                len(sim.orders),
            ),
            "shipped": sum(1 for o in sim.orders if o.shipped),
            "delivered": len(delivered),
            "late_deliveries": sum(1 for o in delivered if o.late),
            "orders_dropped_all_sold_out": sim.dropped,
        },
        "by_day": [
            {"date": d.isoformat(), **{k: v for k, v in sorted(by_day[d].items())}} for d in w.days
        ],
        "by_week": [
            {"week_start": d.isoformat(), **dict(sorted(c.items()))}
            for d, c in sorted(by_week.items())
        ],
        "by_genre": {
            g: {
                "units": v,
                "share_of_book_units": _ratio(v, book_units) if g in BOOK_GENRES else None,
            }
            for g, v in genre_units.most_common()
        },
        "trend": {
            "genre": TREND_GENRE,
            "start": trig.isoformat(),
            "peak_window": [peak_days[0].isoformat(), peak_days[-1].isoformat()]
            if peak_days
            else None,
            "model_peak_multiple": TREND_PEAK,
            "model_half_life_weeks": 2,
            "it": lift(TREND_GENRE),
            "spillover": {g: {"model_peak_lift": v, **lift(g)} for g, v in SPILLOVER.items()},
            "weekly_it_units": weekly_it,
            "buyer_mix_before": before,
            "buyer_mix_peak": at_peak,
            "occupation_lift_peak_vs_before": occ_lift,
            "model_male_40_50_share": {"before": 0.22, "peak": 0.38},
            "top_trend_titles": [
                {
                    "product_id": pid,
                    "name": names[pid].name,
                    "units_since_trend": q,
                    "ai_title": names[pid].ai_title,
                    "stock_left": names[pid].stock,
                    "sold_out_at": _stamp(sim.sold_out_at.get(pid)),
                }
                for pid, q in trend_titles.most_common(5)
            ],
        },
        "twenties_female_novel_essay": {
            "share_20s_female": f20,
            "share_all": everyone,
            "lift": _ratio(f20 or 0, everyone or 0),
        },
        "december_event_suggestion": {
            "genre": TREND_GENRE,
            "segment": "40대 남성 직장인·자영업자",
            "why": "AI·IT 트렌드에 가장 크게 반응한 묶음(트렌드 최고점에 40~50대 남성 몫이 오른다)",
            "evidence": {
                "male_40_50_unit_share_before": before["male_40_50_unit_share"],
                "male_40_50_unit_share_peak": at_peak["male_40_50_unit_share"],
            },
            "caveat": "품절된 트렌드 책은 빼거나 재입고 뒤로 미룬다",
        },
        "chuseok": chuseok,
        "payday": payday,
        "stockouts": {
            "skipped_lines": len(sim.stockouts),
            "trend_title_lines": sum(1 for s in sim.stockouts if s.trend),
            "restock_inquiries": sum(1 for q in sim.inquiries if q.kind == "restock"),
            "sold_out_products": [
                {
                    "product_id": pid,
                    "name": names[pid].name,
                    "genre_code": names[pid].genre,
                    "sold_out_at": _stamp(at),
                    "start_stock": sim.stock_start[pid],
                }
                for pid, at in sorted(sim.sold_out_at.items(), key=lambda kv: kv[1])
            ],
        },
        "claims": {
            "total": len(sim.claims),
            "by_reason": dict(claims_by.most_common()),
            "by_type": dict(Counter(c.type for c in sim.claims).most_common()),
            "rate_of_delivered_orders": _ratio(len(sim.claims), len(delivered)),
            "refunds_created": 0,
            "random_claims_adjusted_to_avoid_crx": sim.claims_adjusted,
            "note": "모두 received(고객지원이 읽어 갈 차례). 환불은 고객지원 판정 뒤 정상 경로로 생긴다",  # noqa: E501
        },
        "reviews": {
            "total": len(sim.reviews),
            "share_of_delivered_items": _ratio(len(sim.reviews), delivered_lines),
            "avg_rating": _ratio(sum(r.rating for r in sim.reviews), len(sim.reviews)),
        },
        "inquiries": {
            "total": len(sim.inquiries),
            "by_kind": dict(Counter(q.kind for q in sim.inquiries).most_common()),
            "share_of_orders": _ratio(len(sim.inquiries), len(sim.orders)),
        },
        "planted": {
            "crx_001_rule": "(같은 기간 판매 ≥5 그리고 클레임 수량/판매 ≥10% 그리고 클레임 ≥2) 또는 불량 클레임 ≥3",  # noqa: E501
            "defect_cluster": sim.planted.get("defect_cluster", []),
            "low_sales_single": sim.planted.get("low_sales_single", []),
            "incidental_trips": [{"product_id": pid, **crx[pid]} for pid in incidental],
            "small_group": sim.planted.get("small_group"),
        },
        "suppressed_groups": suppressed,
    }


def crx_table(sim: Simulation) -> dict[str, dict[str, Any]]:
    """상품별 클레임·판매(최근 30일, 기간 전체)와 CRX-001 기준 판정."""
    w = sim.window
    recent = w.end_utc - timedelta(days=30)
    sold = {"window": _sold(sim.orders), "last_30d": _sold(sim.orders, recent)}
    claims: dict[str, dict[str, Counter[str]]] = {k: defaultdict(Counter) for k in sold}
    for c in sim.claims:
        for i in c.lines:
            pid = c.order.lines[i].product.product_id
            for period in ("window", "last_30d"):
                if period == "last_30d" and c.created < recent:
                    continue
                row = claims[period][pid]
                row["claims"] += 1
                row["qty"] += c.order.lines[i].qty
                row["defect"] += c.reason in DEFECT_REASONS
    out = {}
    for pid in sorted(set(claims["window"])):
        row: dict[str, Any] = {}
        for period in ("last_30d", "window"):
            s, c = sold[period][pid], claims[period][pid]
            rate = c["qty"] / s if s else None
            trips = crx_trips(s, c["claims"], c["qty"], c["defect"])
            row[f"sold_{period}"] = s
            row[f"claims_{period}"] = c["claims"]
            row[f"defect_claims_{period}"] = c["defect"]
            row[f"claim_rate_{period}"] = None if rate is None else round(rate, 3)
            row[f"trips_{period}"] = trips
        out[pid] = row
    return out


def suppressed_groups(sim: Simulation) -> dict[str, Any]:
    """판매 집계(기간 전체)에서 구매자 5명 미만이라 숨겨질 줄."""
    out = {}
    for name, keys in (
        ("genre_age_gender_occupation", ("genre", "age", "gender", "occ")),
        ("age_gender_occupation", ("age", "gender", "occ")),
    ):
        buyers: dict[tuple, set[str]] = defaultdict(set)
        for o in sim.orders:
            if o.status != "paid":
                continue
            m = o.member
            for line in o.lines:
                values = {
                    "genre": line.product.genre,
                    "age": m.age_band,
                    "gender": m.gender,
                    "occ": m.occupation,
                }
                buyers[tuple(values[k] for k in keys)].add(m.buyer_id)
        small = sorted(
            (k for k, v in buyers.items() if len(v) < MIN_GROUP_BUYERS),
            key=lambda k: tuple(str(x) for x in k),
        )
        out[name] = {
            "rows": len(buyers),
            "suppressed_rows": len(small),
            "examples": [
                dict(zip(keys, k, strict=True)) | {"buyers": len(buyers[k])} for k in small[:25]
            ],
        }
    group = sim.planted.get("small_group") or {}
    attrs = group.get("attributes") or {}
    out["planted_group"] = {
        "genre_code": group.get("genre_code"),
        **attrs,
        "buyers": len(group.get("buyers") or []),
        "expected_suppressed": bool(group.get("buyers")),
    }
    return out


def member_rows(sim: Simulation) -> list[dict[str, Any]]:
    rows = []
    for m in sim.members:
        top = sorted(m.weights.items(), key=lambda kv: -kv[1])[:3]
        rows.append(
            {
                "buyer_id": m.buyer_id,
                "persona": m.persona.code,
                "persona_name": m.persona.name,
                "age_band": m.age_band or "",
                "gender": m.gender or "",
                "occupation": m.occupation or "",
                "joined_at": _stamp(m.joined),
                "joined_in_window": int(m.join_day >= 0),
                "yearly_orders": round(m.yearly, 3),
                "trend_sensitivity": round(m.sensitivity, 3),
                "hour_profile": m.persona.hours,
                "top_genres": " ".join(g for g, _ in top),
                "orders_in_window": len(m.orders),
            }
        )
    return rows


# --- DB 쓰기 ----------------------------------------------------------------------------------
def write(conn: sqlite3.Connection, sim: Simulation, log: Callable[[str], None]) -> None:
    password = hash_password(secrets.token_urlsafe(32))  # 아무도 모르는 값: 로그인할 수 없다
    base_seq = conn.execute("SELECT COALESCE(MAX(change_seq), 0) FROM buyers").fetchone()[0]
    log(f"회원 {len(sim.members):,}명 넣는 중…")
    buyers, accounts = [], []
    for n, m in enumerate(sim.members, start=1):
        joined = _stamp(m.joined)
        buyers.append(
            (
                m.buyer_id,
                TENANT_ID,
                joined,
                m.age_band,
                m.gender,
                m.occupation,
                joined,
                base_seq + n,
            )
        )
        accounts.append(
            (
                m.buyer_id,
                synthetic_ids.email(m.buyer_id),
                password,
                f"가상회원{m.buyer_id[4:]}",
                joined,
                joined,
            )
        )
    conn.executemany(
        "INSERT INTO buyers(buyer_id, tenant_id, context_id, created_at, age_band, gender, "
        "occupation, updated_at, change_seq) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
        buyers,
    )
    conn.executemany(
        "INSERT INTO accounts(buyer_id, email, password_hash, display_name, agreed_at, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?)",
        accounts,
    )
    log(f"주문 {len(sim.orders):,}건 넣는 중…")
    orders = sim.orders  # 접수 시각 순(주문 번호도 이 순서다)
    conn.executemany(
        "INSERT INTO orders (order_id, buyer_id, status, total_amount, tenant_id, context_id, "
        "created_at, updated_at, shipping_fee, points_used, delivered_at, promised_delivery_at, "
        "shipped_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, ?, ?, ?)",
        [
            (
                o.order_id,
                o.member.buyer_id,
                o.status,
                o.total,
                TENANT_ID,
                _stamp(o.created),
                _stamp(o.updated or o.created),
                o.shipping_fee,
                _stamp(o.delivered),
                _stamp(o.promised),
                _stamp(o.shipped),
            )
            for o in orders
        ],
    )
    for o in orders:
        for line in o.lines:
            cur = conn.execute(
                "INSERT INTO order_items (order_id, product_id, seller_id, qty, unit_price, "
                "tenant_id, context_id, title, points_share, coupon_share) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 0, 0)",
                (
                    o.order_id,
                    line.product.product_id,
                    line.product.seller_id,
                    line.qty,
                    line.product.price,
                    TENANT_ID,
                    line.product.name,
                ),
            )
            line.rowid = cur.lastrowid
    payments = [(p, o) for o in orders for p in o.payments]
    conn.executemany(
        "INSERT INTO payments (payment_id, order_id, amount, method_code, status, failure_code, "
        "tenant_id, context_id, created_at, provider) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, "
        "'fake')",
        [
            (
                p.payment_id,
                o.order_id,
                o.total,
                p.method,
                p.status,
                p.failure_code,
                TENANT_ID,
                _stamp(p.created),
            )
            for p, o in payments
        ],
    )
    taken: Counter[str] = Counter()
    for o in orders:
        if o.status != "cancelled":
            for line in o.lines:
                taken[line.product.product_id] += line.qty
    for product_id, qty in sorted(taken.items()):
        conn.execute(
            "UPDATE products SET stock = stock - ? WHERE product_id = ?", (qty, product_id)
        )
    counts = f"클레임 {len(sim.claims):,}건·문의 {len(sim.inquiries):,}건"
    log(f"{counts}·후기 {len(sim.reviews):,}건 넣는 중…")
    for c in sim.claims:
        buyer = c.order.member.buyer_id
        items = [
            {"order_item_id": f"oit_{c.order.lines[i].rowid:04d}", "qty": c.order.lines[i].qty}
            for i in c.lines
        ]
        key = claim_digest(
            buyer, c.order.order_id, c.type, c.reason, items, c.unsealed, c.description
        )
        stamp = _stamp(c.created)
        conn.execute(
            "INSERT INTO claim_inbox(digest, context_id, buyer_id, customer_name, order_id,"
            " claim_type, reason, items, unsealed, evidence_count, description, status,"
            " tenant_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,"
            " 'received', ?, ?, ?)",
            (
                key,
                f"{CLAIM_CONTEXT_PREFIX}{key[:12]}",
                buyer,
                buyer,
                c.order.order_id,
                c.type,
                c.reason,
                json.dumps(items, ensure_ascii=False),
                int(c.unsealed),
                c.evidence,
                c.description,
                TENANT_ID,
                stamp,
                stamp,
            ),
        )
    for q in sim.inquiries:
        buyer = q.order.member.buyer_id
        stamp = _stamp(q.created)
        basis = "".join((buyer, q.order.order_id, q.subject, q.message, stamp or ""))
        key = hashlib.sha256(basis.encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO inquiry_inbox(digest, context_id, buyer_id, customer_name, order_id,"
            " subject, message, images, tenant_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?,"
            " NULL, ?, ?)",
            (
                key,
                f"{CLAIM_CONTEXT_PREFIX}{key[:12]}",
                buyer,
                f"가상회원{buyer[4:]}",
                q.order.order_id,
                q.subject,
                q.message,
                TENANT_ID,
                stamp,
            ),
        )
    conn.executemany(
        "INSERT INTO reviews(review_id, order_id, product_id, buyer_id, rating, body, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                r.review_id,
                r.order.order_id,
                r.order.lines[r.line].product.product_id,
                r.order.member.buyer_id,
                r.rating,
                r.body,
                _stamp(r.created),
            )
            for r in sim.reviews
        ],
    )
    for name, used in (
        ("order", len(sim.orders)),
        ("payment", len(payments)),
        ("review", len(sim.reviews)),
    ):
        if used:
            conn.execute(
                "INSERT INTO counters(name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value = value + ?",
                (name, used, used),
            )


@dataclass
class Result:
    simulation: Simulation
    answer_key: dict[str, Any]
    members_csv: list[dict[str, Any]]
    deleted: dict[str, int] | None
    written: bool


def generate(db: Db, config: Config, log: Callable[[str], None] = print) -> Result:
    """계산하고(dry-run이 아니면) 한 트랜잭션에 넣는다. 가상 회원이 이미 있으면 멈춘다."""
    now = (config.now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    deleted = None
    with db.tx() as conn:
        if not config.dry_run:
            conn.execute("BEGIN IMMEDIATE")  # 계산하는 동안 다른 쓰기가 끼지 않게
        existing = synthetic_count(conn)
        if existing and not config.replace:
            raise SyntheticExistsError(
                f"가상 회원이 이미 {existing:,}명 있어요. 다시 넣으려면 --replace를 주세요"
            )
        if existing and not config.dry_run:
            log(f"이미 있는 가상 회원 {existing:,}명과 딸린 행을 지우는 중…")
            deleted = delete_synthetic(conn)
        outbox_before = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
        sim = simulate(conn, config, now, log)
        key = answer_key(sim, config, now)
        rows = member_rows(sim)
        if not config.dry_run:
            write(conn, sim, log)
            outbox_after = conn.execute("SELECT COUNT(*) FROM outbox_events").fetchone()[0]
            if outbox_after != outbox_before:
                raise RuntimeError(
                    "outbox 행이 늘었어요. 되돌려요(가상 데이터는 이벤트를 내지 않아요)"
                )
    return Result(sim, key, rows, deleted, not config.dry_run)


def summary(result: Result) -> str:
    k = result.answer_key
    t, tr = k["totals"], k["trend"]
    it = tr["it"]
    mode = "dry-run(쓰지 않음)" if not result.written else "넣음"
    lines = [
        f"[가상 데이터 {mode}] {k['config']['window_start']} ~ {k['config']['window_end']} "
        f"(트렌드 시작 {tr['start']}, seed {k['config']['seed']})",
        f"회원 {k['members']['total']:,}명 (기간 중 가입 {k['members']['joined_in_window']:,}, "
        f"프로필 비움 {k['members']['profile_blank']:,}, 주문한 회원 "
        f"{k['members']['buyers_with_orders']:,})",
        f"주문 {t['orders']:,}건 = 하루 평균 {t['orders_per_day']}건 · 상태 {t['by_status']}",
        f"결제 완료 매출 {t['paid_revenue']:,}원 · 주문당 {t['avg_order_value']}원 · 품목/주문 "
        f"{t['items_per_order']} · 권/주문 {t['units_per_order']} · 무료배송 "
        f"{t['free_shipping_share']}",
        f"AI·IT 최고점 배수 {it['raw_multiple']} (계절 보정 {it['seasonally_adjusted_multiple']},"
        f" 모델 {tr['model_peak_multiple']}) · 40~50대 남성 몫(수량) "
        f"{tr['buyer_mix_before']['male_40_50_unit_share']} → "
        f"{tr['buyer_mix_peak']['male_40_50_unit_share']}",
        f"클레임 {k['claims']['total']}건 {k['claims']['by_reason']} · 후기 "
        f"{k['reviews']['total']}건 · 문의 {k['inquiries']['total']}건 {k['inquiries']['by_kind']}",
        f"품절 건너뛴 줄 {k['stockouts']['skipped_lines']} · 품절 상품 "
        f"{len(k['stockouts']['sold_out_products'])}개 · 추석 주 비율 "
        f"{(k['chuseok'] or {}).get('ratio')}",
        "심은 클레임 책(걸려야 함): "
        + ", ".join(f"{p['product_id']}({p['claims']}건)" for p in k["planted"]["defect_cluster"]),
        "적게 팔린 책(안 걸려야 함): "
        + ", ".join(p["product_id"] for p in k["planted"]["low_sales_single"]),
        f"숨겨질 작은 묶음(장르×연령×성별×직업): "
        f"{k['suppressed_groups']['genre_age_gender_occupation']['suppressed_rows']}줄",
        "장르별 상품 수: "
        + ", ".join(f"{g}={n}" for g, n in k["catalog"]["products_by_genre"].items()),
    ]
    for warning in k["warnings"]:
        lines.append(f"주의: {warning}")
    return "\n".join(lines)


def write_outputs(result: Result, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "answer_key.json").write_text(
        json.dumps(result.answer_key, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = result.members_csv
    with (out / "member_personas.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["buyer_id"])
        writer.writeheader()
        writer.writerows(rows)


def _date(text: str) -> date:
    return date.fromisoformat(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="캣북스 가상 회원·주문을 판매 서버 DB에 넣는다")
    parser.add_argument("--db", help="DB 파일(기본 SHOP_DB_PATH)")
    parser.add_argument("--members", type=int, default=DEFAULT_MEMBERS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--end", type=_date, help="끝 날짜 YYYY-MM-DD(기본 어제, 한국 시간)")
    parser.add_argument("--trend-start", type=_date, help="트렌드 시작(기본 끝 날짜 − 38일)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="정답지 폴더(커밋 금지)")
    parser.add_argument("--dry-run", action="store_true", help="계산만 하고 쓰지 않는다")
    parser.add_argument("--replace", action="store_true", help="있는 가상 회원을 지우고 다시")
    args = parser.parse_args(argv)
    if args.members < 1:
        parser.error("--members는 1 이상이어야 해요")
    db = Db(args.db)
    if not db.is_file:
        print("주의: DB가 메모리(:memory:)예요. SHOP_DB_PATH나 --db로 파일을 주세요")
    config = Config(
        members=args.members,
        seed=args.seed,
        end=args.end,
        trend_start=args.trend_start,
        replace=args.replace,
        dry_run=args.dry_run,
    )
    try:
        result = generate(db, config)
    except SyntheticExistsError as error:
        print(error, file=sys.stderr)
        return 2
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    if result.deleted:
        print("지운 행: " + ", ".join(f"{k}={v}" for k, v in result.deleted.items() if v))
    print(summary(result))
    if not args.dry_run:
        write_outputs(result, args.out)
        print(f"정답지: {args.out.resolve() / 'answer_key.json'} (로컬 전용, 커밋·공유 금지)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
