"""색인 오염 판정 — 항목별로 따로 본다(심사 A1 재현 회귀)."""

import httpx
import pytest
import respx

from domainchecker.clients import serper
from domainchecker.models import CheckStatus

URL = "https://google.serper.dev/search"


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def payload(*items, total="10"):
    return {"searchInformation": {"totalResults": total}, "organic": list(items)}


def item(title, snippet=""):
    return {"title": title, "snippet": snippet}


@respx.mock
async def test_parking_item_does_not_hide_contamination_in_other_items(http):
    """파킹 한 줄이 나머지 줄의 위험 업종 문구를 덮어 버리면 오염 도메인을 놓친다."""
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=payload(
                item("example.com is for sale", "buy this domain — Sedo 에서 판매 중"),
                item("온라인 카지노 가입 코드", "바카라 먹튀 없는 곳 안내"),
            ),
        )
    )
    result = await serper.check("example.com", "key", http)

    assert result.check.status is CheckStatus.OK
    assert result.current_parking is True  # 파킹 항목이 하나라도 있으면 True
    assert result.contaminated is True  # 비파킹 항목에서 오염어가 나왔다
    assert "카지노" in result.contamination_terms
    assert "바카라" in result.contamination_terms


@respx.mock
async def test_contamination_inside_the_parking_ad_alone_is_not_counted(http):
    """파킹 광고 문구 자체는 과거 이력이 아니다 — 그 항목만 있으면 오염이 아니다."""
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=payload(item("이 도메인 판매 중", "카지노 관련 도메인 판매합니다")),
        )
    )
    result = await serper.check("example.com", "key", http)

    assert result.current_parking is True
    assert result.contaminated is False
    assert result.contamination_terms == []


@respx.mock
async def test_sedona_and_parking_lot_are_not_parking(http):
    """부분 문자열로 세면 Sedona가 sedo로, parking lot이 parking으로 잘못 잡힌다."""
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=payload(
                item("Sedona 여행기", "parking lot 위치와 요금 안내"),
                item("Sedona 숙소 후기", "주차장 이용 방법"),
            ),
        )
    )
    result = await serper.check("example.com", "key", http)

    assert result.current_parking is False
    assert result.contaminated is False


@respx.mock
async def test_contamination_in_a_sedona_item_is_still_counted(http):
    """오탐으로 파킹 취급되면 그 항목의 오염어까지 통째로 빠져 버린다."""
    respx.post(URL).mock(
        return_value=httpx.Response(
            200,
            json=payload(item("Sedona parking lot", "온라인 카지노 후기 모음")),
        )
    )
    result = await serper.check("example.com", "key", http)

    assert result.current_parking is False
    assert result.contaminated is True
    assert result.contamination_terms == ["카지노"]


@respx.mock
async def test_clean_index_is_neither_parked_nor_contaminated(http):
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json=payload(item("동네 빵집 이야기", "오늘 구운 통밀빵 기록"), total="24")
        )
    )
    result = await serper.check("example.com", "key", http)

    assert result.indexed_count == 24
    assert result.current_parking is False
    assert result.contaminated is False
    assert result.titles == ["동네 빵집 이야기"]


def test_parking_matching_is_word_bounded():
    assert serper.is_parking_text("this domain is for sale") is True
    assert serper.is_parking_text("Parked domain name") is True
    assert serper.is_parking_text("listed on sedo") is True
    assert serper.is_parking_text("도메인 판매 안내") is True
    # 오탐 원인이던 부분 문자열들
    assert serper.is_parking_text("Sedona trip report") is False
    assert serper.is_parking_text("parking lot fees") is False
    assert serper.is_parking_text("sedona parking lot") is False


async def test_missing_key_is_not_run(http):
    result = await serper.check("example.com", "", http)
    assert result.check.status is CheckStatus.NOT_RUN
    assert "구글 색인은 못 봤습니다" in result.check.note
