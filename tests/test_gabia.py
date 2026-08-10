"""가비아 구매 가능 확인 — 검색창 답(JSON)을 취득 상태로 읽는 부분."""

import httpx
import respx

from domainchecker.analyze.scoring import availability_of
from domainchecker.clients import gabia
from domainchecker.models import CheckStatus


def test_available_domain_reads_as_free():
    reg = gabia.parse({"flag": "Y", "status": "10001", "result": "등록 가능"})
    assert reg.check.status is CheckStatus.OK
    assert reg.acquisition == "구매 가능(미등록)"
    assert availability_of(reg.acquisition) == "free"


def test_taken_domain_reads_as_taken():
    reg = gabia.parse({"flag": "A", "status": "10002", "result": "이미 등록된 도메인"})
    assert reg.check.status is CheckStatus.OK
    assert reg.acquisition == "등록 중"
    assert availability_of(reg.acquisition) == "taken"


def test_backorder_domain_reads_as_soon():
    reg = gabia.parse(
        {"flag": "A", "status": "BO", "result": "이미 등록된 도메인", "backorder_date": "2026-09-01"}
    )
    assert reg.acquisition == "삭제 대기(곧 등록 가능)"
    assert "2026-09-01" in reg.check.note
    assert availability_of(reg.acquisition) == "soon"


def test_reserved_word_domain_cannot_be_bought():
    """예약어(기관 전용)는 주인이 없어도 일반인은 못 산다 — free로 읽히면 안 된다."""
    reg = gabia.parse({"flag": "Y", "status": "PUBIC", "result": "예약어 도메인"})
    assert reg.acquisition == "등록 제한(예약어·기관 전용)"
    assert availability_of(reg.acquisition) == "taken"


def test_unknown_flag_degrades_to_unchecked():
    reg = gabia.parse({"flag": "Z", "status": "?", "result": ""})
    assert reg.check.status is CheckStatus.UNCHECKED


@respx.mock
async def test_check_calls_gabia_and_maps_the_answer():
    respx.post(gabia.CHECK_URL).mock(
        return_value=httpx.Response(
            200, json={"flag": "Y", "status": "10001", "result": "등록 가능"}
        )
    )
    reg = await gabia.check("bluekites.co.kr")
    assert reg.source == "gabia"
    assert reg.acquisition == "구매 가능(미등록)"
    body = respx.calls.last.request.content.decode()
    assert "domain=bluekites.co.kr" in body


@respx.mock
async def test_check_survives_a_server_error():
    respx.post(gabia.CHECK_URL).mock(return_value=httpx.Response(500))
    reg = await gabia.check("bluekites.co.kr")
    assert reg.check.status is CheckStatus.UNCHECKED


@respx.mock
async def test_check_survives_a_network_error():
    respx.post(gabia.CHECK_URL).mock(side_effect=httpx.ConnectError("boom"))
    reg = await gabia.check("bluekites.co.kr")
    assert reg.check.status is CheckStatus.UNCHECKED
    assert "접속 실패" in reg.check.note


@respx.mock
async def test_check_survives_a_non_json_answer():
    respx.post(gabia.CHECK_URL).mock(return_value=httpx.Response(200, text="<html>점검중</html>"))
    reg = await gabia.check("bluekites.co.kr")
    assert reg.check.status is CheckStatus.UNCHECKED
