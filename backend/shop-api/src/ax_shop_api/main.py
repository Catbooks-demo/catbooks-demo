"""ax-shop-api 서버 진입점.

경로는 `/api/shop/<복수 명사>`, 성공은 `{data, meta}`, 실패는 `{error: {code, message}}`다.
PR 1: 상품·주문·가짜 결제·시드·이벤트 발행. PR 2: 장애 주입·해제와 `shop.degraded` 측정.
관리 API(`/api/shop/admin/*`)는 `Authorization: Bearer <토큰>`의 요청자가 허용 목록에 있어야 한다.
토큰은 판매 서버가 발급한 연결 키(`SHOP_CLIENT_KEYS`)다. 전환 기간에는 AXcellent 에이전트 토큰도
받는다(`SHOP_AGENT_TOKENS=off`면 끈다).
사람 로그인(JWT)은 아직 없어서 관리자는 토큰 이름 `admin`으로 대신한다(TODO).
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, Path, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, Field, field_validator, model_validator

from ax_agent_template.config import set_agent_name
from ax_agent_template.server import verify_token
from ax_agent_template.services import department_of
from ax_contracts.data_access.shop_customers import (
    CUSTOMERS_EXPORT_LIMIT_MAX,
    ShopCustomersExportQuery,
)
from ax_contracts.data_access.shop_failed_orders import ShopFailedOrdersQuery
from ax_contracts.data_access.shop_metric_series import ShopMetricSeriesQuery
from ax_contracts.data_access.shop_sales import ShopSalesExportQuery
from ax_contracts.data_access.shop_seller_prices import ShopSellerPricesQuery
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfileQuery
from ax_shop_api import (
    accounts,
    book_info,
    claim_inbox,
    genres,
    idea_inbox,
    inbox,
    inquiries,
    marketing_client,
    orchestrator_relay,
    outbox,
    retention,
    support_client,
    toss,
)
from ax_shop_api.accounts import Accounts
from ax_shop_api.db import Db
from ax_shop_api.lookups import Lookups
from ax_shop_api.promotions import Promotions
from ax_shop_api.refunds import Refunds
from ax_shop_api.sales_export import SalesExport
from ax_shop_api.seed import seed
from ax_shop_api.service import (
    BAD_PAYMENT_VERSION,
    ORDER_PAGE_DEFAULT,
    ORDER_PAGE_MAX,
    Shop,
    ShopError,
)
from ax_shop_api.storefront import Storefront


class BookInfoIn(BaseModel):
    """상품에 붙는 선택 책 정보·장르. 비워 두면 모르는 것이다(값을 지어내지 않는다)."""

    isbn13: str | None = (
        None  # 하이픈은 뺀다. 13자리·978/979로 시작·확인 숫자가 맞아야 한다(아니면 422)
    )
    author: str | None = Field(default=None, max_length=book_info.AUTHOR_MAX)
    publisher: str | None = Field(default=None, max_length=book_info.PUBLISHER_MAX)
    genre_code: str | None = None  # `genres.GENRES`의 코드 하나(아니면 422), 비우면 모름

    @field_validator("genre_code", mode="before")
    @classmethod
    def _genre(cls, value: Any) -> Any:
        return genres.normalize_genre(value) if isinstance(value, str) or value is None else value

    @field_validator("isbn13", mode="before")
    @classmethod
    def _isbn(cls, value: Any) -> Any:
        return (
            book_info.normalize_isbn13(value) if isinstance(value, str) or value is None else value
        )

    @field_validator("author", "publisher", mode="before")
    @classmethod
    def _text(cls, value: Any) -> Any:
        return book_info.clean_text(value) if isinstance(value, str) or value is None else value


class ProductIn(BookInfoIn):
    seller_id: str
    name: str
    category_code: str
    price: int = Field(ge=0)
    stock: int = Field(ge=0)


class SellerProductIn(BookInfoIn):
    name: str
    category_code: str
    price: int = Field(ge=0)
    stock: int = Field(ge=0)


class SellerIn(BaseModel):
    """판매자 입점 신청. 처음 올릴 상품을 같이 받는다 (입점 심사가 상품 가격·수를 본다)."""

    name: str
    category_codes: list[str] = Field(min_length=1)
    documents_verified: bool = False
    products: list[SellerProductIn] = Field(default_factory=list, max_length=500)


class ProductPatch(BookInfoIn):
    """상품 수정. 책 정보는 `null`·빈 글을 보내면 지운다(칸을 안 보내면 그대로)."""

    name: str | None = None
    price: int | None = Field(default=None, ge=0)
    stock: int | None = Field(default=None, ge=0)
    active: bool | None = None


class OrderLine(BaseModel):
    product_id: str
    qty: int = Field(gt=0)


class OrderIn(BaseModel):
    buyer_id: str | None = None  # 비우면 로그인 토큰의 구매자(보내면 같아야 한다)
    items: list[OrderLine]
    points_used: int = Field(default=0, ge=0)  # 포인트 사용분(원). 품목 금액 비율로 나눠 기록한다
    # 같은 구매자의 같은 키는 주문 하나다. 재시도·중복 전송이 주문을 두 번 만들지 않게 한다
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    # 본인의 쿠폰 한 건과 쓸 장수. 할인은 상품 금액 범위 안이고 배송비는 제외한다
    coupon_id: str | None = None
    coupon_units: int = Field(default=1, ge=1)
    # 유입 출처(utm_source·utm_medium·utm_campaign). 선택. 영문·숫자·`-`·`_`·`.` 64자까지이고
    # 어긋난 값은 주문을 막지 않고 그 값만 비운다. 멱등 비교에는 넣지 않는다
    src_source: str | None = None
    src_medium: str | None = None
    src_campaign: str | None = None


class CartPriceIn(BaseModel):
    items: list[OrderLine]
    points_used: int = Field(default=0, ge=0)
    coupon_id: str | None = None
    coupon_units: int = Field(default=1, ge=1)
    buyer_id: str | None = None  # 쿠폰을 계산할 때만 필요하다(본인 쿠폰인지 확인한다)


class SessionIn(BaseModel):
    buyer_id: str  # 데모 사용자 선택. 비밀번호·토큰은 없다


class ProfileIn(BaseModel):
    """구매자 프로필. 연령대 구간과 성별 코드만 받는다(생년월일·나이·직업은 받지 않는다).

    보낸 칸만 바꾼다(안 보낸 칸은 그대로). null이면 지운다. 칸이 하나도 없으면 422다.
    """

    model_config = {"extra": "forbid"}

    age_band: Literal["10s", "20s", "30s", "40s", "50s_plus"] | None = None
    gender: Literal["female", "male", "no_answer"] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> ProfileIn:
        if not self.model_fields_set:
            raise ValueError("age_band·gender 중 하나는 보내야 한다")
        return self


class SignupIn(BaseModel):
    """이메일 가입. 연령대·성별·직업군은 선택이고 코드값만 받는다(안 보내면 모르는 것이다)."""

    model_config = {"extra": "forbid"}

    email: str = Field(max_length=accounts.EMAIL_MAX + 64)
    password: str = Field(max_length=1024)  # 길이 규칙은 서비스(WEAK_PASSWORD)가 본다
    display_name: str = Field(max_length=200)
    agree_terms: Literal[True]
    agree_privacy: Literal[True]
    age_band: Literal["10s", "20s", "30s", "40s", "50s_plus"] | None = None
    gender: Literal["female", "male", "no_answer"] | None = None
    occupation: Literal["student", "employee", "self_employed", "other", "no_answer"] | None = None


class LoginIn(BaseModel):
    model_config = {"extra": "forbid"}

    email: str = Field(max_length=1024)
    password: str = Field(max_length=1024)


class DemoLoginIn(BaseModel):
    model_config = {"extra": "forbid"}

    buyer_id: str = Field(max_length=32)


class MeIn(BaseModel):
    """내 정보 수정. 보낸 칸만 바꾼다(연령대·성별·직업군은 null이면 지운다). 칸이 없으면 422."""

    model_config = {"extra": "forbid"}

    display_name: str | None = Field(default=None, max_length=200)
    age_band: Literal["10s", "20s", "30s", "40s", "50s_plus"] | None = None
    gender: Literal["female", "male", "no_answer"] | None = None
    occupation: Literal["student", "employee", "self_employed", "other", "no_answer"] | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> MeIn:
        if not self.model_fields_set:
            raise ValueError("바꿀 칸을 하나는 보내야 한다")
        return self


class PasswordIn(BaseModel):
    model_config = {"extra": "forbid"}

    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)  # 길이 규칙은 서비스(WEAK_PASSWORD)가 본다


class ShippedIn(BaseModel):
    shipped_at: AwareDatetime | None = None  # 비우면 지금(시연·시드용으로 지난 시각도 가능)


class DeliveredIn(BaseModel):
    delivered_at: AwareDatetime | None = None  # 비우면 지금(시연·시드용으로 지난 시각도 가능)


class PaymentIn(BaseModel):
    method_code: str = "card"
    # 가짜 결제 서버에 성공·실패를 요청한다. 장애 주입은 이후 PR에서 게이트웨이가 정한다
    simulate: Literal["success", "failure"] | None = None


class TossConfirmIn(BaseModel):
    """토스 결제위젯 성공 리다이렉트의 값 그대로. 금액은 시도 금액과 비교만 한다(믿지 않는다)."""

    toss_order_id: str = Field(pattern=r"^[A-Za-z0-9_-]{6,64}$")
    payment_key: str = Field(min_length=1, max_length=200)
    amount: int = Field(ge=0)


class CancelIn(BaseModel):
    reason_code: Literal["buyer", "seller", "system"] = "buyer"


PG_SLOW_MAX_MS = 10_000  # 결제사 쪽 지연은 결제마다 실제로 기다리므로 상한을 둔다


class PaymentVersionIn(BaseModel):
    version: Literal["v1", "v2"]  # PAYMENT_VERSIONS


class PaymentRollbackIn(BaseModel):
    to_version: Literal["v1", "v2"]


class FaultIn(BaseModel):
    # lost_response: 결제사는 승인했는데 응답이 끊김(시간 초과 → 다시 확인하면 승인으로 나온다)
    # pg_slow: 결제사 쪽 지연(시험용). 장애 스위치가 아니라 AI가 끌 수 없다(`DELETE .../faults/pg`)
    kind: Literal["latency", "failure", "lost_response", "pg_slow"]
    delay_ms: int = Field(default=2000, ge=0, le=60000)  # latency·pg_slow일 때 더할 지연

    @model_validator(mode="after")
    def _pg_slow_limit(self) -> FaultIn:
        if self.kind == "pg_slow" and not 0 < self.delay_ms <= PG_SLOW_MAX_MS:
            raise ValueError(f"pg_slow 지연은 1~{PG_SLOW_MAX_MS}ms")
        return self


class BannerIn(BaseModel):
    notice_id: str
    text: str = Field(min_length=1, max_length=200)  # 공개 문구. 화면이 이스케이프해서 보여 준다
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    context_id: str | None = None  # 사후 추적용 사건 번호(선택)


class ClaimItemIn(BaseModel):
    order_item_id: str
    qty: int = Field(ge=1)


class ClaimIn(BaseModel):
    """고객 반품·환불 신청. 금액은 받지 않는다(고객지원이 주문 사실로 계산한다)."""

    buyer_id: str | None = None  # 비우면 로그인 토큰의 구매자(보내면 같아야 한다)
    customer_name: str = Field(min_length=1, max_length=100)
    order_id: str
    type: Literal["cancel", "return", "exchange"]
    reason: Literal[
        "change_of_mind",
        "defect",
        "wrong_item",
        "damaged",
        "missing_item",
        "late_delivery",
        "other",
    ]
    items: list[ClaimItemIn] | None = None  # 없으면 주문 전체
    unsealed: bool = False
    evidence_count: int = Field(default=0, ge=0)
    description: str = Field(default="", max_length=2000)  # 고객이 쓴 문장. untrusted로만 넘긴다


class InquiryImageIn(BaseModel):
    """문의에 붙이는 사진 한 장. 내용은 로그·오류에 싣지 않고 고객지원에 `untrusted`로만 넘긴다."""

    model_config = {"extra": "forbid"}

    ref: str = Field(max_length=200)
    media_type: Literal["image/jpeg", "image/png", "image/webp"] = "image/jpeg"
    data_base64: (
        str  # 크기는 `inquiries.clean_images`가 확인한다(413, 사진 값은 오류에 싣지 않는다)
    )


class InquiryIn(BaseModel):
    """고객 문의 접수. 주문 선택이 필수다(결정 1). 제목·본문 길이는 서버가 확인한다."""

    model_config = {"extra": "forbid"}

    buyer_id: str | None = None  # 비우면 로그인 토큰의 구매자(보내면 같아야 한다)
    order_id: str
    subject: str | None = None
    body: str
    images: list[InquiryImageIn] = Field(
        default_factory=list
    )  # 영수증 사진. 서버가 개수·크기를 확인


class ClaimResultIn(BaseModel):
    """고객지원이 판정한 클레임 결과를 되돌려 쓴다. `result`는 고객지원이 돌려준 클레임 그대로다."""

    model_config = {"extra": "forbid"}

    support_claim_id: int | None = Field(default=None, ge=1)  # 클레임이 안 만들어졌으면 비운다
    status: str = Field(min_length=1, max_length=40)
    result: dict


class InquirySyncedIn(BaseModel):
    """AXcellent가 문의를 읽어 처리했다는 표시. 고객지원이 만든 문의 번호를 함께 보낸다."""

    model_config = {"extra": "forbid"}

    support_inquiry_id: int = Field(ge=1)


class IntakeFailedIn(BaseModel):
    """읽어 간 쪽이 접수를 처리하지 못했다는 알림. 사유는 짧은 코드다(원문을 싣지 않는다)."""

    model_config = {"extra": "forbid"}

    reason: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.:-]+$")


class InquiryViewIn(BaseModel):
    """고객지원이 정한 고객용 문의 값. 이 서버가 허용한 키만 남기고 값은 고치지 않는다."""

    model_config = {"extra": "forbid"}

    view: dict
    view_digest: str = Field(min_length=1, max_length=64)


class IntakeSyncedIn(BaseModel):
    """읽어 간 쪽의 처리 결과: 짧은 코드(outcome)와 고객지원 쪽 번호(ref, 있을 때)."""

    model_config = {"extra": "forbid"}

    outcome: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.:-]+$")
    ref: str | None = Field(default=None, max_length=64)
    # 아이디어에서만 쓴다(받지 않은 아이디어에 붙일 고객용 안내 한 줄). 다른 경로는 받으면 422다
    notice: str | None = Field(default=None, max_length=idea_inbox.NOTICE_CHARS)


class IdeaIn(BaseModel):
    """고객 아이디어. 문의·불만과 따로 받는다. 글은 `untrusted`로만 넘긴다."""

    model_config = {"extra": "forbid"}

    buyer_id: str | None = None  # 비우면 로그인 토큰의 구매자(보내면 같아야 한다)
    kind: Literal["event", "feature", "improvement", "product", "other"]
    text: str = Field(min_length=1, max_length=500)


class InquiryFeedbackIn(BaseModel):
    """해결된 문의에 고객이 남기는 평가. 후기 글은 `untrusted`로만 넘긴다."""

    model_config = {"extra": "forbid"}

    buyer_id: str | None = None  # 비우면 로그인 토큰의 구매자(보내면 같아야 한다)
    rating: int = Field(ge=1, le=5)
    resolved: bool  # 고객이 "문제가 해결됐다"고 답했는지
    comment: str = Field(default="", max_length=300)


class ReturnReceivedIn(BaseModel):
    order_id: str  # 클레임의 주문(고객지원 응답과 대조한다)
    inspection: Literal["pass", "fail", "partial"] | None = None


class RefundIn(BaseModel):
    refund_amount: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1)  # `claim.context_id:refund_request:claim:<id>`
    context_id: str | None = None


class CouponIn(BaseModel):
    idempotency_key: str = Field(min_length=1)  # `context_id + 액션 + 대상`
    purpose_code: Literal["campaign", "compensation"]
    buyer_id: str
    count: int = Field(gt=0)
    unit_value: int = Field(gt=0)
    campaign_id: str | None = None
    context_id: str | None = None  # 사후 추적용 사건 번호(선택). 멱등 비교에는 쓰지 않는다
    expires_at: AwareDatetime | None = None  # 이 시각부터 주문에 쓸 수 없다(없으면 무기한)


# 브라우저(shop-web 3000, dashboard-web 3001)가 직접 부르므로 허용할 출처만 연다.
# `SHOP_CORS_ORIGINS`(쉼표로 구분)로 바꾼다.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3001,http://127.0.0.1:3001"
)

# 관리 API 허용 요청자. 장애 해제는 infra 롤백이 관문을 거쳐 부른다(후속 결정 15)
INJECT_ALLOWED = {"admin"}
# 상품 등록·수정(재고 보충 포함)은 운영자(admin)만
PRODUCT_WRITERS = {"admin"}
RELEASE_ALLOWED = {"admin", "infra"}
# 결제 설정 버전(잘못된 배포 시연): 배포(아무 버전으로)는 사람만, 이전 버전으로 되돌리기만 인프라
# 롤백(관문을 거친 rollback_deployment)도 부른다. 인프라 토큰은 되돌리기 경로 하나만 통한다
PAYMENT_VERSION_DEPLOYERS = {"admin"}
PAYMENT_VERSION_ROLLBACK = {"admin", "infra"}
# 공지·쿠폰 쓰기는 마케팅 관문 토큰만, 쿠폰 조회는 사후 검증·중복 확인을 하는 부서와
# 캠페인 성과를 세는 data(읽기 전용)까지
MARKETING_ONLY = {"marketing"}
COUPON_READERS = {"marketing", "support", "finance", "orchestrator", "data"}
# 주문 유입 출처 조회(#647)는 데이터분석 커넥터 토큰(data)만. 관리자(admin)도 뺀다
ATTRIBUTION_READERS = {"data"}
# 현금 환불(SHOP-12)은 고객지원 관문 토큰만(마케팅 토큰 거부)
REFUND_ALLOWED = {"support"}
# 문의함 읽기·읽음 표시는 AXcellent 고객지원(과 운영자)만. 문의 원문과 사진이 들어 있다
INQUIRY_READERS = {"support", "admin"}
# 데이터 조회(읽기 전용)는 분석을 하는 data와 관리자만
DATA_READERS = {"data", "admin"}
# 판매 집계(읽기 전용)는 data·관리자에 더해 AXcellent 고객지원(`support`)도 읽는다
# (책별 클레임 비율의 분모)
SALES_READERS = {"support", "data", "admin"}
# 총괄 사건 읽기 중계는 관리자와 읽기 전용 열람자(`viewer`)만.
# 사람 로그인(소유자·공동소유자·분야 관리자)은 사람 인증 뒤에 붙인다
ORCHESTRATOR_VIEWERS = {"admin", "viewer"}
# 장애 주입 상태 읽기(GET)는 열람자도 본다. 주입·해제는 그대로 admin·infra만
FAULT_STATE_READERS = INJECT_ALLOWED | RELEASE_ALLOWED | {"viewer"}
# 읽기 전용 열람 토큰(`SHOP_VIEWER_TOKEN`). 에이전트 토큰 명부(`AX_AGENT_TOKENS`)와 따로 둔다.
# 명부에 넣으면 총괄 등 다른 서버가 명부의 모든 에이전트를 받아 주는 길(`require_agent()`)이
# 같이 열린다.
# 이 서버의 `ORCHESTRATOR_VIEWERS`·`FAULT_STATE_READERS`에서만 통하고 그 밖은 403이다.
VIEWER_TOKEN_ENV = "SHOP_VIEWER_TOKEN"
VIEWER_TOKEN_MIN_LENGTH = 24  # 짧은 값은 켜지 않는다


# 판매 서버가 발급한 연결 키. AXcellent 설치·서비스마다 하나씩 준다.
#   SHOP_CLIENT_KEYS="<설치>/<서비스>:<키>:<read|write>,..."
#   예: ax-251/inquiry:<키>:write, ax-251/claims:<키>:write, ax-local/inquiry:<키>:read
# 서비스 이름이 요청자가 되어 아래 허용 목록을 그대로 탄다(서비스는 원래 부서 이름으로도 인정).
# `read` 키는 GET·HEAD만 된다. 로컬처럼 읽기만 하는 설치(관찰 모드)가 판매 서버 상태를 못 바꾼다.
# 쓰기(읽음 표시, 클레임 결과, 환불, 쿠폰·배너, 장애 해제 등)는 주 담당 설치의 `write` 키만 한다.
CLIENT_KEYS_ENV = "SHOP_CLIENT_KEYS"
CLIENT_KEY_MIN_LENGTH = 24  # 짧은 키는 켜지 않는다
CLIENT_KEY_MODES = frozenset({"read", "write"})
# AXcellent 에이전트 토큰(`AX_AGENT_TOKENS`)을 받을지. 연결 키로 다 옮긴 뒤 off로 끈다
AGENT_TOKENS_ENV = "SHOP_AGENT_TOKENS"
READ_METHODS = frozenset({"GET", "HEAD"})


@dataclass(frozen=True)
class ClientKey:
    install: str
    service: str
    mode: str


def client_keys() -> dict[str, ClientKey]:
    """키 → (설치, 서비스, 권한). 형식이 틀리거나 짧은 항목은 버린다.

    키 값은 로그에 남기지 않는다.
    """
    keys: dict[str, ClientKey] = {}
    for item in os.getenv(CLIENT_KEYS_ENV, "").split(","):
        parts = item.strip().split(":")
        if len(parts) != 3:
            continue
        who, key, mode = (p.strip() for p in parts)
        install, sep, service = who.partition("/")
        if not (sep and install and service) or mode not in CLIENT_KEY_MODES:
            continue
        if len(key) < CLIENT_KEY_MIN_LENGTH:
            continue
        keys[key] = ClientKey(install, service, mode)
    return keys


def _client_key(token: str) -> ClientKey | None:
    found = None
    for key, client in client_keys().items():
        if hmac.compare_digest(key.encode(), token.encode()):
            found = client
    return found


def _agent_tokens_on() -> bool:
    return (os.getenv(AGENT_TOKENS_ENV) or "on").strip().lower() != "off"


def _is_human_token(token: str) -> bool:
    """사람 토큰(`SAFETY_HUMAN_TOKENS="이름:토큰,..."`)인가. 관리 API에는 쓸 수 없다(403)."""
    for item in os.getenv("SAFETY_HUMAN_TOKENS", "").split(","):
        name, sep, known = item.strip().partition(":")
        if sep and name and known and hmac.compare_digest(known, token):
            return True
    return False


def _is_viewer_token(token: str) -> bool:
    """읽기 전용 열람 토큰인가. 설정이 없거나 너무 짧으면 꺼져 있다."""
    known = os.getenv(VIEWER_TOKEN_ENV, "").strip()
    return len(known) >= VIEWER_TOKEN_MIN_LENGTH and hmac.compare_digest(known, token)


def _bearer(authorization: str | None) -> str | None:
    """`Authorization: Bearer <토큰>`의 토큰. 없거나 형식이 다르면 None."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def _service_requester(request: Request, token: str) -> str | None:
    """연결 키·에이전트 토큰이면 요청자 이름, 둘 다 아니면 None(열람 토큰은 따로 본다).

    읽기 전용 연결 키로 쓰기를 하거나 사람 토큰을 내면 403이다.
    """
    client = _client_key(token)
    if client is not None:
        if client.mode == "read" and request.method not in READ_METHODS:
            raise ShopError("READ_ONLY_KEY", "읽기 전용 연결 키로는 바꿀 수 없다", 403)
        return client.service
    if not _agent_tokens_on():
        return None
    try:
        return verify_token(token)  # 요청자는 토큰에서 꺼낸다
    except PermissionError as error:
        if _is_human_token(token):
            raise ShopError("FORBIDDEN", "에이전트 토큰이 필요하다", 403) from error
        return None


