"""고객사(판매 서버)가 발급한 연결 키 `SHOP_CLIENT_KEYS`.

AXcellent 설치·서비스마다 키를 따로 발급한다(`<설치>/<서비스>:<키>:<read|write>`).
- 서비스 이름은 지금 허용 목록(부서 이름, 서비스는 원래 부서로도 인정)에 그대로 들어간다.
- `read` 키는 읽기(GET·HEAD)만 된다. 로컬 같은 관찰 모드 설치가 판매 서버 상태를 못 바꾸게 막는다.
- 전환 기간에는 AXcellent 에이전트 토큰도 받고, `SHOP_AGENT_TOKENS=off`면 끈다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api import main as shop_main
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

W_INQUIRY = "sk_251_inquiry_unit_only_0123456789"
R_INQUIRY = "sk_local_inquiry_unit_only_0123456789"
W_MARKETING = "sk_251_marketing_unit_only_0123456789"
SHORT = "sk_short"


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    monkeypatch.setenv(
        shop_main.CLIENT_KEYS_ENV,
        ",".join(
            [
                f"ax-251/inquiry:{W_INQUIRY}:write",
                f"ax-local/inquiry:{R_INQUIRY}:read",
                f"ax-251/marketing:{W_MARKETING}:write",
                f"ax-251/claims:{SHORT}:write",  # 너무 짧아 켜지지 않는다
                "broken-entry",
                "ax-251/data:sk_bad_mode_unit_only_0123456789:admin",  # 권한이 read·write가 아님
            ]
        ),
    )
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


INBOX = "/api/shop/admin/inquiries"


def test_a_write_key_reads_and_writes_as_its_service(client) -> None:
    assert client.get(INBOX, headers=_auth(W_INQUIRY)).status_code == 200
    # 없는 접수라 404지만, 인증·권한은 통과했다
    res = client.post(f"{INBOX}/1/retry", headers=_auth(W_INQUIRY))
    assert res.status_code == 404, res.text


def test_a_read_key_reads(client) -> None:
    assert client.get(INBOX, headers=_auth(R_INQUIRY)).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", f"{INBOX}/1/retry", None),
        ("POST", f"{INBOX}/1/gone", None),
        ("POST", f"{INBOX}/1/failed", {"reason": "x"}),
        ("POST", f"{INBOX}/1/synced", {"support_inquiry_id": 1}),
        ("POST", "/api/shop/admin/claims/1/result", {}),
    ],
)
def test_a_read_key_cannot_change_anything(client, method, path, body) -> None:
    res = client.request(method, path, json=body, headers=_auth(R_INQUIRY))
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "READ_ONLY_KEY"


def test_the_service_allowlist_still_applies(client) -> None:
    """마케팅 키로 문의함은 못 읽는다(문의함은 고객지원만)."""
    res = client.get(INBOX, headers=_auth(W_MARKETING))
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    "key", [SHORT, "sk_bad_mode_unit_only_0123456789", "sk_unknown_0123456789abcdef"]
)
def test_short_badly_written_or_unknown_keys_do_not_work(client, key) -> None:
    res = client.get(INBOX, headers=_auth(key))
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "UNAUTHORIZED"


def test_agent_tokens_still_work_during_the_switch(client) -> None:
    assert client.get(INBOX, headers=_auth("mock:support")).status_code == 200


def test_agent_tokens_can_be_turned_off(client, monkeypatch) -> None:
    monkeypatch.setenv(shop_main.AGENT_TOKENS_ENV, "off")
    res = client.get(INBOX, headers=_auth("mock:support"))
    assert res.status_code == 401
    assert client.get(INBOX, headers=_auth(W_INQUIRY)).status_code == 200


def test_without_client_keys_nothing_changes(client, monkeypatch) -> None:
    monkeypatch.delenv(shop_main.CLIENT_KEYS_ENV)
    assert client.get(INBOX, headers=_auth("mock:support")).status_code == 200
    assert client.get(INBOX, headers=_auth(W_INQUIRY)).status_code == 401


def test_client_keys_parse_into_install_service_and_mode() -> None:
    keys = shop_main.client_keys()
    assert keys[W_INQUIRY] == shop_main.ClientKey("ax-251", "inquiry", "write")
    assert keys[R_INQUIRY] == shop_main.ClientKey("ax-local", "inquiry", "read")
    assert SHORT not in keys
    assert len(keys) == 3
