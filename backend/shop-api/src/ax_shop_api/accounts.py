"""회원 가입·로그인·로그인 토큰(세션). 웹 프레임워크에 의존하지 않는다.

- 비밀번호는 scrypt로 해시해 저장한다(`scrypt$n$r$p$솔트$해시`). 원문은 어디에도 남기지 않는다.
- 로그인 토큰은 무작위 값이고 DB에는 sha256 16진만 둔다. 토큰 원문은 가입·로그인 응답에만 나간다.
- 이메일은 `strip().lower()`로 맞춰 저장·비교한다.
- 새 구매자 ID는 `buy_` + 기존 ID의 최대 번호 + 1(4자리 이상)이다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_shop_api import retention
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.inquiries import BUYER_ID
from ax_shop_api.service import ShopError

AGE_BANDS: tuple[str, ...] = ("10s", "20s", "30s", "40s", "50s_plus")
GENDERS: tuple[str, ...] = ("female", "male", "no_answer")
OCCUPATIONS: tuple[str, ...] = ("student", "employee", "self_employed", "other", "no_answer")

PASSWORD_MIN = 8
PASSWORD_MAX = 128
DISPLAY_NAME_MAX = 20
EMAIL_MAX = 254
SESSION_DAYS_DEFAULT = 30
ENV_SESSION_DAYS = "SHOP_SESSION_DAYS"
ENV_DEMO_LOGIN = "SHOP_DEMO_LOGIN"
DEMO_BUYERS_MAX = 60

# 가입·로그인 시도 제한(창 안의 시도 수, 창 길이 초). 비밀번호 맞히기와 scrypt 부하를 막는다.
LOGIN_PER_EMAIL = (10, 300.0)
LOGIN_PER_IP = (30, 300.0)
SIGNUP_PER_IP = (5, 600.0)
PASSWORD_CHECK_PER_BUYER = (10, 300.0)

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
_SALT_BYTES = 16
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PROFILE_FIELDS = ("display_name", "age_band", "gender", "occupation")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, maxmem=64 * 1024 * 1024
    )
    b64 = base64.b64encode
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt).decode()}${b64(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        actual = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            maxmem=64 * 1024 * 1024,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


# 없는 이메일로 로그인해도 같은 시간이 걸리도록 비교할 해시(아무 비밀번호와도 맞지 않는다)
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


class KeyedRateLimiter:
    """키(이메일·IP·구매자 ID)별 고정 길이 창 제한. 통과한 시도만 센다.

    `inquiries.InquiryRateLimiter`와 같은 꼴이다.
    """

    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._stamps: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str) -> None:
        now = self._clock()
        stamps = self._stamps[key]
        while stamps and now - stamps[0] >= self.window_s:
            stamps.popleft()
        if len(stamps) >= self.limit:
            raise ShopError("RATE_LIMITED", "시도가 너무 많다. 잠시 후 다시 시도해 달라", 429)
        stamps.append(now)
        if len(self._stamps) > 10_000:  # 오래된 키를 치워 메모리가 끝없이 늘지 않게 한다
            stale = [k for k, v in self._stamps.items() if not v or now - v[-1] >= self.window_s]
            for old in stale:
                del self._stamps[old]


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normalize_email(email: str) -> str:
    cleaned = email.strip().lower()
    if len(cleaned) > EMAIL_MAX or not _EMAIL.match(cleaned):
        raise ShopError("INVALID_EMAIL", "이메일 형식이 올바르지 않다", 422)
    return cleaned


def _check_password(password: str) -> None:
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise ShopError(
            "WEAK_PASSWORD", f"비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자여야 한다", 422
        )


def _clean_name(name: str) -> str:
    cleaned = name.strip()
    if not 1 <= len(cleaned) <= DISPLAY_NAME_MAX:
        raise ShopError("INVALID_PROFILE", f"이름은 1~{DISPLAY_NAME_MAX}자여야 한다", 422)
    return cleaned


def _check_code(field: str, value: str | None, allowed: tuple[str, ...]) -> None:
    if value is not None and value not in allowed:
        raise ShopError("INVALID_PROFILE", f"{field} 값이 올바르지 않다", 422)


def demo_login_enabled() -> bool:
    return (os.getenv(ENV_DEMO_LOGIN) or "").strip().lower() == "on"


def _session_days() -> int:
    try:
        days = int((os.getenv(ENV_SESSION_DAYS) or "").strip())
    except ValueError:
        return SESSION_DAYS_DEFAULT
    return days if 0 < days <= 3650 else SESSION_DAYS_DEFAULT


def _next_buyer_id(conn: sqlite3.Connection) -> str:
    top = 0
    for row in conn.execute("SELECT buyer_id FROM buyers"):
        found = BUYER_ID.match(row["buyer_id"])
        if found:
            top = max(top, int(found.group(1)))
    return f"buy_{top + 1:04d}"


class Accounts:
    def __init__(self, db: Db) -> None:
        self.db = db

    # --- 사용자 ---------------------------------------------------------------
    def _user(self, conn: sqlite3.Connection, buyer_id: str) -> dict[str, Any]:
        row = conn.execute(
            "SELECT b.buyer_id, b.age_band, b.gender, b.occupation, a.email, a.display_name "
            "FROM buyers b LEFT JOIN accounts a ON a.buyer_id = b.buyer_id "
            "WHERE b.buyer_id = ?",
            (buyer_id,),
        ).fetchone()
        if row is None:
            raise ShopError("BUYER_NOT_FOUND", "구매자를 찾을 수 없다", 404)
        name = row["display_name"] or f"데모 구매자 {buyer_id.rsplit('_', 1)[-1]}"
        return {
            "buyer_id": row["buyer_id"],
            "display_name": name,
            "email": row["email"],
            "age_band": row["age_band"],
            "gender": row["gender"],
            "occupation": row["occupation"],
        }

    def _issue(self, conn: sqlite3.Connection, buyer_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = _now()
        conn.execute(
            "INSERT INTO sessions(token_hash, buyer_id, created_at, expires_at) VALUES (?,?,?,?)",
            (
                _token_hash(token),
                buyer_id,
                _stamp(now),
                _stamp(now + timedelta(days=_session_days())),
            ),
        )
        return token

    # --- 가입·로그인 ----------------------------------------------------------
    def signup(
        self,
        email: str,
        password: str,
        display_name: str,
        age_band: str | None,
        gender: str | None,
        occupation: str | None,
    ) -> tuple[str, dict[str, Any]]:
        address = normalize_email(email)
        _check_password(password)
        name = _clean_name(display_name)
        _check_code("age_band", age_band, AGE_BANDS)
        _check_code("gender", gender, GENDERS)
        _check_code("occupation", occupation, OCCUPATIONS)
        hashed = hash_password(password)
        now = _stamp(_now())
        try:
            with self.db.tx() as conn:
                if conn.execute("SELECT 1 FROM accounts WHERE email = ?", (address,)).fetchone():
                    raise ShopError("EMAIL_TAKEN", "이미 가입된 이메일이다", 409)
                buyer_id = _next_buyer_id(conn)
                conn.execute(
                    "INSERT INTO buyers(buyer_id, tenant_id, created_at, age_band, gender, "
                    "occupation, updated_at, change_seq) VALUES (?,?,?,?,?,?,?,"
                    "(SELECT COALESCE(MAX(change_seq), 0) + 1 FROM buyers))",
                    (buyer_id, TENANT_ID, now, age_band, gender, occupation, now),
                )
                conn.execute(
                    "INSERT INTO accounts(buyer_id, email, password_hash, display_name, "
                    "agreed_at, created_at) VALUES (?,?,?,?,?,?)",
                    (buyer_id, address, hashed, name, now, now),
                )
                token = self._issue(conn, buyer_id)
                return token, self._user(conn, buyer_id)
        except sqlite3.IntegrityError as error:  # 동시 가입이 같은 이메일·ID를 먼저 넣은 경우
            raise ShopError("EMAIL_TAKEN", "이미 가입된 이메일이다", 409) from error

    def login(self, email: str, password: str) -> tuple[str, dict[str, Any]]:
        invalid = ShopError("INVALID_LOGIN", "이메일 또는 비밀번호가 맞지 않다", 401)
        address = email.strip().lower()
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT buyer_id, password_hash FROM accounts WHERE email = ?", (address,)
            ).fetchone()
        ok = verify_password(password, row["password_hash"] if row else _DUMMY_HASH)
        if row is None or not ok:
            raise invalid
        with self.db.tx() as conn:
            token = self._issue(conn, row["buyer_id"])
            return token, self._user(conn, row["buyer_id"])

    def demo_login(self, buyer_id: str) -> tuple[str, dict[str, Any]]:
        """비밀번호 없는 옛 데모 구매자에게 세션을 준다. 계정이 있는 구매자는 받지 않는다."""
        with self.db.tx() as conn:
            exists = conn.execute("SELECT 1 FROM buyers WHERE buyer_id = ?", (buyer_id,)).fetchone()
            if exists is None:
                raise ShopError("BUYER_NOT_FOUND", "구매자를 찾을 수 없다", 404)
            if conn.execute("SELECT 1 FROM accounts WHERE buyer_id = ?", (buyer_id,)).fetchone():
                raise ShopError("INVALID_LOGIN", "이메일 또는 비밀번호가 맞지 않다", 401)
            token = self._issue(conn, buyer_id)
            return token, self._user(conn, buyer_id)

    def demo_buyers(self, limit: int = DEMO_BUYERS_MAX) -> list[dict[str, Any]]:
        """계정이 없는 옛 데모 구매자만(시험용 로그인 화면). 이메일·성별·직업은 싣지 않는다."""
        with self.db.tx() as conn:
            rows = conn.execute(
                "SELECT b.buyer_id, b.age_band FROM buyers b WHERE NOT EXISTS "
                "(SELECT 1 FROM accounts a WHERE a.buyer_id = b.buyer_id) "
                "ORDER BY b.buyer_id LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "buyer_id": r["buyer_id"],
                "display_name": f"데모 구매자 {r['buyer_id'].rsplit('_', 1)[-1]}",
                "age_band": r["age_band"],
            }
            for r in rows
        ]

    def logout(self, token: str) -> None:
        with self.db.tx() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))

    def resolve(self, token: str) -> str | None:
        """토큰이 가리키는 구매자 ID. 없거나 만료면 None."""
        if not token:
            return None
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT buyer_id, expires_at FROM sessions WHERE token_hash = ?",
                (_token_hash(token),),
            ).fetchone()
            if row is None:
                return None
            if row["expires_at"] <= _stamp(_now()):
                conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
                return None
            return row["buyer_id"]

    # --- 내 정보 --------------------------------------------------------------
    def profile(self, buyer_id: str) -> dict[str, Any]:
        with self.db.tx() as conn:
            return self._user(conn, buyer_id)

    def update_profile(self, buyer_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """보낸 칸만 바꾼다(연령대·성별·직업군은 None이면 지운다, 이름은 지울 수 없다)."""
        names = [name for name in fields if name in _PROFILE_FIELDS]
        if not names:
            raise ShopError("INVALID_PROFILE", "바꿀 칸이 없다", 422)
        if "display_name" in names:
            if fields["display_name"] is None:
                raise ShopError("INVALID_PROFILE", "이름은 지울 수 없다", 422)
            fields = {**fields, "display_name": _clean_name(fields["display_name"])}
        _check_code("age_band", fields.get("age_band"), AGE_BANDS)
        _check_code("gender", fields.get("gender"), GENDERS)
        _check_code("occupation", fields.get("occupation"), OCCUPATIONS)
        with self.db.tx() as conn:
            if not conn.execute("SELECT 1 FROM buyers WHERE buyer_id = ?", (buyer_id,)).fetchone():
                raise ShopError("BUYER_NOT_FOUND", "구매자를 찾을 수 없다", 404)
            buyer_cols = [n for n in names if n != "display_name"]
            if "display_name" in names:
                changed = conn.execute(
                    "UPDATE accounts SET display_name = ? WHERE buyer_id = ?",
                    (fields["display_name"], buyer_id),
                ).rowcount
                if not changed:
                    raise ShopError(
                        "INVALID_PROFILE", "계정이 없는 구매자는 이름을 바꿀 수 없다", 422
                    )
            sets = ", ".join(
                [f"{n} = ?" for n in buyer_cols]
                + [
                    "updated_at = ?",
                    "change_seq = (SELECT COALESCE(MAX(change_seq), 0) + 1 FROM buyers)",
                ]
            )
            conn.execute(  # 열 이름은 위 고정 목록에서만 온다
                f"UPDATE buyers SET {sets} WHERE buyer_id = ?",  # noqa: S608
                [fields[n] for n in buyer_cols] + [_stamp(_now()), buyer_id],
            )
            return self._user(conn, buyer_id)

    def change_password(
        self, buyer_id: str, current: str, new: str, keep_token: str | None = None
    ) -> None:
        """현재 비밀번호가 맞으면 바꾼다(틀리면 401). 바꾸면 이 구매자의 다른 로그인 토큰은
        모두 끊고 `keep_token`(지금 쓰는 토큰)만 남긴다.
        """
        _check_password(new)
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT password_hash FROM accounts WHERE buyer_id = ?", (buyer_id,)
            ).fetchone()
        if row is None:
            raise ShopError("NO_PASSWORD", "비밀번호가 없는 계정이다", 409)
        if not verify_password(current, row["password_hash"]):
            raise ShopError("INVALID_PASSWORD", "현재 비밀번호가 맞지 않다", 401)
        hashed = hash_password(new)
        keep = _token_hash(keep_token) if keep_token else ""
        with self.db.tx() as conn:
            conn.execute(
                "UPDATE accounts SET password_hash = ? WHERE buyer_id = ?", (hashed, buyer_id)
            )
            conn.execute(
                "DELETE FROM sessions WHERE buyer_id = ? AND token_hash != ?", (buyer_id, keep)
            )

    def withdraw(self, buyer_id: str, password: str) -> None:
        """회원 탈퇴. 비밀번호가 맞으면(틀리면 403) 계정·후기·로그인 토큰을 지우고 구매자 속성을
        비운다(`retention.remove_account`). 주문·클레임 기록은 법정 보존이라 남는다.
        """
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT password_hash FROM accounts WHERE buyer_id = ?", (buyer_id,)
            ).fetchone()
        if row is None:
            raise ShopError("NO_PASSWORD", "비밀번호가 없는 계정이다", 409)
        if not verify_password(password, row["password_hash"]):
            raise ShopError("INVALID_PASSWORD", "비밀번호가 맞지 않다", 403)
        with self.db.tx() as conn:
            retention.remove_account(conn, buyer_id)