def _allows(allowed: set[str], requester: str) -> bool:
    # 서비스로 나뉜 에이전트(claims 등)는 원래 부서 이름(support)으로도 인정한다
    return requester in allowed or department_of(requester) in allowed


def _caller(allowed: set[str]) -> Callable[..., str]:
    def dependency(request: Request, authorization: str | None = Header(default=None)) -> str:
        token = _bearer(authorization)
        if token is None:
            raise ShopError("UNAUTHORIZED", "토큰이 필요하다", 401)
        if _is_viewer_token(token):
            if "viewer" not in allowed:
                raise ShopError("FORBIDDEN", "읽기 전용 토큰으로는 호출할 수 없는 API다", 403)
            return "viewer"
        requester = _service_requester(request, token)
        if requester is None:
            if not _agent_tokens_on():
                raise ShopError("UNAUTHORIZED", "연결 키를 검증할 수 없다", 401)
            raise ShopError("UNAUTHORIZED", "토큰을 검증할 수 없다", 401)
        if not _allows(allowed, requester):
            raise ShopError("FORBIDDEN", "이 API를 호출할 수 없는 요청자다", 403)
        return requester

    return dependency


# --- 구매자 본인 확인 ---------------------------------------------------------------
# `SHOP_BUYER_AUTH`: `required`(기본) — 구매자 경로는 로그인 토큰의 구매자만 쓴다.
#   `legacy` — 토큰이 없으면 요청의 `buyer_id`를 그대로 믿는다(옛 동작. 시험·전환 기간용).
#   모르는 값은 `required`로 본다(안전한 쪽).
# `SHOP_ORDERS_PUBLIC_READ`: `on`(기본) — 토큰 없는 주문 조회(`GET /orders`·`/orders/{id}`)는
#   지금처럼 전체를 준다(AXcellent 재무·고객지원이 이렇게 읽는다). `off`면 토큰이 필요하다.
BUYER_AUTH_ENV = "SHOP_BUYER_AUTH"
ORDERS_PUBLIC_READ_ENV = "SHOP_ORDERS_PUBLIC_READ"
# 구매자를 대신해 구매자 경로를 부를 수 있는 서비스 요청자(가상 주문 스크립트·운영자)
ACT_FOR_BUYER = {"admin"}


