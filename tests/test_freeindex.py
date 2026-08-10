"""키 없이 도는 색인 검사 — 커먼크롤 + 현재 페이지."""

import httpx
import pytest
import respx

from domainchecker.clients import freeindex
from domainchecker.models import CheckStatus

CDX = "https://index.commoncrawl.org/CC-MAIN-2026-30-index"
COLLINFO = [
    {"id": "CC-MAIN-2026-30", "cdx-api": CDX},
    {"id": "CC-MAIN-2026-26", "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-26-index"},
]


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture(autouse=True)
def _fresh_collection():
    freeindex.reset_collection_cache()
    yield
    freeindex.reset_collection_cache()


def crawl_lines(*urls):
    return "\n".join(f'{{"url": "{u}", "status": "200"}}' for u in urls)


def mock_collinfo():
    respx.get(freeindex.COLLINFO_URL).mock(return_value=httpx.Response(200, json=COLLINFO))


def page(html):
    return httpx.Response(200, html=html, headers={"Content-Type": "text/html; charset=utf-8"})


@respx.mock
async def test_healthy_domain_counts_crawled_pages_and_reads_the_live_page(http):
    mock_collinfo()
    respx.get(url__startswith=CDX).mock(
        return_value=httpx.Response(
            200, text=crawl_lines("https://example.com/", "https://example.com/about")
        )
    )
    respx.get("https://example.com/").mock(
        return_value=page("<html><head><title>보통 회사</title></head><body>안내 문서</body></html>")
    )

    result = await freeindex.check("example.com", http)

    assert result.check.status is CheckStatus.OK
    assert result.indexed_count == 2
    assert result.titles == ["보통 회사"]
    assert result.current_parking is False
    assert "무료 공개 자료" in result.check.note


@respx.mock
async def test_redirect_to_a_domain_seller_is_read_as_parking(http):
    mock_collinfo()
    respx.get(url__startswith=CDX).mock(return_value=httpx.Response(404, text="No Captures found"))
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(301, headers={"Location": "https://sedo.com/search/details/?domain=example.com"})
    )
    respx.get(url__startswith="https://sedo.com/").mock(return_value=page("<html><body>Angebot</body></html>"))

    result = await freeindex.check("example.com", http)

    assert result.check.status is CheckStatus.OK
    assert result.current_parking is True
    assert result.indexed_count == 0  # 404 는 "색인에 없다"는 정상 답


@respx.mock
async def test_a_seller_page_that_blocks_us_is_still_read_as_parking(http):
    """판매 업체 쪽이 '사람 확인'으로 막아도, 거기로 넘어갔다는 사실만으로 파킹이다."""
    mock_collinfo()
    respx.get(url__startswith=CDX).mock(return_value=httpx.Response(200, text=crawl_lines()))
    respx.get("https://example.com/").mock(side_effect=httpx.ConnectError("bad certificate"))
    respx.get("http://example.com/").mock(
        return_value=httpx.Response(301, headers={"Location": "https://sedo.com/search/details/"})
    )
    respx.get(url__startswith="https://sedo.com/").mock(return_value=httpx.Response(403, text="Just a moment"))

    result = await freeindex.check("example.com", http)

    assert result.check.status is CheckStatus.OK
    assert result.current_parking is True
    assert "현재 파킹 페이지" in result.check.note


@respx.mock
async def test_crawled_paths_are_sampled_for_the_ai_without_the_domain_name(http):
    """색인에 남은 주소 흔적은 AI가 읽을 표본으로 담는다 — 도메인 이름은 넣지 않는다."""
    mock_collinfo()
    respx.get(url__startswith=CDX).mock(
        return_value=httpx.Response(200, text=crawl_lines("https://casino-shop.com/products/mug"))
    )
    respx.get("https://casino-shop.com/").mock(return_value=httpx.Response(500))
    respx.get("http://casino-shop.com/").mock(return_value=httpx.Response(500))

    result = await freeindex.check("casino-shop.com", http)

    assert result.sample_paths == ["/products/mug"]
    assert all("casino-shop.com" not in path for path in result.sample_paths)


@respx.mock
async def test_an_everyday_sentence_about_parking_a_car_is_not_a_parking_page(http):
    """'parked' 같은 낱말 하나로 파킹이라 부르면 멀쩡한 도메인이 잘못 표시된다."""
    mock_collinfo()
    respx.get(url__startswith=CDX).mock(return_value=httpx.Response(200, text=crawl_lines("https://example.com/")))
    respx.get("https://example.com/").mock(
        return_value=page("<html><head><title>여행 일기</title></head><body>Where I parked my car last night.</body></html>")
    )

    result = await freeindex.check("example.com", http)

    assert result.current_parking is False


