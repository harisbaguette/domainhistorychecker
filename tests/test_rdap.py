import httpx
import pytest
import respx

from domainchecker.clients.rdap import RdapClient, decode_whois, parse_whois
from domainchecker.models import CheckStatus

RDAP_JSON = {
    "events": [
        {"eventAction": "registration", "eventDate": "2009-04-01T00:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2026-04-01T00:00:00Z"},
    ],
    "status": ["client transfer prohibited"],
    "entities": [
        {"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "Example Registrar"]]]}
    ],
}

WHOIS_KR = """Domain Name                 : example.kr
Registered Date             : 2004. 07. 12.
Expiration Date             : 2027. 07. 12.
Domain Status               : redemptionPeriod
"""


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@respx.mock
async def test_rdap_success_parses_dates_and_status(http):
    respx.get("https://rdap.org/domain/example.com").mock(
        return_value=httpx.Response(200, json=RDAP_JSON)
    )
    result = await RdapClient(http).fetch("example.com")

    assert result.check.status is CheckStatus.OK
    assert result.source == "rdap"
    assert result.created == "2009-04-01"
    assert result.expires == "2026-04-01"
    assert result.registrar == "Example Registrar"
    assert result.acquisition == "등록 중(이전 잠금)"


@respx.mock
async def test_rdap_404_means_available(http):
    respx.get("https://rdap.org/domain/free.com").mock(return_value=httpx.Response(404))
    result = await RdapClient(http).fetch("free.com")

    assert result.check.status is CheckStatus.OK
    assert result.acquisition == "구매 가능(미등록)"


@respx.mock
async def test_falls_back_to_whois_when_rdap_fails(http):
    respx.get("https://rdap.org/domain/example.kr").mock(return_value=httpx.Response(500))
    asked = {}

    async def fake_whois(domain, server):
        asked["domain"], asked["server"] = domain, server
        return WHOIS_KR

    result = await RdapClient(http, whois_query=fake_whois).fetch("example.kr")

    assert asked == {"domain": "example.kr", "server": "whois.kr"}
    assert result.source == "whois"
    assert result.check.status is CheckStatus.OK
    assert result.created == "2004-07-12"
    assert result.expires == "2027-07-12"
    assert result.acquisition == "복원 기간(경매·복원 대상)"


@respx.mock
async def test_both_sources_failing_is_unchecked(http):
    respx.get("https://rdap.org/domain/dead.com").mock(side_effect=httpx.ConnectError("boom"))

    async def broken_whois(domain, server):
        raise OSError("refused")

    result = await RdapClient(http, whois_query=broken_whois).fetch("dead.com")
    assert result.check.status is CheckStatus.UNCHECKED
    assert "실패" in result.check.note


def test_whois_no_match_means_available():
    result = parse_whois("No match for DOESNOTEXIST.COM")
    assert result.acquisition == "구매 가능(미등록)"


def test_euckr_whois_is_decoded():
    payload = "등록일 : 2004. 07. 12.".encode("euc-kr")
    assert decode_whois(payload).startswith("등록일")
