"""키 없이 도는 권위 점수 — Tranco 인기 도메인 목록."""

import io
import time
import zipfile

import httpx
import pytest
import respx

from domainchecker.clients import tranco
from domainchecker.models import CheckStatus


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def make_zip(rows: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("top-1m.csv", rows)
    return buffer.getvalue()


LIST = make_zip("1,google.com\n460,ietf.org\n2003,rfc-editor.org\n")


def test_rank_becomes_a_score_between_zero_and_ten():
    assert tranco.rank_to_score(1) == 10.0
    assert tranco.rank_to_score(1_000_000) == 0.0
    assert 5.0 < tranco.rank_to_score(460) < 6.5
    assert tranco.rank_to_score(0) == 0.0
    assert tranco.rank_to_score(5_000_000) == 0.0  # 목록 밖 숫자도 음수로 안 내려간다


@respx.mock
async def test_listed_domain_gets_a_score_and_an_absent_one_gets_no_data(http, tmp_path):
    respx.get(tranco.LIST_URL).mock(return_value=httpx.Response(200, content=LIST))

    found = await tranco.fetch_batch(["ietf.org", "nowhere.example"], http, tmp_path)

    listed = found["ietf.org"]
    assert listed.check.status is CheckStatus.OK
    assert listed.rank == 460
    assert listed.has_data is True
    assert listed.page_rank > 0

    absent = found["nowhere.example"]
    assert absent.check.status is CheckStatus.OK  # 자료가 없는 것도 정상 답이다
    assert absent.has_data is False
    assert absent.page_rank == 0.0
    assert "권위 자료 없음" in absent.check.note


@respx.mock
async def test_the_list_is_downloaded_once_and_reused_from_disk(http, tmp_path):
    route = respx.get(tranco.LIST_URL).mock(return_value=httpx.Response(200, content=LIST))

    await tranco.fetch_batch(["ietf.org"], http, tmp_path)
    await tranco.fetch_batch(["rfc-editor.org"], http, tmp_path)

    assert route.call_count == 1
    assert (tmp_path / tranco.CACHE_NAME).exists()


@respx.mock
async def test_a_stale_saved_list_is_used_when_the_download_fails(http, tmp_path):
    path = tmp_path / tranco.CACHE_NAME
    path.write_bytes(LIST)
    old = time.time() - (tranco.MAX_AGE_SECONDS + 60)
    import os

    os.utime(path, (old, old))
    respx.get(tranco.LIST_URL).mock(side_effect=httpx.ConnectError("no net"))

    found = await tranco.fetch_batch(["ietf.org"], http, tmp_path)

    assert found["ietf.org"].check.status is CheckStatus.OK
    assert "예전에 저장해 둔" in found["ietf.org"].check.note


@respx.mock
async def test_no_list_at_all_degrades_to_unchecked(http, tmp_path):
    respx.get(tranco.LIST_URL).mock(side_effect=httpx.ConnectError("no net"))

    found = await tranco.fetch_batch(["ietf.org"], http, tmp_path)

    assert found["ietf.org"].check.status is CheckStatus.UNCHECKED
    assert "받지 못했습니다" in found["ietf.org"].check.note


@respx.mock
async def test_a_damaged_list_file_is_thrown_away_so_the_next_run_refetches(http, tmp_path):
    path = tmp_path / tranco.CACHE_NAME
    path.write_bytes(b"not a zip at all")
    respx.get(tranco.LIST_URL).mock(return_value=httpx.Response(500))

    found = await tranco.fetch_batch(["ietf.org"], http, tmp_path)

    assert found["ietf.org"].check.status is CheckStatus.UNCHECKED
    assert not path.exists()


@respx.mock
async def test_www_prefix_and_capital_letters_still_match(http, tmp_path):
    respx.get(tranco.LIST_URL).mock(return_value=httpx.Response(200, content=LIST))

    found = await tranco.fetch_batch(["WWW.Ietf.org"], http, tmp_path)

    assert found["WWW.Ietf.org"].rank == 460