@respx.mock
async def test_korean_paths_hidden_in_an_encoded_url_are_decoded(http):
    """주소에 감싸인 한글(%EC…)을 안 풀면 AI도 사람도 흔적을 못 읽는다."""
    mock_collinfo()
    respx.get(url__startswith=CDX).mock(
        return_value=httpx.Response(
            200, text=crawl_lines("https://shop.com/%EC%B9%B4%EC%A7%80%EB%85%B8/join")
        )
    )
    respx.get("https://shop.com/").mock(side_effect=httpx.ConnectError("dead"))
    respx.get("http://shop.com/").mock(side_effect=httpx.ConnectError("dead"))

    result = await freeindex.check("shop.com", http)

    assert result.sample_paths == ["/카지노/join"]


@respx.mock
async def test_knowing_nothing_is_not_reported_as_a_clean_check(http):
    """커먼크롤도 실패하고 페이지도 못 읽었으면 '확인함'이라 말하면 안 된다 — 초록이 새어 나간다."""
    respx.get(freeindex.COLLINFO_URL).mock(return_value=httpx.Response(500))
    respx.get("https://example.com/").mock(return_value=httpx.Response(403, text="Just a moment"))
    respx.get("http://example.com/").mock(return_value=httpx.Response(403, text="Just a moment"))

    result = await freeindex.check("example.com", http)

    assert result.check.status is CheckStatus.UNCHECKED


def test_addresses_pointing_at_my_own_computer_are_refused():
    assert freeindex.host_is_reachable("https://example.com/") is True
    assert freeindex.host_is_reachable("http://127.0.0.1/") is False
    assert freeindex.host_is_reachable("http://169.254.169.254/latest/meta-data/") is False
    assert freeindex.host_is_reachable("http://192.168.0.1/") is False
    assert freeindex.host_is_reachable("http://[::1]/") is False
    assert freeindex.host_is_reachable("http://localhost:8765/") is False
    assert freeindex.host_is_reachable("http://printer.local/") is False


@respx.mock
async def test_a_redirect_into_the_home_network_is_not_followed(http):
    mock_collinfo()
    respx.get(url__startswith=CDX).mock(return_value=httpx.Response(200, text=crawl_lines("https://example.com/")))
    inner = respx.get("http://192.168.0.1/").mock(return_value=page("<title>공유기 설정</title>"))
    respx.get("https://example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "http://192.168.0.1/"})
    )
    respx.get("http://example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "http://192.168.0.1/"})
    )

    result = await freeindex.check("example.com", http)

    assert inner.call_count == 0  # 집 안 기기는 아예 찾아가지 않는다
    assert result.titles == []
    assert result.check.status is CheckStatus.OK  # 커먼크롤은 봤으니 판정은 가능


@respx.mock
async def test_the_same_address_twice_is_counted_once(http):
    mock_collinfo()
    respx.get(url__startswith=CDX).mock(
        return_value=httpx.Response(
            200, text=crawl_lines("https://example.com/a", "https://example.com/a", "https://example.com/b")
        )
    )
    respx.get("https://example.com/").mock(return_value=page("<title>안내</title>"))

    result = await freeindex.check("example.com", http)

    assert result.indexed_count == 2


@respx.mock
async def test_both_sources_failing_degrades_to_unchecked(http):
    respx.get(freeindex.COLLINFO_URL).mock(side_effect=httpx.ConnectError("no net"))
    respx.get("https://example.com/").mock(side_effect=httpx.ConnectError("no net"))
    respx.get("http://example.com/").mock(side_effect=httpx.ConnectError("no net"))

    result = await freeindex.check("example.com", http)

    assert result.check.status is CheckStatus.UNCHECKED
    assert "못 했습니다" in result.check.note


@respx.mock
async def test_a_dead_domain_still_produces_an_answer_from_the_crawl_index(http):
    """만료 도메인은 페이지가 안 열리는 게 정상 — 그래도 판정은 나와야 한다."""
    mock_collinfo()
    respx.get(url__startswith=CDX).mock(
        return_value=httpx.Response(200, text=crawl_lines(*[f"https://gone.com/{i}" for i in range(12)]))
    )
    respx.get("https://gone.com/").mock(side_effect=httpx.ConnectError("dead"))
    respx.get("http://gone.com/").mock(side_effect=httpx.ConnectError("dead"))

    result = await freeindex.check("gone.com", http)

    assert result.check.status is CheckStatus.OK
    assert result.indexed_count == 12
    assert "열리지 않음" in result.check.note


def test_parking_host_matches_subdomains_only_of_real_sellers():
    assert freeindex.parking_host("https://sedo.com/x") == "sedo.com"
    assert freeindex.parking_host("https://www.afternic.com/") == "afternic.com"
    assert freeindex.parking_host("https://shop.dan.com/") == "dan.com"
    assert freeindex.parking_host("https://notsedo.com/") == ""
    assert freeindex.parking_host("https://example.com/sedo.com") == ""
