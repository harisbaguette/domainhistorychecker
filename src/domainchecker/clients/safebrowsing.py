"""구글 세이프 브라우징 — 키 없이 공개 조회 창구로 확인한다.

정식 조회 창구(API)는 키가 있어야 쓴다. 대신 구글이 누구나 보게 열어 둔
공개 안전 조회 페이지(transparencyreport.google.com)의 뒷문 주소를 쓰면
키 없이 같은 등재 여부를 알 수 있다. 공식 문서가 없는 창구라 응답 모양이
바뀔 수 있는데, 그때는 "미확인"으로 두고 나머지 검사는 그대로 간다.

응답 실측(2026-08-09): 깨끗한 도메인은 상태값 1, 위험 도메인은 상태값 2~3에
위협 종류 표지(참/거짓 다섯 칸)가 참으로 온다. 맨 앞의 ")]}'" 는 구글이
스크립트 도용을 막으려고 일부러 붙여 두는 글자라 떼고 읽어야 한다.
"""

from __future__ import annotations

import json

import httpx

from ..models import CheckState, CheckStatus, Reputation
from . import http_reason

URL = "https://transparencyreport.google.com/transparencyreport/api/v3/safebrowsing/status"

# 위협 표지 다섯 칸의 순서(실측·공개 페이지 표기 기준).
_FLAG_LABELS = ("악성코드", "피싱·사회공학", "원치 않는 소프트웨어", "유해 앱", "드문 내려받기")


def _parse(text: str) -> tuple[int, list[bool]] | None:
    """응답에서 (상태값, 위협 표지들)을 꺼낸다. 모양이 다르면 None."""
    try:
        data = json.loads(text.lstrip(")]}'\r\n "))
    except ValueError:
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return None
    row = data[0]
    try:
        status = int(row[1])
    except (IndexError, TypeError, ValueError):
        return None
    flags = [bool(value) for value in row[2:7] if isinstance(value, bool)]
    return status, flags


async def check(domain: str, http: httpx.AsyncClient) -> Reputation:
    result = Reputation()
    try:
        response = await http.get(URL, params={"site": domain})
    except httpx.HTTPError:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="세이프 브라우징 접속 실패.")
        return result
    if response.status_code != 200:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED,
            note="세이프 브라우징 검사를 못 했습니다 — " + http_reason(response.status_code),
        )
        return result
    parsed = _parse(response.text)
    if parsed is None:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note="세이프 브라우징 응답 해석 실패.")
        return result

    status, flags = parsed
    codes = [label for label, hit in zip(_FLAG_LABELS, flags, strict=False) if hit]
    if codes or status >= 2:
        result.listed = True
        result.codes = codes or ["위험 등재"]
        result.check = CheckState(
            status=CheckStatus.OK, note="세이프 브라우징 등재: " + ", ".join(result.codes)
        )
    else:
        result.check = CheckState(status=CheckStatus.OK, note="세이프 브라우징 등재 없음.")
    return result
