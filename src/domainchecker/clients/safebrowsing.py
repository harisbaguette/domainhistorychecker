"""Google Safe Browsing v4 Lookup (optional check; no key -> NOT_RUN)."""

from __future__ import annotations

import httpx

from ..models import CheckState, CheckStatus, Reputation

URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"

_THREAT_LABEL = {
    "MALWARE": "악성코드",
    "SOCIAL_ENGINEERING": "피싱·사회공학",
    "UNWANTED_SOFTWARE": "원치 않는 소프트웨어",
    "POTENTIALLY_HARMFUL_APPLICATION": "유해 앱",
}


async def check(domain: str, api_key: str, http: httpx.AsyncClient) -> Reputation:
    result = Reputation()
    if not api_key:
        result.check = CheckState(
            status=CheckStatus.NOT_RUN, note="세이프 브라우징 키가 없어 미실시."
        )
        return result
    body = {
        "client": {"clientId": "domainchecker", "clientVersion": "0.1.0"},
        "threatInfo": {
            "threatTypes": list(_THREAT_LABEL),
            "platformTypes": ["ANY_PLATFORM"],
            "threatEntryTypes": ["URL"],
            "threatEntries": [{"url": f"http://{domain}/"}, {"url": f"https://{domain}/"}],
        },
    }
    try:
        response = await http.post(URL, params={"key": api_key}, json=body)
    except httpx.HTTPError:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="세이프 브라우징 접속 실패.")
        return result
    if response.status_code != 200:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note=f"세이프 브라우징 응답 오류({response.status_code})."
        )
        return result
    try:
        data = response.json()
    except ValueError:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="세이프 브라우징 응답 해석 실패.")
        return result

    matches = data.get("matches") or []
    if matches:
        result.listed = True
        result.codes = sorted(
            {_THREAT_LABEL.get(m.get("threatType", ""), m.get("threatType", "위협")) for m in matches}
        )
        result.check = CheckState(
            status=CheckStatus.OK, note="세이프 브라우징 등재: " + ", ".join(result.codes)
        )
    else:
        result.check = CheckState(status=CheckStatus.OK, note="세이프 브라우징 등재 없음.")
    return result
