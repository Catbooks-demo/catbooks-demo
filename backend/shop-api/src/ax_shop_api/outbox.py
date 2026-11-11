"""이벤트 outbox. 이벤트는 주문·결제와 같은 트랜잭션에 저장하고, 저장 뒤 AXcellent로 보낸다.

보내는 곳
- `AX_EVENTS_WEBHOOK_URL`이 있으면 AXcellent의 알림 받는 주소(웹훅)로 `POST`한다. 본문은 공통 봉투
  (`EventEnvelope`) JSON, 인증은 `Authorization: Bearer <AX_COMPANY_KEY>`(회사 키, `.env`에만 둔다).
  받는 쪽(AXcellent dashboard-api `POST /api/dashboard/events`)이 확인한 뒤 이벤트 버스에 넣는다.
- 없으면 지금처럼 `events.publish`로 버스에 직접 넣는다(251 전환 전까지만).

보내지 못하면(연결 실패, 5xx, 401·403·408·429, 회사 키 없음, Redis 연동 전) 미발행으로 남겨 두고
다음 `flush`에서 순서대로 다시 보낸다(최소 한 번 전달). 받는 쪽은 같은 `event_id`를 한 번만 넣는다.
받는 쪽이 그 밖의 4xx(형식 거부 등)로 돌려주면 다시 보내도 같으므로 오류 로그를 남기고 보낸 것으로
친다. 그래야 뒤에 쌓인 장애 알림(`shop.degraded`)이 막히지 않는다.

`flush` 한 번은 오래된 순으로 `FLUSH_BATCH`건까지만 읽어 보낸다. 미발행이 수십만 건 쌓여도
한꺼번에 메모리로 읽지 않아 서버가 멈추지 않는다(로컬 주문 48만 건에서 시작하자마자
1.3GB·`/health` 무응답). 쌓인 것은 주기 판정 루프가 묶음씩 이어서 비운다(`drain`).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
from datetime import UTC, datetime
from typing import Any

import httpx

from ax_agent_template.events import publish
from ax_contracts.schemas import EventEnvelope, EventType
from ax_shop_api.db import TENANT_ID, Db

SOURCE = "shop-api"
WEBHOOK_URL_ENV = "AX_EVENTS_WEBHOOK_URL"
COMPANY_KEY_ENV = "AX_COMPANY_KEY"
TIMEOUT_SECONDS = 5.0
# 다시 보내면 될 수 있는 응답 (그 밖의 4xx는 받는 쪽이 이 이벤트를 거부한 것)
RETRY_STATUSES = frozenset({401, 403, 408, 429})
# 테스트에서 httpx.MockTransport로 바꿔 끼운다
TRANSPORT: httpx.AsyncBaseTransport | None = None

logger = logging.getLogger("ax.shop.outbox")


class DeliveryFailed(Exception):
    """이번에는 보내지 못했다. 미발행으로 남겨 두고 다음에 다시 보낸다."""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def enqueue(
    conn: sqlite3.Connection, event_type: EventType, data: dict[str, Any], key: str
) -> None:
    """`key`는 `<type>:<대상 ID>`다. 같은 키는 한 번만 저장한다(이벤트 ID도 키에서 만든다)."""
    envelope = EventEnvelope(
        event_id=f"evt_{hashlib.sha256(key.encode()).hexdigest()[:12]}",
        type=event_type,
        occurred_at=datetime.now(UTC),
        tenant_id=TENANT_ID,
        context_id=None,  # 사건 번호는 통합 에이전트가 발급한다
        source=SOURCE,
        idempotency_key=key,
        data=data,
    )
    conn.execute(
        "INSERT OR IGNORE INTO outbox_events(event_id, payload, tenant_id, context_id, created_at) "
        "VALUES (?, ?, ?, NULL, ?)",
        (envelope.event_id, envelope.model_dump_json(), TENANT_ID, _now()),
    )


FLUSH_BATCH = 500  # flush 한 번에 읽어 보내는 최대 건수


async def _post(client: httpx.AsyncClient, url: str, key: str, payload: str, event_id: str) -> None:
    try:
        res = await client.post(
            url,
            content=payload,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    except httpx.HTTPError as error:
        raise DeliveryFailed(f"{type(error).__name__}") from None
    if res.is_success:
        return
    if res.status_code >= 500 or res.status_code in RETRY_STATUSES:
        raise DeliveryFailed(f"HTTP {res.status_code}")
    logger.error(
        "알림 받는 주소가 이벤트를 거부해 다시 보내지 않는다 %s: HTTP %s %s",
        event_id,
        res.status_code,
        res.text[:300],
    )


async def flush(db: Db, *, batch: int = FLUSH_BATCH) -> int:
    """미발행 이벤트를 발행 순서대로 `batch`건까지 보낸다. 실패하면 멈추고 보낸 개수를 돌려준다.

    주문·결제 요청마다 부르므로 한 번에 다 비우지 않는다(요청이 쌓인 이벤트를 기다리지 않게)."""
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT event_id, payload FROM outbox_events WHERE sent_at IS NULL "
            "ORDER BY rowid LIMIT ?",
            (batch,),
        ).fetchall()
    if not rows:
        return 0
    url = (os.getenv(WEBHOOK_URL_ENV) or "").strip()
    if not url:
        return await _send_rows(db, rows, None)
    key = (os.getenv(COMPANY_KEY_ENV) or "").strip()
    if not key:
        logger.warning("%s가 없어 이벤트를 보내지 않는다 (미발행 %s건)", COMPANY_KEY_ENV, len(rows))
        return 0
    async with httpx.AsyncClient(transport=TRANSPORT, timeout=TIMEOUT_SECONDS) as client:
        return await _send_rows(db, rows, (client, url, key))


async def _send_rows(
    db: Db, rows: list[sqlite3.Row], webhook: tuple[httpx.AsyncClient, str, str] | None
) -> int:
    sent = 0
    for row in rows:
        try:
            if webhook is None:
                await publish(EventEnvelope.model_validate_json(row["payload"]))
            else:
                await _post(*webhook, row["payload"], row["event_id"])
        except NotImplementedError:  # Redis 연동 전. 미발행으로 남겨 둔다
            break
        except DeliveryFailed as error:
            logger.warning("이벤트를 보내지 못해 다음에 다시 보낸다 %s: %s", row["event_id"], error)
            break
        with db.tx() as conn:
            conn.execute(
                "UPDATE outbox_events SET sent_at = ? WHERE event_id = ?", (_now(), row["event_id"])
            )
        sent += 1
    return sent


async def drain(db: Db, *, batch: int = FLUSH_BATCH) -> int:
    """미발행이 남지 않을 때까지 묶음씩 보낸다. 묶음 사이에 다른 요청이 돌 틈을 준다.

    한 묶음을 다 못 보냈으면(발행 실패·남은 것 없음) 멈춘다. 보낸 전체 개수를 돌려준다."""
    total = 0
    while True:
        sent = await flush(db, batch=batch)
        total += sent
        if sent < batch:
            return total
        await asyncio.sleep(0)
