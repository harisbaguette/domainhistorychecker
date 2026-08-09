"""접속 잠금 — 아이디·비밀번호로 한 번 들어오면 쿠키로 계속 들어온다.

서버리스 칸(베르셀)은 접속한 사람 목록을 서버에 들고 있지 못한다. 그래서
"이 사람 로그인했음"이라는 쪽지를 서버 도장(비밀번호에서 만든 서명)과 함께
브라우저에 맡기고, 올 때마다 도장이 진짜인지만 확인한다. 도장 만드는 재료가
비밀번호라서, 비밀번호를 바꾸면 예전 쪽지는 저절로 못 쓰게 된다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

COOKIE_NAME = "domainchecker_session"
DEFAULT_USER = "domainchecker"
REMEMBER_SECONDS = 30 * 24 * 60 * 60  # "로그인 유지"에 체크했을 때
SESSION_SECONDS = 12 * 60 * 60  # 체크 안 했을 때(창을 닫으면 그때 사라진다)

# 로그인 잠금 없이 지나갈 수 있는 자리 — 로그인 화면 자체와 그 모양·글꼴뿐이다.
PUBLIC_PATHS = ("/login", "/api/login", "/api/logout", "/style.css", "/favicon.ico")
PUBLIC_PREFIXES = ("/fonts/",)


def account() -> tuple[str, str]:
    """이 서버가 받아 주는 아이디·비밀번호. 비밀번호가 비어 있으면 잠금이 없다."""
    user = os.environ.get("DOMAINCHECKER_USER", "").strip() or DEFAULT_USER
    return user, os.environ.get("DOMAINCHECKER_PASSWORD", "")


def locked() -> bool:
    return bool(account()[1])


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def check_login(user: str, password: str) -> bool:
    """아이디와 비밀번호가 둘 다 맞아야 참. 길이로 눈치채지 못하게 둘 다 끝까지 견준다."""
    real_user, real_password = account()
    if not real_password:
        return False
    ok_user = hmac.compare_digest(user.strip().encode("utf-8"), real_user.encode("utf-8"))
    ok_password = hmac.compare_digest(password.encode("utf-8"), real_password.encode("utf-8"))
    return ok_user and ok_password


def _secret() -> bytes:
    raw = os.environ.get("DOMAINCHECKER_SECRET", "") or account()[1]
    return hashlib.sha256(f"domainchecker-session:{raw}".encode()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user: str, ttl: int) -> str:
    """'누가·언제까지' 를 적고 서버 도장을 찍은 쪽지."""
    body = _b64(json.dumps({"u": user, "x": int(time.time()) + ttl}).encode("utf-8"))
    sign = _b64(hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).digest())
    return f"{body}.{sign}"


def valid_token(token: str) -> bool:
    """도장이 진짜이고, 아직 기한이 남았고, 지금 계정과 같은 이름일 때만 참."""
    if not token or "." not in token:
        return False
    body, _, sign = token.partition(".")
    expected = _b64(hmac.new(_secret(), body.encode("utf-8"), hashlib.sha256).digest())
    if not hmac.compare_digest(sign, expected):
        return False
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    if float(payload.get("x", 0)) < time.time():
        return False
    return payload.get("u") == account()[0]


class Attempts:
    """비밀번호를 마구 찍어 보는 것을 늦춘다.

    같은 곳에서 5번 틀리면 1분 동안 아예 안 받아 준다. 서버리스 칸은 여러 개가
    번갈아 뜨므로 칸마다 따로 센다 — 완벽한 자물쇠는 아니고 속도만 늦추는 턱이다.
    simplify: 사용자가 1명뿐이라 이 정도면 충분하다. 사람이 늘면 저장소를 쓰는
    방식(Upstash 등)으로 올린다.
    """

    LIMIT = 5
    COOLDOWN = 60

    def __init__(self) -> None:
        self._fails: dict[str, tuple[int, float]] = {}

    def blocked(self, who: str) -> bool:
        count, until = self._fails.get(who, (0, 0.0))
        return count >= self.LIMIT and time.time() < until

    def failed(self, who: str) -> None:
        count, _ = self._fails.get(who, (0, 0.0))
        self._fails[who] = (count + 1, time.time() + self.COOLDOWN)

    def passed(self, who: str) -> None:
        self._fails.pop(who, None)
