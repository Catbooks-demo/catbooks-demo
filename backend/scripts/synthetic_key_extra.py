"""가상 데이터 덧붙임 정답지: 장르 묶음 구매(lift)와 재구매·이탈. 판매 DB를 읽기만 한다.

되채우기(`gen_synthetic.py`)·매일 모드(`gen_synthetic_daily.py`)가 넣은 결과를 DB에서 다시 세어
`--out/answer_key_extra.json`에 쓴다. 되채우기 정답지(`answer_key.json`)·DB는 바꾸지 않는다.

    cd backend
    SHOP_DB_PATH=... uv run python scripts/synthetic_key_extra.py [--out ~/catbooks-synthetic-out]
        [--db shop.db] [--as-of 2026-12-01T00:00:00Z] [--min-buyers 20] [--dry-run]

- 대상: 가상 회원(`synthetic_ids.is_synthetic`)의 결제된 주문(성공한 결제가 있는 주문, 뒤에 환불·
  클레임이 있어도 산 것으로 센다). 사람 주문은 세지 않는다.
- 기간: `--out/answer_key.json`의 되채우기 기간(한국 시간 `window_start` 0시 ~ `cutoff_utc`).
- 묶음(bundles): 기간 안에 같은 회원이 산 장르의 쌍. 회원 단위다(같은 주문이 아니어도 된다).
  N = 기간 안에 결제한 가상 회원 수, P(A) = A를 산 회원 / N, support = P(A&B),
  lift = P(A&B) / (P(A)·P(B)). 장르가 없는 상품의 줄은 빼고 센다.
- 재구매·이탈(repeat_churn): 기준 시각까지 결제 주문이 2건 이상인 회원 중 기준 시각 전 60일 안에
  결제 주문이 없는 회원의 몫. 기준은 되채우기 끝(`cutoff_utc`)과, 그 뒤 가상 주문(매일 모드)이
  있으면 "지금"(`--as-of`, 기본은 DB의 마지막 가상 주문 시각)이다. 실행 시각을 쓰지 않아 같은 DB면
  같은 결과다.
- 정답지는 로컬 전용이다(기본 `~/catbooks-synthetic-out`, 저장소에 넣지 않는다).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_synthetic as gen  # noqa: E402

from ax_shop_api import synthetic_ids  # noqa: E402

OUT_FILE = "answer_key_extra.json"
CHURN_DAYS = 60
DEFAULT_MIN_BUYERS = 20  # lift 상위에 넣는 쌍의 최소 회원 수(작은 표본의 큰 lift를 거른다)
TOP = 10


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(gen.STAMP)


def open_readonly(path: str) -> sqlite3.Connection:
    """읽기 전용으로 연다(파일이 없으면 만들지 않고 실패한다)."""
    if path in ("", ":memory:"):
        raise SystemExit("DB 파일이 필요하다(--db 또는 SHOP_DB_PATH)")
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise SystemExit(f"DB 파일이 없다: {file}")
    conn = sqlite3.connect(f"{file.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def paid_orders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """가상 회원의 결제된 주문과 품목 장르. 주문 시각 순(같으면 주문 번호 순)."""
    rows = conn.execute(
        "SELECT o.order_id, o.buyer_id, o.created_at, p.genre_code "
        "FROM orders o JOIN order_items i ON i.order_id = o.order_id "
        "LEFT JOIN products p ON p.product_id = i.product_id "
        f"WHERE {synthetic_ids.BUYER_WHERE.replace('buyer_id', 'o.buyer_id')} "  # noqa: S608
        "AND EXISTS (SELECT 1 FROM payments y WHERE y.order_id = o.order_id "
        "AND y.status = 'succeeded') ORDER BY o.created_at, o.order_id"
    ).fetchall()
    orders: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not synthetic_ids.is_synthetic(r["buyer_id"]):
            continue
        order = orders.setdefault(
            r["order_id"],
            {"buyer_id": r["buyer_id"], "at": _parse(r["created_at"]), "genres": set()},
        )
        order["genres"].add(r["genre_code"])
    return list(orders.values())


def bundles(
    orders: list[dict[str, Any]], start: datetime, end: datetime, min_buyers: int
) -> dict[str, Any]:
    """회원 단위 장르 쌍의 support·lift."""
    baskets: dict[str, set[str]] = {}
    untagged = 0
    for o in orders:
        if not start <= o["at"] < end:
            continue
        basket = baskets.setdefault(o["buyer_id"], set())
        for genre in o["genres"]:
            if genre is None:
                untagged += 1
            else:
                basket.add(genre)
    n = len(baskets)
    singles: Counter[str] = Counter()
    pairs: Counter[tuple[str, str]] = Counter()
    for basket in baskets.values():
        singles.update(basket)
        pairs.update(itertools.combinations(sorted(basket), 2))
    rows = []
    for (a, b), both in sorted(pairs.items()):
        p_a, p_b, p_ab = singles[a] / n, singles[b] / n, both / n
        rows.append(
            {
                "a": a,
                "b": b,
                "buyers": both,
                "support": round(p_ab, 6),
                "lift": round(p_ab / (p_a * p_b), 4),
                "confidence_a_to_b": round(both / singles[a], 4),
                "confidence_b_to_a": round(both / singles[b], 4),
            }
        )
    big = [r for r in rows if r["buyers"] >= min_buyers]
    return {
        "unit": "buyer (genres bought by the same synthetic buyer in the window)",
        "buyers": n,
        "untagged_order_genres_skipped": untagged,
        "genre_buyers": dict(sorted(singles.items(), key=lambda kv: (-kv[1], kv[0]))),
        "min_buyers_for_top_lift": min_buyers,
        "top_by_lift": sorted(big, key=lambda r: (-r["lift"], -r["buyers"], r["a"], r["b"]))[:TOP],
        "top_by_support": sorted(rows, key=lambda r: (-r["buyers"], -r["lift"], r["a"], r["b"]))[
            :TOP
        ],
        "pairs": rows,
    }


def churn(orders: list[dict[str, Any]], as_of: datetime, days: int = CHURN_DAYS) -> dict[str, Any]:
    """기준 시각까지 결제 주문 2건 이상인 회원 중 최근 `days`일 안에 주문이 없는 몫."""
    count: Counter[str] = Counter()
    last: dict[str, datetime] = {}
    for o in orders:
        if o["at"] > as_of:
            continue
        count[o["buyer_id"]] += 1
        last[o["buyer_id"]] = max(last.get(o["buyer_id"], o["at"]), o["at"])
    repeat = sorted(b for b, c in count.items() if c >= 2)
    since = as_of - timedelta(days=days)
    churned = [b for b in repeat if last[b] < since]
    return {
        "as_of_utc": _stamp(as_of),
        "inactive_days": days,
        "buyers_with_paid_order": len(count),
        "repeat_buyers": len(repeat),
        "churned_repeat_buyers": len(churned),
        "active_repeat_buyers": len(repeat) - len(churned),
        "churn_share": round(len(churned) / len(repeat), 4) if repeat else None,
    }


def build(
    conn: sqlite3.Connection,
    key: dict[str, Any],
    *,
    as_of: datetime | None = None,
    min_buyers: int = DEFAULT_MIN_BUYERS,
) -> dict[str, Any]:
    cfg = key["config"]
    start = gen._kst_midnight(date.fromisoformat(cfg["window_start"]))
    cutoff = _parse(cfg["cutoff_utc"])
    orders = paid_orders(conn)
    after = [o for o in orders if o["at"] >= cutoff]
    result: dict[str, Any] = {
        "source": "shop DB (read-only), synthetic buyers only, paid orders",
        "window": {
            "start_utc": _stamp(start),
            "cutoff_utc": _stamp(cutoff),
            "window_start": cfg["window_start"],
            "window_end": cfg["window_end"],
            "timezone_for_days": "Asia/Seoul (KST)",
        },
        "seed": cfg.get("seed"),
        "paid_orders": {
            "in_window": sum(1 for o in orders if start <= o["at"] < cutoff),
            "after_window": len(after),
        },
        "bundles": bundles(orders, start, cutoff, min_buyers),
        "repeat_churn": {"window_end": churn(orders, cutoff)},
    }
    if as_of is None and after:
        as_of = max(o["at"] for o in after)
    if as_of is not None and as_of > cutoff:
        result["repeat_churn"]["now"] = churn(orders, as_of)
    return result


def summary(result: dict[str, Any]) -> str:
    b = result["bundles"]
    lines = [
        f"기간 {result['window']['window_start']} ~ {result['window']['window_end']}: "
        f"결제 주문 {result['paid_orders']['in_window']:,}건, 산 회원 {b['buyers']:,}명",
        f"묶음 lift 상위(회원 ≥{b['min_buyers_for_top_lift']}명):",
    ]
    for r in b["top_by_lift"][:5]:
        lines.append(
            f"  {r['a']}+{r['b']}: lift {r['lift']}, support {r['support']} ({r['buyers']}명)"
        )
    for name, c in result["repeat_churn"].items():
        lines.append(
            f"이탈({name}, {c['as_of_utc']}): 재구매 회원 {c['repeat_buyers']:,}명 중 "
            f"{c['inactive_days']}일 무주문 {c['churned_repeat_buyers']:,}명 ({c['churn_share']})"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="가상 데이터 덧붙임 정답지(묶음 lift·이탈)")
    parser.add_argument("--db", default=os.getenv("SHOP_DB_PATH", ""), help="판매 DB 파일")
    parser.add_argument("--out", type=Path, default=gen.DEFAULT_OUT, help="정답지 폴더")
    parser.add_argument("--as-of", help="'지금' 이탈 기준 시각(ISO, 기본: 마지막 가상 주문)")
    parser.add_argument("--min-buyers", type=int, default=DEFAULT_MIN_BUYERS)
    parser.add_argument("--dry-run", action="store_true", help="쓰지 않고 요약만 보여 준다")
    args = parser.parse_args(argv)
    key_path = args.out / "answer_key.json"
    if not key_path.is_file():
        print(f"되채우기 정답지가 없다({key_path}). --out을 맞춘다", file=sys.stderr)
        return 2
    key = json.loads(key_path.read_text(encoding="utf-8"))
    conn = open_readonly(args.db)
    try:
        as_of = _parse(args.as_of) if args.as_of else None
        result = build(conn, key, as_of=as_of, min_buyers=args.min_buyers)
    finally:
        conn.close()
    print(summary(result))
    if args.dry_run:
        return 0
    path = args.out / OUT_FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    print(f"썼다: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
