"""VirusTotal (optional). Free tier allows 4 requests per minute, so it gets its
own limiter and runs on a separate lane from the Wayback budget."""

from __future__ import annotations

import httpx

from ..models import CheckState, CheckStatus, MalwareScan
from ..ratelimit import AdaptiveRateLimiter
from . import http_reason

URL = "https://www.virustotal.com/api/v3/domains/{domain}"
RPM = 4


def make_limiter() -> AdaptiveRateLimiter:
    return AdaptiveRateLimiter(rpm=RPM, degraded_rpm=2)


async def check(
    domain: str,
    api_key: str,
    http: httpx.AsyncClient,
    limiter: AdaptiveRateLimiter | None = None,
    enabled: bool = True,
) -> MalwareScan:
    result = MalwareScan()
    if not enabled:
        result.check = CheckState(status=CheckStatus.NOT_RUN, note="바이러스토탈 검사를 껐습니다.")
        return result
    if not api_key:
        result.check = CheckState(status=CheckStatus.NOT_RUN, note="바이러스토탈 키가 없어 미실시.")
        return result
    if limiter is not None:
        await limiter.acquire()
    try:
        response = await http.get(URL.format(domain=domain), headers={"x-apikey": api_key})
    except httpx.HTTPError:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="바이러스토탈 접속 실패.")
        return result
    if response.status_code == 429 and limiter is not None:
        limiter.note_429()
    if response.status_code == 404:
        result.check = CheckState(status=CheckStatus.OK, note="바이러스토탈에 기록이 없습니다.")
        return result
    if response.status_code != 200:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED,
            note="바이러스토탈 검사를 못 했습니다 — " + http_reason(response.status_code),
        )
        return result
    try:
        data = response.json()
    except ValueError:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="바이러스토탈 응답 해석 실패.")
        return result

    stats = ((data.get("data") or {}).get("attributes") or {}).get("last_analysis_stats") or {}
    result.malicious = int(stats.get("malicious") or 0)
    result.suspicious = int(stats.get("suspicious") or 0)
    if result.malicious:
        note = f"악성 판정 {result.malicious}건."
    elif result.suspicious:
        note = f"의심 판정 {result.suspicious}건."
    else:
        note = "악성 판정 없음."
    result.check = CheckState(status=CheckStatus.OK, note=note)
    return result
