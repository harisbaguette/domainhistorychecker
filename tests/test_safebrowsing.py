"""세이프 브라우징 — 키 없는 공개 조회 창구(투명성 보고서) 응답 처리."""

import httpx
import pytest
import respx

from domainchecker.clients import safebrowsing
from domainchecker.models import CheckStatus


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def reply(text: str) -> httpx.Response:
    return httpx.Response(200, text=text)


@respx.mock
async def test_a_clean_domain_is_not_listed(http):
    # 2026-08-09 실측 응답 그대로 — 맨 앞 ")]}'" 를 떼고 읽어야 한다.
    respx.get(url__startswith=safebrowsing.URL).mock(
        return_value=reply(")]}'\n\n[[\"sb.ssr\",1,false,false,false,false,false,1786277573219,\"example.com\"]]")
    )
    result = await safebrowsing.check("example.com", http)

    assert result.check.status is CheckStatus.OK
    assert result.listed is False
    assert "등재 없음" in result.check.note


@respx.mock
async def test_a_flagged_domain_is_listed_with_threat_labels(http):
    respx.get(url__startswith=safebrowsing.URL).mock(
        return_value=reply(")]}'\n\n[[\"sb.ssr\",3,true,true,true,false,false,1786272198188,\"bad.test\"]]")
    )
    result = await safebrowsing.check("bad.test", http)

    assert result.check.status is CheckStatus.OK
    assert result.listed is True
    assert "악성코드" in result.codes
    assert "피싱·사회공학" in result.codes


@respx.mock
async def test_a_dangerous_status_without_flags_still_counts_as_listed(http):
    respx.get(url__startswith=safebrowsing.URL).mock(
        return_value=reply(")]}'\n\n[[\"sb.ssr\",2,false,false,false,false,false,1,\"odd.test\"]]")
    )
    result = await safebrowsing.check("odd.test", http)

    assert result.listed is True
    assert result.codes == ["위험 등재"]


@respx.mock
async def test_a_famous_clean_site_status_4_is_not_listed(http):
    """2026-08-10 실측: google.com·naver.com 이 상태값 4로 온다 — 위험이 아니다."""
    respx.get(url__startswith=safebrowsing.URL).mock(
        return_value=reply(")]}'\n\n[[\"sb.ssr\",4,false,false,false,false,false,1767633340523,\"google.com\"]]")
    )
    result = await safebrowsing.check("google.com", http)

    assert result.check.status is CheckStatus.OK
    assert result.listed is False
    assert "등재 없음" in result.check.note


@respx.mock
async def test_a_never_seen_domain_status_6_is_not_listed(http):
    """2026-08-10 실측: 아무도 안 쓰는 도메인은 상태값 6(자료 없음)으로 온다."""
    respx.get(url__startswith=safebrowsing.URL).mock(
        return_value=reply(")]}'\n\n[[\"sb.ssr\",6,false,false,false,false,false,0,\"fresh.test\"]]")
    )
    result = await safebrowsing.check("fresh.test", http)

    assert result.check.status is CheckStatus.OK
    assert result.listed is False
    assert "자료 없는" in result.check.note


@respx.mock
async def test_an_unknown_status_value_degrades_to_unchecked(http):
    """모르는 상태값을 위험으로 단정하면 멀쩡한 도메인이 전부 탈락한다."""
    respx.get(url__startswith=safebrowsing.URL).mock(
        return_value=reply(")]}'\n\n[[\"sb.ssr\",9,false,false,false,false,false,1,\"odd.test\"]]")
    )
    result = await safebrowsing.check("odd.test", http)

    assert result.check.status is CheckStatus.UNCHECKED
    assert result.listed is False


@respx.mock
async def test_an_unexpected_answer_degrades_to_unchecked_not_clean(http):
    """모양이 바뀐 응답을 '깨끗함'으로 읽으면 위험 도메인에 초록이 나간다."""
    respx.get(url__startswith=safebrowsing.URL).mock(return_value=reply("<html>점검 중</html>"))
    result = await safebrowsing.check("example.com", http)

    assert result.check.status is CheckStatus.UNCHECKED
    assert result.listed is False


@respx.mock
async def test_a_blocked_or_failed_call_degrades_to_unchecked(http):
    respx.get(url__startswith=safebrowsing.URL).mock(return_value=httpx.Response(429))
    result = await safebrowsing.check("example.com", http)
    assert result.check.status is CheckStatus.UNCHECKED

    respx.get(url__startswith=safebrowsing.URL).mock(side_effect=httpx.ConnectError("no net"))
    again = await safebrowsing.check("example.com", http)
    assert again.check.status is CheckStatus.UNCHECKED
