"""2단 — 공개 위험 명단. 실제 네트워크에는 나가지 않는다(가짜 서버로만 시험)."""

import os
import time

import httpx
import pytest
import respx
from conftest import blacklist_bytes, put_blacklist

from domainchecker.clients.offline_lists import OfflineLists, candidates

BASE = "https://lists.test/download"

# conftest 가 모든 시험에서 내려받기를 막아 둔다 — 이 파일만 진짜 내려받기를 되살려
# 가짜 서버(respx)로 받아 본다.
REAL_DOWNLOAD = OfflineLists._download


@pytest.fixture(autouse=True)
def _use_the_real_download(monkeypatch):
    monkeypatch.setattr(OfflineLists, "_download", REAL_DOWNLOAD)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def lists_for(tmp_path, *categories):
    return OfflineLists(tmp_path / "data", categories=categories, url_base=BASE)


def mock_download(category: str, domains: list[str]):
    return respx.get(f"{BASE}/{category}.tar.gz").mock(
        return_value=httpx.Response(200, content=blacklist_bytes(category, domains))
    )


def test_candidates_include_the_parent_domains():
    """명단이 위 도메인만 들고 있어도 잡혀야 한다 — 하위 이름은 얼마든지 만들 수 있다."""
    assert candidates("shop.example.com") == ["shop.example.com", "example.com"]
    assert candidates("www.example.com") == ["example.com"]
    assert candidates("example") == []


@respx.mock
async def test_downloaded_list_catches_a_listed_domain(tmp_path, http):
    mock_download("gambling", ["bad-casino.com", "other.net"])
    lists = lists_for(tmp_path, "gambling")

    await lists.refresh(http)
    hits = lists.screen(["bad-casino.com", "clean.com"])

    assert lists.available is True
    assert lists.path("gambling").exists()  # 데이터 폴더에 남는다
    assert "clean.com" not in hits
    assert "도박 명단(UT1 툴루즈" in hits["bad-casino.com"][0]


@respx.mock
async def test_a_second_run_uses_the_cache_instead_of_downloading_again(tmp_path, http):
    route = mock_download("warez", ["pirate.com"])
    lists = lists_for(tmp_path, "warez")

    await lists.refresh(http)
    await lists_for(tmp_path, "warez").refresh(http)

    assert route.call_count == 1  # 7일 안에는 다시 받지 않는다


@respx.mock
async def test_a_stale_cache_is_downloaded_again(tmp_path, http):
    route = mock_download("warez", ["pirate.com"])
    lists = lists_for(tmp_path, "warez")
    await lists.refresh(http)
    # 8일 전에 받은 것처럼 시계를 돌려 둔다
    old = time.time() - 8 * 86400
    os.utime(lists.path("warez"), (old, old))

    await lists_for(tmp_path, "warez").refresh(http)

    assert route.call_count == 2


@respx.mock
async def test_a_failed_download_keeps_using_the_list_we_already_have(tmp_path, http):
    """명단 서버가 죽어도 검사는 계속 돈다 — 받아 둔 명단으로 간다."""
    put_blacklist(tmp_path / "data", "phishing", ["fake-bank.com"])
    old = time.time() - 30 * 86400
    os.utime(OfflineLists(tmp_path / "data").path("phishing"), (old, old))
    respx.get(f"{BASE}/phishing.tar.gz").mock(return_value=httpx.Response(503))

    lists = lists_for(tmp_path, "phishing")
    await lists.refresh(http)

    assert lists.available is True
    assert lists.screen(["fake-bank.com"])


@respx.mock
async def test_no_list_at_all_just_skips_this_step(tmp_path, http):
    """한 장도 못 받으면 그 단만 건너뛴다 — 검사를 막지는 않는다."""
    respx.get(url__startswith=BASE).mock(side_effect=httpx.ConnectError("boom"))

    lists = lists_for(tmp_path, "gambling")
    await lists.refresh(http)

    assert lists.available is False
    assert lists.screen(["anything.com"]) == {}
    assert "건너뛰" in lists.note


@respx.mock
async def test_a_broken_bundle_is_never_kept_as_a_list(tmp_path, http):
    """반쪽으로 받힌 파일을 명단으로 믿으면 멀쩡한 도메인이 조용히 통과한다."""
    respx.get(f"{BASE}/malware.tar.gz").mock(
        return_value=httpx.Response(200, content=b"this is not a tar.gz")
    )

    lists = lists_for(tmp_path, "malware")
    await lists.refresh(http)

    assert lists.available is False
    assert not lists.path("malware").exists()