def _buyer_auth_legacy() -> bool:
    return (os.getenv(BUYER_AUTH_ENV) or "").strip().lower() == "legacy"


def _orders_public_read() -> bool:
    return (os.getenv(ORDERS_PUBLIC_READ_ENV) or "on").strip().lower() != "off"


@dataclass(frozen=True)
class Who:
    """요청한 쪽. `kind`가 `buyer`면 `name`은 구매자 ID, `service`면 요청자 이름이다."""

    kind: Literal["anonymous", "service", "buyer"]
    name: str | None = None


def _identify(request: Request) -> Who:
    """토큰 판별 순서: 열람 토큰·연결 키·에이전트 토큰(`_caller`와 같은 검증) → 구매자 로그인
    토큰(`Accounts.resolve`) → 401. 토큰이 없으면 `anonymous`다. 토큰 값은 로그에 남기지 않는다.
    """
    token = _bearer(request.headers.get("authorization"))
    if token is None:
        return Who("anonymous")
    if _is_viewer_token(token):
        return Who("service", "viewer")
    requester = _service_requester(request, token)
    if requester is not None:
        return Who("service", requester)
    buyer_id = request.app.state.accounts.resolve(token)
    if buyer_id is not None:
        return Who("buyer", buyer_id)
    raise ShopError("UNAUTHORIZED", "토큰을 검증할 수 없다", 401)


def _acting_admin(who: Who) -> None:
    """서비스 요청자가 구매자를 대신할 수 있는지(`admin`만). 아니면 403."""
    if who.name == "viewer" or not _allows(ACT_FOR_BUYER, who.name or ""):
        raise ShopError("FORBIDDEN", "구매자를 대신할 수 없는 요청자다", 403)


def _buyer(request: Request, claimed: str | None = None) -> str:
    """구매자 경로의 구매자 ID. `claimed`는 요청 본문·쿼리·경로의 `buyer_id`(없으면 None).

    - 구매자 로그인 토큰: 토큰의 구매자. `claimed`가 다르면 403 `BUYER_MISMATCH`.
    - `admin` 토큰: `claimed`(없으면 422). 다른 서비스 토큰은 403.
    - 토큰 없음: `required`면 401 `LOGIN_REQUIRED`, `legacy`면 `claimed`(없으면 422).
    - 알 수 없는 토큰: 401 `UNAUTHORIZED`.
    """
    who = _identify(request)
    if who.kind == "buyer":
        if claimed is not None and claimed != who.name:
            raise ShopError("BUYER_MISMATCH", "로그인한 구매자와 다르다", 403)
        return str(who.name)
    if who.kind == "service":
        _acting_admin(who)
    elif not _buyer_auth_legacy():
        raise ShopError("LOGIN_REQUIRED", "로그인이 필요하다", 401)
    if claimed is None:
        raise ShopError("BUYER_REQUIRED", "buyer_id가 필요하다", 422)
    return claimed


def _session_buyer(request: Request) -> tuple[str, str]:
    """`/me` 경로: 구매자 로그인 토큰만 받는다. `(구매자 ID, 토큰)`."""
    token = _bearer(request.headers.get("authorization"))
    if token is None:
        raise ShopError("LOGIN_REQUIRED", "로그인이 필요하다", 401)
    who = _identify(request)
    if who.kind != "buyer":
        raise ShopError("FORBIDDEN", "구매자 로그인 토큰이 필요하다", 403)
    return str(who.name), token


def _order_scope(request: Request) -> str | None:
    """주문 조회 범위. 구매자 토큰이면 그 구매자 ID, 전체를 볼 수 있으면 None.

    서비스 토큰·연결 키는 전체(열람 토큰은 403). 토큰이 없으면 `SHOP_ORDERS_PUBLIC_READ=on`일 때
    지금처럼 전체, `off`면 401. 알 수 없는 토큰은 401.
    """
    who = _identify(request)
    if who.kind == "buyer":
        return who.name
    if who.kind == "service":
        if who.name == "viewer":
            raise ShopError("FORBIDDEN", "읽기 전용 토큰으로는 호출할 수 없는 API다", 403)
        return None
    if not _orders_public_read():
        raise ShopError("UNAUTHORIZED", "토큰이 필요하다", 401)
    return None


