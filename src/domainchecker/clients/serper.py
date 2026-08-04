"""Serper.dev — one `site:<domain>` query for index count and top titles."""

from __future__ import annotations

import httpx

from ..models import CheckState, CheckStatus, IndexInfo

URL = "https://google.serper.dev/search"

# 색인 오염 판단용 최소 표지. 현재 파킹 문구와는 구분해서 쓴다.
CONTAMINATION_TERMS = (
    "카지노",
    "바카라",
    "토토",
    "먹튀",
    "슬롯",
    "성인",
    "야동",
    "비아그라",
    "casino",
    "baccarat",
    "poker",
    "viagra",
    "cialis",
    "porn",
    "xxx",
    "escort",
    "replica",
    "オンラインカジノ",
    "エロ",
    "赌场",
    "博彩",
    "色情",
)

PARKING_TERMS = (
    "this domain is for sale",
    "domain for sale",
    "buy this domain",
    "parked",
    "parking",
    "도메인 판매",
    "판매 중인 도메인",
    "sedo",
    "afternic",
    "dan.com",
)


async def check(domain: str, api_key: str, http: httpx.AsyncClient) -> IndexInfo:
    result = IndexInfo()
    if not api_key:
        result.check = CheckState(
            status=CheckStatus.NOT_RUN,
            note="Serper 키가 없어 색인 검사를 못 했습니다(필수 검사라 ✅ 판정 불가).",
        )
        return result
    try:
        response = await http.post(
            URL,
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": f"site:{domain}", "num": 20},
        )
    except httpx.HTTPError:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="색인 검사 접속 실패.")
        return result
    if response.status_code != 200:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note=f"색인 검사 응답 오류({response.status_code})."
        )
        return result
    try:
        data = response.json()
    except ValueError:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="색인 검사 응답 해석 실패.")
        return result

    organic = data.get("organic") or []
    result.titles = [str(item.get("title", "")).strip() for item in organic if item.get("title")]
    snippets = [str(item.get("snippet", "")) for item in organic]
    total = (data.get("searchInformation") or {}).get("totalResults")
    try:
        result.indexed_count = int(total)
    except (TypeError, ValueError):
        result.indexed_count = len(organic)

    haystack = " ".join(result.titles + snippets).lower()
    result.current_parking = any(term in haystack for term in PARKING_TERMS)
    hits = sorted({term for term in CONTAMINATION_TERMS if term.lower() in haystack})
    # 현재 파킹 페이지의 광고 문구를 과거 이력 탓으로 돌리지 않는다.
    result.contamination_terms = hits
    result.contaminated = bool(hits) and not result.current_parking
    if result.indexed_count == 0:
        note = "색인된 페이지가 없습니다(만료 도메인의 기본값 — 중립)."
    elif result.contaminated:
        note = "색인에 위험 업종 문구가 남아 있습니다."
    elif result.current_parking:
        note = "현재 파킹 페이지가 색인되어 있습니다(과거 이력과 별개)."
    else:
        note = f"색인 {result.indexed_count}건."
    result.check = CheckState(status=CheckStatus.OK, note=note)
    return result
