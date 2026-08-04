"""Serper.dev — one `site:<domain>` query for index count and top titles."""

from __future__ import annotations

import re

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

# 파킹 표지 — 영문은 단어 경계로만 맞춘다.
# 부분 문자열로 세면 "Sedona"가 sedo로, "parking lot"이 parking으로 잘못 잡힌다.
# 그래서 홀로 쓰이면 뜻이 넓은 낱말(parking)은 파킹 문맥 구(句)로만 남겼다.
PARKING_TERMS = (
    "this domain is for sale",
    "domain for sale",
    "buy this domain",
    "domain parking",
    "parking page",
    "parked",
    "sedo",
    "afternic",
    "dan.com",
)

# 한국어는 낱말 경계(\b)가 통하지 않아 구 전체 포함으로 맞춘다.
PARKING_PHRASES_KO = (
    "도메인 판매",
    "판매 중인 도메인",
    "도메인 팝니다",
)

_PARKING_RE = re.compile(
    "|".join(rf"\b{re.escape(term)}\b" for term in PARKING_TERMS), re.IGNORECASE
)


def is_parking_text(text: str) -> bool:
    """True when this one search result reads like a parking/for-sale page."""
    if _PARKING_RE.search(text):
        return True
    return any(phrase in text for phrase in PARKING_PHRASES_KO)


def contamination_hits(text: str) -> list[str]:
    lowered = text.lower()
    return sorted({term for term in CONTAMINATION_TERMS if term.lower() in lowered})


async def check(domain: str, api_key: str, http: httpx.AsyncClient) -> IndexInfo:
    result = IndexInfo()
    if not api_key:
        result.check = CheckState(
            status=CheckStatus.NOT_RUN,
            note="Serper 키가 없어 색인 검사를 못 했습니다(필수 검사라 매입 후보 판정 불가).",
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
    total = (data.get("searchInformation") or {}).get("totalResults")
    try:
        result.indexed_count = int(total)
    except (TypeError, ValueError):
        result.indexed_count = len(organic)

    # 판단은 검색 결과 "항목별"로 한다. 한 항목이 파킹이라고 해서 나머지 항목의
    # 위험 업종 문구까지 파킹 광고로 덮어 버리면 오염 도메인을 통째로 놓친다.
    entries = [
        (str(item.get("title", "")).strip(), str(item.get("snippet", "")))
        for item in organic
    ]
    parked = [is_parking_text(f"{title} {snippet}") for title, snippet in entries]
    result.current_parking = any(parked)
    clean_text = " ".join(
        f"{title} {snippet}"
        for (title, snippet), is_parked in zip(entries, parked, strict=True)
        if not is_parked
    )
    # 파킹 항목의 광고 문구는 빼고, 남은 항목에서만 오염어를 센다.
    result.contamination_terms = contamination_hits(clean_text)
    result.contaminated = bool(result.contamination_terms)
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