def _ok(data: Any, **meta: Any) -> dict[str, Any]:
    return {"data": data, "meta": meta}


def _query(model: type[BaseModel], **values: Any) -> Any:
    """쿼리 값을 계약 모델로 검증한다. 시간대가 없는 시각·모르는 지표는 400이다."""
    try:
        return model.model_validate(values)
    except ValueError as error:
        raise ShopError(
            "INVALID_QUERY", f"조회 조건이 올바르지 않다: {error.__class__.__name__}"
        ) from error


def create_app(
    db: Db | None = None,
    seed_data: bool = True,
    toss_transport: httpx.AsyncBaseTransport | None = None,  # 시험용(토스 흉내)
) -> FastAPI:
    set_agent_name("shop")  # SHOP_MOCK=1로 목 모드(환경변수 접두사 SHOP)
    database = db or Db()
    if seed_data:
        seed(database)
    shop = Shop(database)
    payment_config = toss.load_config()
    if payment_config.warning:  # 키 값은 담지 않는다
        logging.getLogger(__name__).warning(payment_config.warning)
    toss_payments = (
        toss.TossPayments(shop, toss.TossClient(payment_config.secret_key, toss_transport))
        if payment_config.provider == toss.TOSS_TEST and payment_config.secret_key
        else None
    )
    promotions = Promotions(database)
    lookups = Lookups(database)
    sales_export = SalesExport(database)
    refunds = Refunds(database)
    storefront = Storefront(database)
    account_service = Accounts(database)
    interval = float(os.getenv("SHOP_MONITOR_INTERVAL_S") or 5)  # 0이면 주기 판정을 끈다

    recheck_every = float(os.getenv("SHOP_PAYMENT_RECHECK_S") or 30)  # 시간 초과 다시 확인 간격
    last_recheck = [0.0]

    async def _monitor_loop() -> None:
        """결제 요청이 없어도 지속 시간을 판정하도록 주기적으로 평가한다."""
        while True:
            await asyncio.sleep(interval)
            shop.evaluate_monitor()
            now = asyncio.get_running_loop().time()
            if recheck_every > 0 and now - last_recheck[0] >= recheck_every:
                last_recheck[0] = now
                try:
                    await toss.recheck_timeouts(
                        shop, toss_payments.client if toss_payments else None
                    )
                except Exception:  # 다시 확인이 실패해도 판정 루프는 계속 돈다
                    logging.getLogger(__name__).exception("시간 초과 결제 다시 확인 실패")
            await outbox.drain(database)  # 쌓인 미발행은 여기서 묶음씩 비운다

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(_monitor_loop()) if interval > 0 else None
        retention_task = retention.start_background(database)  # 파일 DB일 때만, 기본 dry-run
        try:
            yield
        finally:
            if task:
                task.cancel()
            if retention_task:
                retention_task.cancel()

    app = FastAPI(title="ax-shop-api", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            origin.strip()
            for origin in (os.getenv("SHOP_CORS_ORIGINS") or DEFAULT_CORS_ORIGINS).split(",")
            if origin.strip()
        ],
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
    )
    app.state.shop = shop
    app.state.promotions = promotions
    app.state.lookups = lookups
    app.state.refunds = refunds
    app.state.accounts = account_service
    inquiry_limiter = inquiries.InquiryRateLimiter()
    app.state.inquiry_limiter = inquiry_limiter
    feedback_limiter = inquiries.InquiryRateLimiter()  # 문의 접수와 한도를 따로 센다
    idea_limiter = inquiries.InquiryRateLimiter()  # 아이디어도 문의와 따로 센다

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        """422 본문에서 `input`(요청이 보낸 값)을 뺀다. 사진·본문이 오류로 되돌아가지 않게."""
        errors = [{k: v for k, v in e.items() if k != "input"} for e in error.errors()]
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

    @app.exception_handler(ShopError)
    async def _shop_error(_: Request, error: ShopError) -> JSONResponse:
        body = {"error": {"code": error.code, "message": error.message}}
        return JSONResponse(status_code=error.status, content=body)

    @app.get("/health")
    def health() -> dict:
        return {"data": {"status": "ok"}, "meta": {}}

    @app.get("/api/shop/users")
    async def list_users(request: Request) -> dict:
        """구매자 목록은 `admin` 토큰만. `legacy`에서 토큰 없이 부르면 옛 동작대로 준다."""
        authorization = request.headers.get("authorization")
        if not (_buyer_auth_legacy() and _bearer(authorization) is None):
            _caller(INJECT_ALLOWED)(request, authorization)
        return _ok(storefront.list_users())

    @app.get("/api/shop/users/{buyer_id}")
    async def get_user(buyer_id: str, request: Request) -> dict:
        """본인(로그인 토큰) 또는 `admin`만."""
        return _ok(storefront.get_user(_buyer(request, buyer_id)))

    @app.patch("/api/shop/users/{buyer_id}")
    async def update_profile(buyer_id: str, body: ProfileIn, request: Request) -> dict:
        """연령대 구간(`age_band`)·성별(`gender`)을 정하거나(null이면 지운다). 보낸 칸만 바꾼다.

        본인(로그인 토큰) 또는 `admin`만.
        """
        fields = {name: getattr(body, name) for name in body.model_fields_set}
        return _ok(storefront.set_profile(_buyer(request, buyer_id), fields))

    @app.get("/api/shop/me")
    async def get_me(request: Request) -> dict:
        """로그인한 구매자 본인의 정보(구매자 로그인 토큰만)."""
        buyer_id, _token = _session_buyer(request)
        return _ok(account_service.profile(buyer_id))

    @app.patch("/api/shop/me")
    async def update_me(body: MeIn, request: Request) -> dict:
        """이름·연령대·성별·직업군 중 보낸 칸만 바꾼다(구매자 로그인 토큰만)."""
        buyer_id, _token = _session_buyer(request)
        fields = {name: getattr(body, name) for name in body.model_fields_set}
        return _ok(account_service.update_profile(buyer_id, fields))

    @app.post("/api/shop/me/password")
    async def change_my_password(body: PasswordIn, request: Request) -> dict:
        """현재 비밀번호가 맞으면 바꾸고, 이 토큰을 뺀 다른 로그인은 모두 끊는다."""
        buyer_id, token = _session_buyer(request)
        account_service.change_password(buyer_id, body.current, body.new, keep_token=token)
        return _ok({"changed": True})

    @app.post("/api/shop/auth/signup", status_code=201)
    async def auth_signup(body: SignupIn) -> dict:
        token, user = account_service.signup(
            body.email,
            body.password,
            body.display_name,
            body.age_band,
            body.gender,
            body.occupation,
        )
        return _ok({"token": token, "user": user})

    @app.post("/api/shop/auth/login")
    async def auth_login(body: LoginIn) -> dict:
        token, user = account_service.login(body.email, body.password)
        return _ok({"token": token, "user": user})

    @app.post("/api/shop/auth/logout")
    async def auth_logout(authorization: Annotated[str | None, Header()] = None) -> dict:
        """보낸 로그인 토큰을 무효로 한다. 토큰이 없거나 이미 무효여도 성공이다."""
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            account_service.logout(token.strip())
        return _ok({})

    @app.get("/api/shop/auth/config")
    async def auth_config() -> dict:
        return _ok({"demo_login": accounts.demo_login_enabled()})

    @app.post("/api/shop/auth/demo-login")
    async def auth_demo_login(body: DemoLoginIn) -> dict:
        """`SHOP_DEMO_LOGIN=on`일 때만 열린다(옛 데모 구매자에게 비밀번호 없이 세션을 준다)."""
        if not accounts.demo_login_enabled():
            raise ShopError("NOT_FOUND", "없는 경로다", 404)
        inquiries.buyer_number(body.buyer_id)
        token, user = account_service.demo_login(body.buyer_id)
        return _ok({"token": token, "user": user})

    @app.post("/api/shop/sessions")
    async def select_user(body: SessionIn) -> dict:
        """데모 사용자 고르기. `SHOP_DEMO_LOGIN=on`일 때만 로그인 토큰을 준다(`{user, token}`).

        꺼져 있으면 404다. 다만 `SHOP_BUYER_AUTH=legacy`에서는 옛 동작(토큰 없이 사용자만)을 둔다
        (legacy는 어차피 `buyer_id`를 믿으므로 새로 열리는 것이 없다).
        """
        if accounts.demo_login_enabled():
            inquiries.buyer_number(body.buyer_id)
            token, user = account_service.demo_login(body.buyer_id)
            return _ok({"user": user, "token": token})
        if _buyer_auth_legacy():
            return _ok({"user": storefront.get_user(body.buyer_id)})
        raise ShopError("NOT_FOUND", "없는 경로다", 404)

    @app.get("/api/shop/categories")
    async def list_categories() -> dict:
        return _ok(storefront.list_categories())

    @app.post("/api/shop/cart/price")
    async def price_cart(body: CartPriceIn, request: Request) -> dict:
        """쿠폰을 쓸 때만 구매자를 확인한다(`_buyer`). 쿠폰이 없으면 토큰 없이 계산한다."""
        lines = [(i.product_id, i.qty) for i in body.items]
        buyer_id = body.buyer_id
        # legacy에서 토큰도 buyer_id도 없으면 옛 동작(400 BUYER_REQUIRED)을 그대로 둔다
        anonymous = _bearer(request.headers.get("authorization")) is None
        old_rule = _buyer_auth_legacy() and anonymous and buyer_id is None
        if body.coupon_id and not old_rule:
            buyer_id = _buyer(request, buyer_id)
        return _ok(
            storefront.price_cart(
                lines, body.points_used, body.coupon_id, body.coupon_units, buyer_id
            )
        )

    @app.get("/api/shop/sellers")
    async def list_sellers() -> dict:
        return _ok(shop.list_sellers())

    @app.get("/api/shop/products")
    async def list_products(seller_id: str | None = None, category_code: str | None = None) -> dict:
        return _ok(shop.list_products(seller_id, category_code))

    @app.get("/api/shop/products/{product_id}")
    async def get_product(product_id: str) -> dict:
        return _ok(shop.get_product(product_id))

    @app.post("/api/shop/sellers", status_code=201)
    async def register_seller(body: SellerIn, _: str = Depends(_caller(PRODUCT_WRITERS))) -> dict:
        """판매자 입점 (운영자만). `seller.registered`로 총괄이 입점 심사를 연다."""
        created = shop.register_seller(
            body.name,
            body.category_codes,
            body.documents_verified,
            [p.model_dump() for p in body.products],
        )
        await outbox.flush(database)
        return _ok(created)

    @app.post("/api/shop/products", status_code=201)
    async def create_product(body: ProductIn, _: str = Depends(_caller(PRODUCT_WRITERS))) -> dict:
        return _ok(shop.create_product(**body.model_dump()))

    @app.patch("/api/shop/products/{product_id}")
    async def update_product(
        product_id: str, body: ProductPatch, _: str = Depends(_caller(PRODUCT_WRITERS))
    ) -> dict:
        changes = body.model_dump(exclude_none=True)
        for key in ("isbn13", "author", "publisher", "genre_code"):  # 보냈으면 null도 "지움"이다
            if key in body.model_fields_set:
                changes[key] = getattr(body, key)
        return _ok(shop.update_product(product_id, changes))

    # 동기 핸들러: FastAPI가 스레드에서 돌려 DB를 읽는 동안 이벤트 루프(/health 등)를 막지 않는다.
    # Db는 연결 하나를 RLock으로 감싸고(check_same_thread=False) sqlite는 직렬 모드라
    # 스레드에서 써도 된다
    def _own_order(request: Request, order_id: str) -> None:
        """주문 하나를 바꾸는 구매자 경로: 구매자 토큰이면 본인 주문만(남의 주문은 없는 주문과
        같은 404), `admin`은 누구 주문이든, 토큰이 없으면 `required`에서 401·`legacy`에서 통과.
        """
        who = _identify(request)
        if who.kind == "buyer":
            if shop.get_order(order_id)["buyer_id"] != who.name:
                raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
        elif who.kind == "service":
            _acting_admin(who)
        elif not _buyer_auth_legacy():
            raise ShopError("LOGIN_REQUIRED", "로그인이 필요하다", 401)

    @app.get("/api/shop/orders")
    def list_orders(
        request: Request,
        buyer_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: Annotated[int, Query(ge=1, le=ORDER_PAGE_MAX)] = ORDER_PAGE_DEFAULT,
        cursor: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_-]{1,64}$")] = None,
    ) -> dict:
        """order_id 순 한 페이지. 다음 페이지는 `meta.next_cursor`를 cursor로 준다(끝이면 null).

        구매자 토큰이면 본인 주문만, 서비스 토큰·연결 키면 전체. 토큰이 없으면
        `SHOP_ORDERS_PUBLIC_READ=on`(기본)일 때 지금처럼 전체, `off`면 401.
        """
        scope = _order_scope(request)
        if scope is not None:
            if buyer_id is not None and buyer_id != scope:
                raise ShopError("BUYER_MISMATCH", "로그인한 구매자와 다르다", 403)
            buyer_id = scope
        orders, next_cursor = shop.list_orders(buyer_id, since, until, limit, cursor)
        return _ok(orders, next_cursor=next_cursor)

    # 고정 경로는 `{order_id}` 경로보다 먼저 등록해야 가로채이지 않는다
    @app.get("/api/shop/orders/attributed")
    async def attributed_orders(
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        campaign_id: Annotated[str | None, Query(max_length=64)] = None,
        source: Annotated[str | None, Query(max_length=64)] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 500,
        _: str = Depends(_caller(ATTRIBUTION_READERS)),
    ) -> dict:
        if from_.tzinfo is None or to.tzinfo is None:
            raise ShopError("INVALID_QUERY", "from·to에는 시간대가 필요하다")
        return _ok(lookups.attributed_orders(from_, to, campaign_id, source, limit))

    @app.get("/api/shop/orders/{order_id}")
    async def get_order(order_id: str, request: Request) -> dict:
        """구매자 토큰이면 본인 주문만(남의 주문은 404). 그 밖은 주문 목록과 같은 규칙."""
        scope = _order_scope(request)
        order = shop.get_order(order_id)
        if scope is not None and order["buyer_id"] != scope:
            raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
        return _ok(order)

    @app.post("/api/shop/orders", status_code=201)
    async def create_order(body: OrderIn, response: Response, request: Request) -> dict:
        buyer_id = _buyer(request, body.buyer_id)
        order, replayed = shop.place_order(
            buyer_id,
            [(i.product_id, i.qty) for i in body.items],
            body.points_used,
            body.idempotency_key,
            body.coupon_id,
            body.coupon_units,
            body.src_source,
            body.src_medium,
            body.src_campaign,
        )
        if replayed:
            response.status_code = 200  # 새로 만든 게 아니라 처음 주문을 돌려준다
        await outbox.flush(database)
        return _ok(order, replayed=replayed)

    @app.post("/api/shop/orders/{order_id}/payments")
    async def pay(order_id: str, body: PaymentIn, request: Request) -> dict:
        _own_order(request, order_id)
        waited = await shop.gateway.pg_wait()  # 결제사 쪽 지연(시험용)을 실제로 기다린다
        order = shop.pay(order_id, body.method_code, body.simulate, pg_wait_ms=waited)
        shop.evaluate_monitor()
        await outbox.flush(database)
        return _ok(order)

    @app.get("/api/shop/payment-config")
    def payment_config_view() -> dict:
        return _ok({"provider": payment_config.provider})  # 키는 담지 않는다

    @app.post("/api/shop/orders/{order_id}/payments/toss/prepare")
    def toss_prepare(order_id: str, request: Request) -> dict:
        toss.require(toss_payments)
        _own_order(request, order_id)
        return _ok(shop.toss_prepare(order_id))

    @app.post("/api/shop/payments/toss/confirm")
    async def toss_confirm(body: TossConfirmIn, request: Request) -> dict:
        payments = toss.require(toss_payments)
        attempt_order = shop.attempt_order_id(body.toss_order_id)
        if attempt_order is not None:  # 없는 시도는 아래 승인이 404로 답한다
            _own_order(request, attempt_order)
        elif not _buyer_auth_legacy() and _identify(request).kind == "anonymous":
            raise ShopError("LOGIN_REQUIRED", "로그인이 필요하다", 401)
        order, replayed = await payments.confirm(body.toss_order_id, body.payment_key, body.amount)
        shop.evaluate_monitor()
        await outbox.flush(database)
        return _ok(order, replayed=replayed, toss_order_id=body.toss_order_id)

    @app.post("/api/shop/orders/{order_id}/cancel")
    async def cancel(order_id: str, body: CancelIn, request: Request) -> dict:
        _own_order(request, order_id)
        order = shop.cancel(order_id, body.reason_code)
        await outbox.flush(database)
        return _ok(order)

    @app.post("/api/shop/admin/orders/{order_id}/shipped")
    async def mark_shipped(
        order_id: str, body: ShippedIn, _: str = Depends(_caller(INJECT_ALLOWED))
    ) -> dict:
        return _ok(shop.mark_shipped(order_id, body.shipped_at))

    @app.post("/api/shop/admin/orders/{order_id}/delivered")
    async def mark_delivered(
        order_id: str, body: DeliveredIn, _: str = Depends(_caller(INJECT_ALLOWED))
    ) -> dict:
        return _ok(shop.mark_delivered(order_id, body.delivered_at))

    @app.get("/api/shop/admin/faults")
    async def fault_state(_: str = Depends(_caller(FAULT_STATE_READERS))) -> dict:
        return _ok(shop.fault_state())

    @app.post("/api/shop/admin/faults", status_code=201)
    async def inject_fault(body: FaultIn, _: str = Depends(_caller(INJECT_ALLOWED))) -> dict:
        return _ok(shop.inject_fault(body.kind, body.delay_ms))

    @app.get("/api/shop/admin/payments/timeouts")
    async def timeout_payments(_: str = Depends(_caller(RELEASE_ALLOWED))) -> dict:
        """시간 초과(승인됐는지 모름) 결제. 다시 확인을 기다리는 돈이다."""
        rows = shop.timeout_payments()
        keep = ("payment_id", "order_id", "amount", "provider", "failure_detail", "created_at")
        return _ok([{k: r.get(k) for k in keep} for r in rows])

    @app.post("/api/shop/admin/payments/recheck")
    async def recheck_payments(_: str = Depends(_caller(RELEASE_ALLOWED))) -> dict:
        """시간 초과 결제를 결제사에 지금 다시 확인한다(기록만 바로잡고 돈은 옮기지 않는다)."""
        done = await toss.recheck_timeouts(shop, toss_payments.client if toss_payments else None)
        await outbox.flush(database)
        return _ok({"resolved": done, "left": len(shop.timeout_payments())})

    @app.delete("/api/shop/admin/faults")
    async def release_fault(caller: str = Depends(_caller(RELEASE_ALLOWED))) -> dict:
        # 해제한 쪽은 지표로 복구를 확인한 뒤 나가는 복구 신호에 싣는다
        state = shop.release_fault(released_by=caller)
        await outbox.flush(database)
        return _ok(state)

    @app.get("/api/shop/admin/payment-version")
    async def payment_version(_: str = Depends(_caller(FAULT_STATE_READERS))) -> dict:
        return _ok(shop.payment_version())

    @app.put("/api/shop/admin/payment-version")
    async def deploy_payment_version(
        body: PaymentVersionIn, caller: str = Depends(_caller(PAYMENT_VERSION_DEPLOYERS))
    ) -> dict:
        """결제 설정 버전 배포(사람만, `demo_deploy.sh --bad`·`--reset`). v2는 잘못된 배포다."""
        if body.version == BAD_PAYMENT_VERSION and payment_config.live_key:
            raise ShopError(
                "LIVE_KEY", "라이브 결제 키가 있으면 잘못된 배포(v2)를 하지 않는다", 409
            )
        return _ok(shop.set_payment_version(body.version, caller))

    @app.post("/api/shop/admin/payment-version/rollback")
    async def rollback_payment_version(
        body: PaymentRollbackIn, caller: str = Depends(_caller(PAYMENT_VERSION_ROLLBACK))
    ) -> dict:
        """결제 설정을 이전 버전으로 되돌린다(인프라 AI 롤백). 앞으로 바꾸는 것은 거절한다."""
        return _ok(shop.rollback_payment_version(body.to_version, caller))

    @app.delete("/api/shop/admin/faults/pg")
    async def pg_ok(_: str = Depends(_caller(INJECT_ALLOWED))) -> dict:
        """결제사 쪽 지연(시험용)을 끈다. 사람(admin)만. 인프라 AI의 해제로는 꺼지지 않는다."""
        state = shop.pg_ok()
        await outbox.flush(database)
        return _ok(state)

    # --- 총괄 읽기 중계: 관리자 화면(shop-web)이 shop-api만 부르게 한다. GET만 ----------
    @app.get("/api/shop/admin/orchestrator/incidents")
    async def orchestrator_incidents(_: str = Depends(_caller(ORCHESTRATOR_VIEWERS))) -> dict:
        data, meta = await orchestrator_relay.incidents()
        return _ok(data, **meta)

    @app.get("/api/shop/admin/orchestrator/incidents/{context_id}")
    async def orchestrator_incident(
        context_id: str, _: str = Depends(_caller(ORCHESTRATOR_VIEWERS))
    ) -> dict:
        data, meta = await orchestrator_relay.incident(context_id)
        return _ok(data, **meta)

    @app.get("/api/shop/admin/orchestrator/departments")
    async def orchestrator_departments(_: str = Depends(_caller(ORCHESTRATOR_VIEWERS))) -> dict:
        data, meta = await orchestrator_relay.departments()
        return _ok(data, **meta)

    # --- 마케팅 받은 기획안 읽기 중계(A2A 읽기 skill). 관리자와 읽기 전용 열람자만 ----------
    @app.get("/api/shop/admin/marketing/briefs")
    async def marketing_briefs(
        limit: int = 50, _: str = Depends(_caller(ORCHESTRATOR_VIEWERS))
    ) -> dict:
        return _ok(await marketing_client.list_briefs(limit))

    @app.get("/api/shop/admin/marketing/briefs/{proposal_id}")
    async def marketing_brief(
        proposal_id: str, _: str = Depends(_caller(ORCHESTRATOR_VIEWERS))
    ) -> dict:
        return _ok(await marketing_client.get_brief(proposal_id))

    @app.get("/api/shop/admin/marketing/outage-sns-drafts")
    async def marketing_outage_sns_drafts(
        limit: int = 20,
        include_merged: bool = False,
        _: str = Depends(_caller(ORCHESTRATOR_VIEWERS)),
    ) -> dict:
        """장애 사과·보상 SNS 글 초안 읽기 중계. 초안만 보여 주고 게시하지 않는다.

        `include_merged`(기본 false): 유사 초안 병합으로 접힌 원본(감사 기록)까지 본다.
        """
        return _ok(await marketing_client.list_outage_sns_drafts(limit, include_merged))

    # --- 공지 배너(SHOP-08): 고객 화면이 읽고, 마케팅 관문 토큰만 게시·해제한다 ---------
    @app.get("/api/shop/banners")
    async def list_banners() -> dict:
        return _ok(promotions.active_banners())

    @app.post("/api/shop/banners", status_code=201)
    async def post_banner(body: BannerIn, _: str = Depends(_caller(MARKETING_ONLY))) -> dict:
        return _ok(
            promotions.post_banner(
                body.notice_id, body.text, body.valid_from, body.valid_to, body.context_id
            )
        )

    @app.delete("/api/shop/banners/{notice_id}")
    async def end_banner(notice_id: str, _: str = Depends(_caller(MARKETING_ONLY))) -> dict:
        return _ok(promotions.end_banner(notice_id))

    # --- 쿠폰(SHOP-10): 발급은 마케팅 관문 토큰만, `idempotency_key` 필수 ---------------
    @app.post("/api/shop/coupons", status_code=201)
    async def issue_coupon(
        body: CouponIn, response: Response, _: str = Depends(_caller(MARKETING_ONLY))
    ) -> dict:
        coupon, replayed = promotions.issue_coupon(**body.model_dump())
        if replayed:
            response.status_code = 200  # 같은 키의 재요청은 이전 쿠폰을 그대로 돌려준다
        return _ok(coupon, replayed=replayed)

    @app.get("/api/shop/coupons")
    async def list_coupons(
        buyer_id: str | None = None,
        purpose_code: str | None = None,
        campaign_id: str | None = None,
        _: str = Depends(_caller(COUPON_READERS)),
    ) -> dict:
        return _ok(promotions.list_coupons(buyer_id, purpose_code, campaign_id))

    @app.get("/api/shop/coupons/{coupon_id}")
    async def get_coupon(coupon_id: str, _: str = Depends(_caller(COUPON_READERS))) -> dict:
        return _ok(promotions.get_coupon(coupon_id))

    # --- 클레임 접수·반품 입고·환불 (#117): 쇼핑몰 → 고객지원 → 재무 경로의 입구 -----------
    @app.post("/api/shop/claims", status_code=201)
    async def create_claim(body: ClaimIn, request: Request) -> dict:
        buyer_id = _buyer(request, body.buyer_id)
        order = shop.get_order(body.order_id)
        if order["buyer_id"] != buyer_id:
            raise ShopError("BUYER_MISMATCH", "주문의 구매자가 아니다", 403)
        items = [item.model_dump() for item in body.items] if body.items else None
        claim_digest = claim_inbox.digest(
            buyer_id,
            body.order_id,
            body.type,
            body.reason,
            items,
            body.unsealed,
            body.description,
        )
        # 고객지원을 부르지 않고 이 서버에 "접수됨"으로 저장한다.
        # AXcellent가 읽어 가 판정하고 결과를 적는다
        receipt = await asyncio.to_thread(
            claim_inbox.add,
            database,
            digest=claim_digest,
            context_id=f"{support_client.CLAIM_CONTEXT_PREFIX}{claim_digest[:12]}",
            buyer_id=buyer_id,
            customer_name=body.customer_name,
            order_id=body.order_id,
            type_=body.type,
            reason=body.reason,
            items=items,
            unsealed=body.unsealed,
            evidence_count=body.evidence_count,
            description=body.description,
        )
        return _ok(receipt, context_id=receipt["context_id"])

    # --- 내 클레임 목록·결과: 이 서버에 저장된 신청과 AXcellent가 적어 준 결과 (읽기) ----
    @app.get("/api/shop/claims")
    async def list_my_claims(
        request: Request, buyer_id: str | None = None, order_id: str | None = None
    ) -> dict:
        buyer_id = _buyer(request, buyer_id)
        inquiries.buyer_number(buyer_id)
        if order_id is not None:
            inquiries.check_order_id(order_id)
        items = await asyncio.to_thread(claim_inbox.for_customer, database, buyer_id, order_id)
        return _ok(items, total=len(items))

    # --- 클레임함 읽기: AXcellent 고객지원이 새 신청을 읽어 가고 판정 결과를 적는다 ------
    @app.get("/api/shop/admin/claims")
    async def read_claim_inbox(
        state: Annotated[str, Query(pattern=r"^(new|open)$")] = "new",
        limit: Annotated[int, Query(ge=1, le=claim_inbox.READ_LIMIT_MAX)] = (
            claim_inbox.READ_LIMIT_DEFAULT
        ),
        after: Annotated[int, Query(ge=0)] = 0,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        if state == "open":  # 읽어 갔지만 아직 끝나지 않은 신청(상태를 다시 적을 수 있다)
            open_items, next_after = await asyncio.to_thread(
                claim_inbox.open_claims, database, after, limit
            )
            return _ok(open_items, next_after=next_after)
        items, total, failed = await asyncio.to_thread(claim_inbox.pending, database, limit)
        return _ok(items, total=total, failed=failed)

    @app.post("/api/shop/admin/claims/{seq}/failed")
    async def report_claim_failed(
        seq: Annotated[int, Path(ge=1)],
        body: IntakeFailedIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        done = await asyncio.to_thread(claim_inbox.report_failure, database, seq, body.reason)
        if done is None:
            raise ShopError("CLAIM_NOT_FOUND", "클레임함에 없는 접수 번호다", 404)
        return _ok(done)

    @app.post("/api/shop/admin/claims/{seq}/result")
    async def write_claim_result(
        seq: Annotated[int, Path(ge=1)],
        body: ClaimResultIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        if len(json.dumps(body.result, ensure_ascii=False)) > claim_inbox.MAX_RESULT_CHARS:
            raise ShopError("RESULT_TOO_LARGE", "결과가 너무 크다", 413)
        try:
            done = await asyncio.to_thread(
                claim_inbox.write_result,
                database,
                seq,
                body.support_claim_id,
                body.status,
                body.result,
            )
        except ValueError as error:
            raise ShopError(
                "CLAIM_MISMATCH", "이 신청에는 다른 클레임 번호가 적혀 있다", 409
            ) from error
        if done is None:
            raise ShopError("CLAIM_NOT_FOUND", "클레임함에 없는 접수 번호다", 404)
        return _ok(done)

    # --- 고객 문의 접수 (#385 B안): 쇼핑몰 → 고객지원 `create_inquiry` ---------------------
    @app.post("/api/shop/inquiries", status_code=201)
    async def create_inquiry(body: InquiryIn, request: Request) -> dict:
        buyer_id = _buyer(request, body.buyer_id)
        inquiries.buyer_number(buyer_id)  # 형식 확인(저장은 구매자 ID 그대로)
        inquiries.check_order_id(body.order_id)
        subject, message = inquiries.clean_text(body.subject, body.body, body.order_id)
        photos = inquiries.clean_images([i.model_dump() for i in body.images])
        order = shop.get_order(body.order_id)  # 읽기만 한다. 없으면 404
        if order["buyer_id"] != buyer_id:  # 남의 주문도 같은 404로 답한다
            raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
        inquiry_limiter.hit(buyer_id)
        digest = inquiries.digest(
            buyer_id, body.order_id, subject, message, images_key=inquiries.images_key(photos)
        )
        context_id = (
            f"{support_client.CLAIM_CONTEXT_PREFIX}{digest[:12]}"  # 같은 접수는 같은 사건·키
        )
        # 고객지원을 부르지 않고 이 서버에 저장한다. AXcellent가 정해진 간격으로 읽어 간다
        saved = await asyncio.to_thread(
            inbox.add,
            database,
            digest=digest,
            context_id=context_id,
            buyer_id=buyer_id,
            customer_name=storefront.get_user(buyer_id)["display_name"],
            order_id=body.order_id,
            subject=subject,
            message=message,
            images=photos or None,
        )
        # 고객에게는 접수 사실만 돌려준다. 문의 번호는 이 서버의 접수 번호다
        receipt = {
            "id": saved["seq"],
            "status": "open",
            "order_id": saved["order_id"],
            "updated_at": saved["created_at"],
        }
        return _ok(receipt, context_id=saved["context_id"])

    # --- 문의함 읽기: AXcellent 고객지원이 새 문의를 읽어 가고 읽었다고 알린다 ------------------
    @app.get("/api/shop/admin/inquiries")
    async def read_inquiry_inbox(
        limit: Annotated[int, Query(ge=1, le=inbox.READ_LIMIT_MAX)] = inbox.READ_LIMIT_DEFAULT,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        items, total, failed = await asyncio.to_thread(inbox.pending, database, limit)
        return _ok(items, total=total, failed=failed)

    @app.post("/api/shop/admin/inquiries/{seq}/failed")
    async def report_inquiry_failed(
        seq: Annotated[int, Path(ge=1)],
        body: IntakeFailedIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        done = await asyncio.to_thread(inbox.report_failure, database, seq, body.reason)
        if done is None:
            raise ShopError("INQUIRY_NOT_FOUND", "문의함에 없는 접수 번호다", 404)
        return _ok(done)

    @app.get("/api/shop/admin/intake/stuck")
    async def list_stuck_intake(_: str = Depends(_caller(INQUIRY_READERS))) -> dict:
        """실패로 빼 둔 문의·클레임 접수(번호·사유·횟수만). 사람이 확인하고 다시 시도한다."""
        a = await asyncio.to_thread(inbox.stuck, database)
        b = await asyncio.to_thread(claim_inbox.stuck, database)
        items = sorted(a + b, key=lambda i: (i["failed_at"] or "", i["seq"]))
        return _ok(items, total=len(items))

    @app.post("/api/shop/admin/inquiries/{seq}/retry")
    async def retry_inquiry(
        seq: Annotated[int, Path(ge=1)], _: str = Depends(_caller(INQUIRY_READERS))
    ) -> dict:
        done = await asyncio.to_thread(inbox.retry, database, seq)
        if done is None:
            raise ShopError("INQUIRY_NOT_FOUND", "문의함에 없는 접수 번호다", 404)
        return _ok(done)

    @app.post("/api/shop/admin/claims/{seq}/retry")
    async def retry_claim(
        seq: Annotated[int, Path(ge=1)], _: str = Depends(_caller(INQUIRY_READERS))
    ) -> dict:
        done = await asyncio.to_thread(claim_inbox.retry, database, seq)
        if done is None:
            raise ShopError("CLAIM_NOT_FOUND", "클레임함에 없는 접수 번호다", 404)
        return _ok(done)

    @app.post("/api/shop/admin/inquiries/{seq}/synced")
    async def mark_inquiry_synced(
        seq: Annotated[int, Path(ge=1)],
        body: InquirySyncedIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        done = await asyncio.to_thread(inbox.mark_synced, database, seq, body.support_inquiry_id)
        if done is None:
            raise ShopError("INQUIRY_NOT_FOUND", "문의함에 없는 접수 번호다", 404)
        return _ok(done)

    # --- 내 문의 목록·상태: 이 서버가 저장한 값만 읽는다 (고객지원을 부르지 않는다) -------------
    # 고객지원이 읽어 간 뒤 고객이 보는 값(상태·답변)을 되돌려 쓰면
    # (`POST /admin/inquiries/{seq}/view`) 여기서 읽는다. 아직 안 쓴 문의는 접수한 내용만 보여 준다.
    @app.get("/api/shop/inquiries")
    async def list_my_inquiries(
        request: Request,
        buyer_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict:
        buyer_id = _buyer(request, buyer_id)
        inquiries.buyer_number(buyer_id)  # 형식 확인
        own = await asyncio.to_thread(inbox.for_buyer, database, buyer_id)
        items = [inquiries.own_view(o) for o in own]
        return _ok(items[:limit], total=len(items))

    @app.get("/api/shop/inquiries/{inquiry_id}")
    async def get_my_inquiry(
        inquiry_id: Annotated[int, Path(ge=1)], request: Request, buyer_id: str | None = None
    ) -> dict:
        buyer_id = _buyer(request, buyer_id)
        inquiries.buyer_number(buyer_id)
        # 번호는 이 서버의 접수 번호다. 본인 접수가 아니면 없는 번호와 똑같이 404다
        own = await asyncio.to_thread(inbox.get_for_buyer, database, inquiry_id, buyer_id)
        if own is None:
            raise ShopError("NOT_FOUND", "문의를 찾을 수 없다", 404)
        return _ok(inquiries.own_view(own, with_messages=True))

    @app.get("/api/shop/admin/inquiries/open")
    async def read_open_inquiry_views(
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=inbox.READ_LIMIT_MAX)] = inbox.READ_LIMIT_MAX,
        sweep: bool = False,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        items, next_after = await asyncio.to_thread(inbox.open_views, database, after, limit, sweep)
        return _ok(items, next_after=next_after)

    @app.post("/api/shop/admin/inquiries/{seq}/view")
    async def write_inquiry_view(
        seq: Annotated[int, Path(ge=1)],
        body: InquiryViewIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        view = inquiries.customer_view(body.view)  # 허용한 키만 남긴다
        done = await asyncio.to_thread(inbox.set_view, database, seq, view, body.view_digest)
        if done is None:
            raise ShopError("INQUIRY_NOT_FOUND", "문의함에 없는 접수 번호다", 404)
        return _ok(done)

    @app.post("/api/shop/admin/inquiries/by-support/{support_inquiry_id}/gone")
    async def mark_inquiry_gone_by_support(
        support_inquiry_id: Annotated[int, Path(ge=1)],
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        """고객지원이 문의를 지운 그 자리에서 알려 고객 화면에서 빼고 되돌려 쓴 값을 지운다."""
        done = await asyncio.to_thread(inbox.mark_gone_by_support_id, database, support_inquiry_id)
        if done is None:
            raise ShopError("INQUIRY_NOT_FOUND", "이 번호로 읽어 간 문의가 없다", 404)
        return _ok(done)

    @app.post("/api/shop/admin/inquiries/{seq}/gone")
    async def mark_inquiry_gone(
        seq: Annotated[int, Path(ge=1)], _: str = Depends(_caller(INQUIRY_READERS))
    ) -> dict:
        """고객지원에서 찾을 수 없는 문의를 고객 화면에서 뺀다(개인정보 삭제·고객지원 재시작)."""
        done = await asyncio.to_thread(inbox.mark_gone, database, seq)
        if done is None:
            raise ShopError("INQUIRY_NOT_FOUND", "문의함에 없는 접수 번호다", 404)
        return _ok(done)

    # --- 고객 아이디어(의견): 저장만 한다. AXcellent가 읽어 가 분류·기획 전달을 한다 ------------
    @app.post("/api/shop/ideas", status_code=201)
    async def submit_idea(body: IdeaIn, request: Request) -> dict:
        buyer_id = _buyer(request, body.buyer_id)
        inquiries.buyer_number(buyer_id)  # 형식 확인(저장은 구매자 ID 그대로)
        idea_limiter.hit(buyer_id)
        text = " ".join(body.text.split())
        key = idea_inbox.digest(buyer_id, body.kind, text)
        saved = await asyncio.to_thread(
            idea_inbox.add,
            database,
            digest=key,
            context_id=support_client.new_context_id(),
            buyer_id=buyer_id,
            kind=body.kind,
            text=text,
        )
        # 고객에게는 접수 사실만 돌려준다 (영역·묶음 같은 내부 분류는 보이지 않는다)
        return _ok({"idea_id": saved["seq"], "received": True}, context_id=saved["context_id"])

    @app.get("/api/shop/ideas")
    async def list_my_ideas(
        request: Request,
        buyer_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict:
        """내 아이디어 내역(읽기): 접수 번호·종류·앞부분·처리 상태·안내 한 줄. 본인만(`_buyer`)."""
        buyer_id = _buyer(request, buyer_id)
        inquiries.buyer_number(buyer_id)
        items = await asyncio.to_thread(idea_inbox.for_buyer, database, buyer_id, limit)
        return _ok(items, total=len(items))

    @app.get("/api/shop/admin/ideas")
    async def read_idea_inbox(
        limit: Annotated[int, Query(ge=1, le=idea_inbox.READ_LIMIT_MAX)] = (
            idea_inbox.READ_LIMIT_DEFAULT
        ),
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        items, total = await asyncio.to_thread(idea_inbox.pending, database, limit)
        return _ok(items, total=total)

    @app.post("/api/shop/admin/ideas/{seq}/synced")
    async def mark_idea_synced(
        seq: Annotated[int, Path(ge=1)],
        body: IntakeSyncedIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        done = await asyncio.to_thread(
            idea_inbox.mark_synced, database, seq, body.outcome, body.ref, body.notice
        )
        if done is None:
            raise ShopError("IDEA_NOT_FOUND", "아이디어함에 없는 접수 번호다", 404)
        return _ok(done)

    # --- 해결 결과 평가: 저장만 한다. AXcellent가 읽어 가 후기로 처리한다 -----------------------
    @app.post("/api/shop/inquiries/{inquiry_id}/feedback", status_code=201)
    async def submit_inquiry_feedback(
        inquiry_id: Annotated[int, Path(ge=1)], body: InquiryFeedbackIn, request: Request
    ) -> dict:
        buyer_id = _buyer(request, body.buyer_id)
        inquiries.buyer_number(buyer_id)
        feedback_limiter.hit(buyer_id)
        # 번호는 이 서버의 접수 번호다. 본인 접수가 아니면 없는 번호와 똑같이 404다
        own = await asyncio.to_thread(inbox.get_for_buyer, database, inquiry_id, buyer_id)
        if own is None:
            raise ShopError("NOT_FOUND", "문의를 찾을 수 없다", 404)
        view = own["view"]
        if view is None:  # 고객지원이 읽어 가기 전에는 평가할 수 없다(해결된 문의도 아니다)
            raise ShopError("INVALID_STATE", "아직 고객지원이 확인하기 전인 문의다", 409)
        if view.get("status") != "resolved":
            raise ShopError("INVALID_STATE", "해결된 문의만 평가할 수 있다", 409)
        if view.get("rated") is True:
            raise ShopError("INVALID_STATE", "이미 평가를 남긴 문의다", 409)
        saved = await asyncio.to_thread(
            inbox.save_feedback,
            database,
            inquiry_id,
            buyer_id,
            body.rating,
            body.resolved,
            body.comment,
        )
        if saved != "saved":  # 먼저 들어온 요청이 이미 남겼다
            raise ShopError("INVALID_STATE", "이미 평가를 남긴 문의다", 409)
        # 고객에게는 접수 사실만 돌려준다(공개·숨김 같은 내부 판단은 보이지 않는다)
        receipt = {"inquiry_id": inquiry_id, "rated": True, "rating": body.rating}
        return _ok(receipt)

    @app.get("/api/shop/admin/feedback")
    async def read_feedback_inbox(
        limit: Annotated[int, Query(ge=1, le=inbox.READ_LIMIT_MAX)] = inbox.READ_LIMIT_DEFAULT,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        items, total = await asyncio.to_thread(inbox.pending_feedback, database, limit)
        return _ok(items, total=total)

    @app.post("/api/shop/admin/inquiries/{seq}/feedback-synced")
    async def mark_feedback_synced(
        seq: Annotated[int, Path(ge=1)],
        body: IntakeSyncedIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        if body.notice is not None:
            raise ShopError("INVALID_INPUT", "notice는 아이디어에서만 쓴다", 422)
        done = await asyncio.to_thread(inbox.mark_feedback_synced, database, seq, body.outcome)
        if done is None:
            raise ShopError("FEEDBACK_NOT_FOUND", "평가가 없는 접수 번호다", 404)
        return _ok(done)

    # --- 반품 입고: 이 서버에 기록만 한다. AXcellent가 읽어 가 클레임을 입고 처리한다 -----------
    @app.post("/api/shop/admin/claims/{claim_id}/return-received")
    async def return_received(
        claim_id: int, body: ReturnReceivedIn, _: str = Depends(_caller(INJECT_ALLOWED))
    ) -> dict:
        done = refunds.get_return(claim_id)
        if done is not None:  # 이미 입고 처리했다: 다시 기록하지 않는다
            return _ok(done, replayed=True)
        shop.get_order(body.order_id)
        # 이 서버의 클레임 신청 기록으로 확인한다: 모르는 클레임·다른 주문·
        # 아직 입고 단계가 아닌 클레임은 기록하지 않고 이벤트도 내지 않는다
        claim = await asyncio.to_thread(claim_inbox.find_by_support_id, database, claim_id)
        if claim is None:
            raise ShopError("CLAIM_NOT_FOUND", "클레임을 찾을 수 없다", 404)
        if claim["order_id"] != body.order_id:
            raise ShopError("ORDER_MISMATCH", "클레임의 주문과 다르다", 409)
        if claim["status"] != "awaiting_return":
            raise ShopError(
                "INVALID_STATE", "입고를 받을 상태가 아니다(상태 반영은 최대 10초 걸린다)", 409
            )
        recorded, _replayed = refunds.record_return(
            claim_id, body.order_id, body.inspection, support_client.new_context_id()
        )
        await outbox.flush(database)
        return _ok(recorded)

    @app.get("/api/shop/admin/returns")
    async def read_return_inbox(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        items, total = await asyncio.to_thread(refunds.pending_returns, limit)
        return _ok(items, total=total)

    @app.post("/api/shop/admin/returns/{claim_id}/synced")
    async def mark_return_synced(
        claim_id: int,
        body: IntakeSyncedIn,
        _: str = Depends(_caller(INQUIRY_READERS)),
    ) -> dict:
        if body.notice is not None:
            raise ShopError("INVALID_INPUT", "notice는 아이디어에서만 쓴다", 422)
        done = await asyncio.to_thread(refunds.mark_return_synced, claim_id, body.outcome)
        if done is None:
            raise ShopError("RETURN_NOT_FOUND", "입고 기록에 없는 클레임 번호다", 404)
        return _ok(done)

    @app.post("/api/shop/admin/returns/{claim_id}/requeue")
    async def requeue_return(claim_id: int, _: str = Depends(_caller(INQUIRY_READERS))) -> dict:
        """처리를 포기(gave_up)했거나 잘못 닫은 입고를 다시 읽는 차례로 돌린다(사람이 확인한 뒤)."""
        done = await asyncio.to_thread(refunds.requeue_return, claim_id)
        if done is None:
            raise ShopError("RETURN_NOT_FOUND", "입고 기록에 없는 클레임 번호다", 404)
        return _ok(done)

    @app.post("/api/shop/payments/{payment_id}/refund", status_code=201)
    async def refund_payment(
        payment_id: str,
        body: RefundIn,
        response: Response,
        _: str = Depends(_caller(REFUND_ALLOWED)),
    ) -> dict:
        refund, replayed = refunds.refund(
            payment_id, body.refund_amount, body.idempotency_key, body.context_id
        )
        await outbox.flush(database)
        if replayed:
            response.status_code = 200  # 같은 키의 재요청은 이전 환불을 그대로 돌려준다
        return _ok(refund, replayed=replayed)

    # --- 데이터 조회(contracts/data-access): data 에이전트가 읽는 읽기 전용 API -----------
    # 아래 두 조회는 동기 핸들러: FastAPI가 스레드에서 돌려 DB를 읽는 동안 이벤트 루프를 막지 않는다
    # (주문 목록과 같은 이유, #959)
    @app.get("/api/shop/failed-orders")
    def failed_orders(
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        category: str | None = None,
        limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
        status: Literal["failed", "timeout"] = "failed",
        _: str = Depends(_caller(DATA_READERS)),
    ) -> dict:
        """결제 실패 주문. `status=timeout`이면 시간 초과(확인·환불 필요) 결제를 같은 모양으로."""
        query = _query(ShopFailedOrdersQuery, from_=from_, to=to, category=category, limit=limit)
        result = lookups.failed_orders(query, status=status)
        return _ok(result.model_dump(mode="json"))

    @app.get("/api/shop/sales-summary")
    def sales_summary(
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        group_by: Annotated[str, Query(max_length=64)] = "product",
        _: str = Depends(_caller(SALES_READERS)),
    ) -> dict:
        """결제가 끝난 주문의 판매 수량을 `group_by`(`product`·`age_band`·`category`)별로 합친다."""
        if from_.tzinfo is None or to.tzinfo is None:
            raise ShopError("INVALID_QUERY", "from·to에는 시간대가 필요하다")
        return _ok(lookups.sales_summary(from_, to, group_by))

    @app.get("/api/shop/customers-export")
    def customers_export(
        cursor: Annotated[str | None, Query(max_length=200)] = None,
        limit: Annotated[int, Query(ge=1, le=CUSTOMERS_EXPORT_LIMIT_MAX)] = 500,
        _: str = Depends(_caller(DATA_READERS)),
    ) -> dict:
        """고객 내보내기(`ShopCustomersExportResult`). 가명 ID와 값이 있는 속성만, 이름·연락처 없음.

        `(change_seq, buyer_id)` 순이다. `next_cursor`를 다음 `cursor`로 넘기고, 받을 것이 없으면
        받은 커서가 그대로 돌아온다. `data`·`admin` 토큰만.
        """
        query = _query(ShopCustomersExportQuery, cursor=cursor, limit=limit)
        return _ok(lookups.customer_export(query).model_dump(mode="json"))

    @app.get("/api/shop/metric-series")
    def metric_series(
        metric: str,
        from_: Annotated[datetime, Query(alias="from")],
        to: datetime,
        bucket: str = "5m",
        _: str = Depends(_caller(DATA_READERS)),
    ) -> dict:
        query = _query(ShopMetricSeriesQuery, metric=metric, from_=from_, to=to, bucket=bucket)
        result = lookups.metric_series(query)
        return _ok(result.model_dump(mode="json"))

    @app.get("/api/shop/sales-export")
    def sales_export_page(
        cursor: str | None = None,
        limit: int = 500,
        _: str = Depends(_caller(DATA_READERS)),
    ) -> dict:
        """판매 기록 내보내기 (data 수집기). 동기 핸들러라 스레드에서 읽는다(#959와 같은 이유)."""
        query = _query(ShopSalesExportQuery, cursor=cursor, limit=limit)
        result = sales_export.export(query)
        return _ok(result.model_dump(mode="json"))

    @app.get("/api/shop/sellers/{seller_id}/profile")
    async def seller_profile(seller_id: str, _: str = Depends(_caller(DATA_READERS))) -> dict:
        query = _query(ShopSellerProfileQuery, seller_id=seller_id)
        return _ok(lookups.seller_profile(query).model_dump(mode="json"))

    @app.get("/api/shop/sellers/{seller_id}/prices")
    async def seller_prices(seller_id: str, _: str = Depends(_caller(DATA_READERS))) -> dict:
        query = _query(ShopSellerPricesQuery, seller_id=seller_id)
        return _ok(lookups.seller_prices(query).model_dump(mode="json"))

    return app


app = create_app()
