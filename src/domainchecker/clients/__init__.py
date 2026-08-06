"""External data collectors. Every client fails soft: errors become UNCHECKED."""

from __future__ import annotations

# HTTP status -> what the user should actually do about it. A bare "401" tells a
# non-technical buyer nothing, and the number is what they see in the report.
_HTTP_REASON = {
    400: "보낸 요청을 거절했습니다(설정을 확인해 주세요).",
    401: "키가 틀렸습니다 — 설정 화면에서 키를 다시 넣어 주세요.",
    402: "쓸 수 있는 돈이 떨어졌습니다 — 해당 사이트에서 충전한 뒤 다시 해 주세요.",
    403: "키가 막혔거나 권한이 없습니다 — 설정 화면에서 키를 다시 확인해 주세요.",
    404: "찾는 자료가 없습니다.",
    429: "무료로 쓸 수 있는 횟수를 다 썼습니다 — 잠시 뒤에 다시 해 주세요.",
}


def http_reason(status_code: int) -> str:
    """Korean, actionable text for an HTTP failure (number kept for support)."""
    known = _HTTP_REASON.get(status_code)
    if known:
        return f"{known} (오류 번호 {status_code})"
    if 500 <= status_code < 600:
        return f"상대 사이트가 잠시 고장 났습니다 — 잠시 뒤에 다시 해 주세요. (오류 번호 {status_code})"
    return f"응답 오류가 났습니다. (오류 번호 {status_code})"
