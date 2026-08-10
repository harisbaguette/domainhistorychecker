"""가비아 도메인 검색과 같은 확인 — 판매처에 직접 물어본다.

등록 기관 자료(RDAP·whois)를 읽고 해석하는 대신, 실제 판매처(가비아)의
검색창이 쓰는 것과 똑같은 요청을 보낸다. 판매처가 "등록 가능"이라고 답하면
그게 곧 살 수 있다는 뜻이라 따로 해석할 것이 없다.
"""

from __future__ import annotations

import ssl

import httpx

from ..models import CheckState, CheckStatus, Registration

CHECK_URL = "https://domain.gabia.com/ajax_lib/check/regist.php"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://domain.gabia.com/regist/regist_step1.php",
    "X-Requested-With": "XMLHttpRequest",
}

# 가비아 서버는 파이썬 기본 보안 등급(2)이 거절하는 오래된 암호로만 접속을
# 받는다(기본값으로 붙으면 handshake failure). 등급을 1로 낮춘 전용 연결을
# 쓴다 — 인증서 검증은 그대로 한다. 이 연결은 가비아에만 쓴다.
_context: ssl.SSLContext | None = None


def ssl_context() -> ssl.SSLContext:
    global _context
    if _context is None:
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        _context = ctx
    return _context


async def check(domain: str, timeout: float = 15.0) -> Registration:
    """한 도메인의 구매 가능 여부. 실패하면 UNCHECKED로 내려간다."""
    result = Registration(source="gabia")
    try:
        async with httpx.AsyncClient(
            timeout=timeout, verify=ssl_context(), headers=HEADERS
        ) as http:
            response = await http.post(CHECK_URL, data={"domain": domain})
    except httpx.HTTPError as exc:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note=f"가비아 접속 실패({type(exc).__name__})."
        )
        return result
    if response.status_code != 200:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note=f"가비아 응답 오류({response.status_code})."
        )
        return result
    try:
        data = response.json()
    except ValueError:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="가비아 응답 해석 실패.")
        return result
    if not isinstance(data, dict):
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="가비아 응답 형식이 다릅니다.")
        return result
    return parse(data, result)


def parse(data: dict, result: Registration | None = None) -> Registration:
    """가비아 검색 응답(JSON) → 취득 상태.

    flag "Y" = 등록 가능, "A"/"F"/"" = 등록 불가. status "BO"는 삭제 예정이라
    사전 예약이 되는 도메인, "PUBIC"은 국가 기관만 쓸 수 있는 예약어다.
    """
    result = result or Registration(source="gabia")
    flag = str(data.get("flag") or "")
    status = str(data.get("status") or "")
    label = str(data.get("result") or "")
    backorder = str(data.get("backorder_date") or "")
    result.statuses = [s for s in (flag, status) if s]

    if status == "BO" or backorder:
        result.acquisition = "삭제 대기(곧 등록 가능)"
        note = "삭제 예정이라 사전 예약 등록이 가능합니다."
        if backorder:
            note += f" 예약 기준일: {backorder}"
    elif flag == "Y" and status == "PUBIC":
        result.acquisition = "등록 제한(예약어·기관 전용)"
        note = "국가 기관 등만 등록할 수 있는 예약어 도메인입니다."
    elif flag == "Y":
        result.acquisition = "구매 가능(미등록)"
        note = "가비아에서 바로 등록할 수 있습니다."
    elif flag in ("A", "F", ""):
        result.acquisition = "등록 중"
        note = label or "이미 등록된 도메인입니다."
    else:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note=f"가비아 응답을 알아볼 수 없습니다(flag={flag})."
        )
        return result

    result.check = CheckState(status=CheckStatus.OK, note=note)
    return result
