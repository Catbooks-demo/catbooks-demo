"""알림 받는 주소(웹훅 + 회사 키)가 설정되면 outbox는 Redis 대신 그 주소로 보낸다.

받는 쪽은 AXcellent dashboard-api `POST /api/dashboard/events` (Axcellent-bs #980).
"""

from __future__ import annotations

import json

import httpx
import pytest

from ax_agent_template import events
from ax_contracts.schemas import EventType
from ax_shop_api import outbox
from ax_shop_api.db import Db

URL = "https://ax.example/api/dashboard/events"
KEY = "ck_catbooks_unit_only"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv(outbox.WEBHOOK_URL_ENV, URL)
    monkeypatch.setenv(outbox.COMPANY_KEY_ENV, KEY)
    events.reset_mock_bus()
    yield
    outbox.TRANSPORT = None


class Receiver:
    """가짜 알림 받는 주소. `statuses`를 차례로 돌려주고, 다 쓰면 202."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = list(statuses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else 202
        return httpx.Response(status, json={"data": {}})

    def sent_ids(self) -> list[str]:
        return [json.loads(r.content)["data"]["order_id"] for r in self.requests]


def _install(receiver) -> None:
    outbox.TRANSPORT = httpx.MockTransport(receiver)


def _fill(db: Db, n: int) -> None:
    with db.tx() as conn:
        conn.execute("DELETE FROM outbox_events")
        for i in range(n):
            data = {
                "order_id": f"ord_{i:05d}",
                "buyer_id": "buy_0001",
                "items": [
                    {"product_id": "prd_0001", "seller_id": "sel_01", "qty": 1, "unit_price": 1000}
                ],
                "total_amount": 1000,
            }
            outbox.enqueue(conn, EventType.ORDER_CREATED, data, f"t:{i}")


def _unsent(db: Db) -> int:
    with db.tx() as conn:
        row = conn.execute("SELECT COUNT(*) FROM outbox_events WHERE sent_at IS NULL").fetchone()
        return row[0]


@pytest.mark.asyncio
async def test_events_go_to_the_webhook_with_the_company_key_not_redis() -> None:
    receiver = Receiver()
    _install(receiver)
    db = Db(":memory:")
    _fill(db, 3)
    assert await outbox.flush(db) == 3
    assert _unsent(db) == 0
    assert events.MOCK_PUBLISHED == []  # 버스에는 직접 넣지 않는다
    assert receiver.sent_ids() == ["ord_00000", "ord_00001", "ord_00002"]  # 오래된 순
    first = receiver.requests[0]
    assert str(first.url) == URL
    assert first.headers["authorization"] == f"Bearer {KEY}"
    assert first.headers["content-type"] == "application/json"
    body = json.loads(first.content)
    assert body["type"] == "order.created"
    assert body["source"] == "shop-api"
    assert body["context_id"] is None


@pytest.mark.asyncio
async def test_a_failed_send_stays_unsent_and_goes_again_next_flush_in_order() -> None:
    receiver = Receiver(202, 503)
    _install(receiver)
    db = Db(":memory:")
    _fill(db, 3)
    assert await outbox.flush(db) == 1  # 두 번째에서 멈춘다
    assert _unsent(db) == 2
    assert await outbox.flush(db) == 2
    assert _unsent(db) == 0
    assert receiver.sent_ids() == ["ord_00000", "ord_00001", "ord_00001", "ord_00002"]


@pytest.mark.asyncio
async def test_a_connection_error_stays_unsent() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _install(down)
    db = Db(":memory:")
    _fill(db, 2)
    assert await outbox.flush(db) == 0
    assert _unsent(db) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429])
async def test_auth_and_rate_limit_errors_are_retried_later(status) -> None:
    """키 설정 오류·잠깐 막힘은 고치면 다시 보낼 수 있으니 버리지 않는다."""
    _install(Receiver(status))
    db = Db(":memory:")
    _fill(db, 2)
    assert await outbox.flush(db) == 0
    assert _unsent(db) == 2


@pytest.mark.asyncio
async def test_an_event_the_receiver_rejects_as_invalid_does_not_block_the_rest(caplog) -> None:
    """422(형식 거부)는 다시 보내도 같다. 기록을 남기고 넘어가 뒤의 장애 알림이 막히지 않게 한다."""
    receiver = Receiver(422)
    _install(receiver)
    db = Db(":memory:")
    _fill(db, 3)
    with caplog.at_level("ERROR", logger="ax.shop.outbox"):
        assert await outbox.flush(db) == 3
    assert _unsent(db) == 0
    assert receiver.sent_ids() == ["ord_00000", "ord_00001", "ord_00002"]
    assert any("거부" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_without_a_company_key_nothing_is_sent(monkeypatch) -> None:
    monkeypatch.delenv(outbox.COMPANY_KEY_ENV)
    receiver = Receiver()
    _install(receiver)
    db = Db(":memory:")
    _fill(db, 2)
    assert await outbox.flush(db) == 0
    assert _unsent(db) == 2
    assert receiver.requests == []
    assert events.MOCK_PUBLISHED == []


@pytest.mark.asyncio
async def test_without_a_webhook_url_it_still_publishes_to_the_bus(monkeypatch) -> None:
    """251 전환 전까지는 지금처럼 버스로 넣는다."""
    monkeypatch.delenv(outbox.WEBHOOK_URL_ENV)
    receiver = Receiver()
    _install(receiver)
    db = Db(":memory:")
    _fill(db, 2)
    assert await outbox.flush(db) == 2
    assert receiver.requests == []
    assert len(events.MOCK_PUBLISHED) == 2
