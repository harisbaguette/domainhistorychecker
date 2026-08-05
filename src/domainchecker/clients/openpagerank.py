"""Open PageRank — authority score, queried 100 domains per call."""

from __future__ import annotations

import httpx

from ..models import Authority, CheckState, CheckStatus

URL = "https://openpagerank.com/api/v1.0/getPageRank"
BATCH = 100


async def fetch_batch(
    domains: list[str], api_key: str, http: httpx.AsyncClient
) -> dict[str, Authority]:
    """Return one Authority per requested domain (never raises)."""
    if not api_key:
        state = CheckState(
            status=CheckStatus.NOT_RUN,
            note="Open PageRank 키가 없어 권위 점수를 못 봤습니다(필수 검사라 매입 후보 판정 불가).",
        )
        return {d: Authority(check=state) for d in domains}

    results: dict[str, Authority] = {}
    for start in range(0, len(domains), BATCH):
        chunk = domains[start : start + BATCH]
        results.update(await _one_call(chunk, api_key, http))
    return results


async def _one_call(chunk: list[str], api_key: str, http: httpx.AsyncClient) -> dict[str, Authority]:
    def failed(note: str) -> dict[str, Authority]:
        state = CheckState(status=CheckStatus.UNCHECKED, note=note)
        return {d: Authority(check=state) for d in chunk}

    try:
        response = await http.get(
            URL,
            params=[("domains[]", d) for d in chunk],
            headers={"API-OPR": api_key},
        )
    except httpx.HTTPError:
        return failed("권위 점수 조회 접속 실패.")
    if response.status_code != 200:
        return failed(f"권위 점수 응답 오류({response.status_code}).")
    try:
        data = response.json()
    except ValueError:
        return failed("권위 점수 응답 해석 실패.")

    out = failed("응답에 해당 도메인이 없습니다.")
    for row in data.get("response") or []:
        domain = str(row.get("domain", "")).lower()
        if domain not in out:
            continue
        if int(row.get("status_code", 0)) != 200:
            out[domain] = Authority(
                check=CheckState(
                    status=CheckStatus.OK, note="권위 점수 자료가 없습니다(신규·무링크로 추정)."
                )
            )
            continue
        try:
            rank_value = float(row.get("page_rank_decimal") or 0)
        except (TypeError, ValueError):
            rank_value = 0.0
        try:
            rank = int(row.get("rank")) if row.get("rank") else None
        except (TypeError, ValueError):
            rank = None
        out[domain] = Authority(
            check=CheckState(status=CheckStatus.OK),
            page_rank=rank_value,
            rank=rank,
        )
    return out
