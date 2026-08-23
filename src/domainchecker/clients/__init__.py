"""External data collectors. Every client fails soft: errors become UNCHECKED.

바깥 조회는 **한 번 실패했다고 접지 않는다.** 남의 서버는 바쁘면 429(잠깐 그만
두드려라)나 5xx(잠깐 고장)를 던지는데, 그걸 곧바로 "확인 못 함"으로 적으면 멀쩡한
도메인이 우리 사정 때문에 노랑으로 주저앉는다(실측 2026-08-23: 저장된 결과 17건 중
세이프 브라우징 3건·색인 2건이 이런 일시적 막힘으로 미확인이었고, 같은 도메인을
바로 다시 물으니 전부 정상 응답). 웨이백 쪽은 이미 이 요령을 쓰고 있었고
(clients/wayback.py `_get`), 나머지 조회에도 같은 요령을 한 벌로 나눠 쓴다.
"""

from __future__ import annotations

import asyncio

import httpx

# 다시 묻기 전에 쉬는 시간(초). 세 번까지 두드린다 — 잠깐 막힌 것은 이 사이에 풀린다.
RETRY_WAITS = (0.5, 2.0)


def _retryable(response: httpx.Response | None) -> bool:
    """지금은 안 되지만 조금 뒤엔 될 수도 있는 답인가."""
    if response is None:
        return True  # 접속 자체가 안 됨(시간 초과·연결 끊김)
    return response.status_code == 429 or response.status_code >= 500


async def request_retry(
    http: httpx.AsyncClient, method: str, url: str, **kwargs
) -> httpx.Response | None:
    """요청 한 번 — 잠깐 막힌 답(429·5xx·시간 초과)이면 쉬었다 다시 묻는다.

    None = 끝내 접속 자체를 못 했다. 상태 코드 해석은 부르는 쪽이 한다.
    쉬는 시간은 부를 때마다 읽는다 — 시험에서 0으로 갈아 끼울 수 있게.
    """
    waits = RETRY_WAITS
    response: httpx.Response | None = None
    for attempt in range(len(waits) + 1):
        try:
            response = await http.request(method, url, **kwargs)
        except httpx.HTTPError:
            response = None
        if not _retryable(response):
            return response
        if attempt < len(waits):
            await asyncio.sleep(waits[attempt])
    return response


async def get_retry(
    http: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    timeout: float | httpx.Timeout | None = None,
    headers: dict | None = None,
) -> httpx.Response | None:
    """GET — 잠깐 막힌 것은 쉬었다 다시."""
    kwargs: dict = {}
    if params is not None:
        kwargs["params"] = params
    if timeout is not None:
        kwargs["timeout"] = timeout
    if headers is not None:
        kwargs["headers"] = headers
    return await request_retry(http, "GET", url, **kwargs)


async def post_retry(
    http: httpx.AsyncClient, url: str, *, data: dict | None = None
) -> httpx.Response | None:
    """POST — 잠깐 막힌 것은 쉬었다 다시."""
    return await request_retry(http, "POST", url, data=data)

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
