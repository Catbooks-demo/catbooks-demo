"""`SHOP_ID_START`: 새 번호표만 크게 시작하고, 이미 있는 번호표는 그대로 이어 간다."""

from __future__ import annotations

import pytest

from ax_shop_api.db import Db, id_start


def test_default_starts_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOP_ID_START", raising=False)
    db = Db(":memory:")
    with db.tx() as conn:
        assert db.next_id(conn, "order", "ord_") == "ord_0001"
        assert db.next_id(conn, "order", "ord_") == "ord_0002"


def test_new_counters_start_at_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHOP_ID_START", "1000000")
    db = Db(":memory:")
    with db.tx() as conn:
        assert db.next_id(conn, "order", "ord_") == "ord_1000000"
        assert db.next_id(conn, "order", "ord_") == "ord_1000001"
        assert db.next_id(conn, "payment", "pay_") == "pay_1000000"


def test_existing_counter_keeps_counting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHOP_ID_START", raising=False)
    db = Db(":memory:")
    with db.tx() as conn:
        db.next_id(conn, "order", "ord_")
    monkeypatch.setenv("SHOP_ID_START", "1000000")
    with db.tx() as conn:
        assert db.next_id(conn, "order", "ord_") == "ord_0002"


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5"])
def test_bad_values_fall_back_to_one(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("SHOP_ID_START", raw)
    assert id_start() == 1
