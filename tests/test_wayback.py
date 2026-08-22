import httpx
import pytest
import respx

from domainchecker.clients.wayback import (
    CDX_URL,
    WaybackClient,
    cluster_key,
    cluster_representatives,
)
from domainchecker.models import CheckStatus, Snapshot
from domainchecker.ratelimit import AdaptiveRateLimiter

HEADER = ["timestamp", "original", "statuscode", "mimetype", "digest"]


def rows(entries):
    return [HEADER, *entries]


def test_era_representatives_keep_first_middle_last_of_each_year():
    """한 해에 판이 많아도 처음·중간·끝 세 장이면 그 해의 정체 변화가 보인다."""
    from domainchecker.clients.wayback import era_representatives

    stamps = [f"2016{m:02d}01000000" for m in range(1, 13)] + ["20170301000000"]
    versions = [
        Snapshot(timestamp=s, original="http://x.com/", status_code="200") for s in stamps
    ]
    reps = era_representatives(versions)

    assert [s.timestamp[:8] for s in reps] == ["20160101", "20160701", "20161201", "20170301"]


def test_cluster_key_folds_numbered_pages_into_one_kind():
    """같은 틀로 찍은 /post/1042 와 /post/93 은 한 유형으로 묶인다 — 숫자만 다른 주소."""
    assert cluster_key("/post/1042") == cluster_key("/post/93")
    assert cluster_key("/post/1042") != cluster_key("/casino/join")
    assert cluster_key("/view?id=93") == cluster_key("/view?id=12")  # 물음표 뒤는 모양에서 뺀다


def test_cluster_representatives_cover_every_kind_and_the_revival_year():
    """유형마다 처음·마지막 해 + 공백 직후 해 대표가 반드시 뽑힌다 — 종류 단위 전수."""
    def snap(year, path):
        return Snapshot(timestamp=f"{year}0601000000", original=f"http://x.com{path}", status_code="200")

    subpages = [
        snap(2010, "/blog/1"), snap(2015, "/blog/2"), snap(2020, "/blog/3"),
        snap(2015, "/casino/join"),
    ]
    reps, kinds = cluster_representatives(subpages, gap_years=[2014])  # 2015 = 부활 해

    assert kinds == 2  # /blog/N 과 /casino/join
    picked = {(s.year, s.original.replace("http://x.com", "")) for s in reps}
    assert (2010, "/blog/1") in picked  # blog 유형 처음
    assert (2020, "/blog/3") in picked  # blog 유형 마지막
    assert (2015, "/blog/2") in picked  # 공백 직후 해
    assert (2015, "/casino/join") in picked  # casino 유형은 하나뿐이라 그 자체가 대표


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def fast_limiter():
    return AdaptiveRateLimiter(rpm=6000)


def mock_cdx(stats, versions, paths):
    """CDX 조회 3종(통계·변경본·주소)을 물음표 뒤 내용으로 구분해 흉내 낸다."""

    def respond(request):
        collapse = request.url.params.get("collapse", "")
        if collapse == "digest":
            return versions if isinstance(versions, httpx.Response) else httpx.Response(200, json=versions)
        if collapse == "urlkey":
            return paths if isinstance(paths, httpx.Response) else httpx.Response(200, json=paths)
        return stats if isinstance(stats, httpx.Response) else httpx.Response(200, json=stats)

    respx.get(url__startswith="https://web.archive.org/cdx").mock(side_effect=respond)


@respx.mock
async def test_collect_reads_every_distinct_front_page_version(http):
    mock_cdx(
        stats=rows(
            [
                ["20180101000000", "http://x.com/", "200", "text/html", "A"],
                ["20200101000000", "http://x.com/", "200", "text/html", "B"],
            ]
        ),
        versions=rows(
            [
                ["20180101000000", "http://x.com/", "200", "text/html", "A"],
                ["20190601000000", "http://x.com/", "200", "text/html", "B"],
                ["20200301000000", "http://x.com/", "200", "text/html", "C"],
            ]
        ),
        paths=rows(
            [
                ["20180101000000", "http://x.com/blog/hello", "200", "text/html", "A"],
                ["20190601000000", "http://x.com/%EC%B9%B4%EC%A7%80%EB%85%B8/join", "200", "text/html", "B"],
            ]
        ),
    )
    respx.get(url__regex=r"https://web\.archive\.org/web/\d+id_/.*").mock(
        return_value=httpx.Response(200, text="<html><body>본문</body></html>")
    )

    history = await WaybackClient(http, fast_limiter()).collect("x.com")

    assert history.check.status is CheckStatus.OK
    # 앞페이지 변경본 3장은 전부 본문을 읽고, 하위 주소 2개는 목록으로 전부 훑는다.
    assert history.versions_total == 3
    assert history.versions_read == 3
    assert len(history.pages) == 3
    assert len(history.subpages) == 2  # 정독 후보 — 본문은 AI가 고른 뒤에 읽는다
    assert history.path_samples == ["2018 /blog/hello", "2019 /카지노/join"]  # 한글 풀림
    assert "변경본 3장" in history.coverage_note
    assert "전부 읽음" in history.coverage_note
    assert "하위 주소 2개" in history.coverage_note


@respx.mock
async def test_timeline_counts_how_many_different_contents_there_were(http):
    """1단은 목록 조회 한 번으로 '서로 다른 내용이 몇 가지였나'까지 뽑는다.

    달마다 저장은 됐는데 내용이 한 가지뿐이면 여러 해 동안 빈 화면만 걸려 있던 것이다.
    """
    respx.get(url__startswith="https://web.archive.org/cdx").mock(
        return_value=httpx.Response(
            200,
            json=rows(
                [
                    ["20180101000000", "http://x.com/", "200", "text/html", "SAME"],
                    ["20190101000000", "http://x.com/", "200", "text/html", "SAME"],
                    ["20200101000000", "http://x.com/", "200", "text/html", "OTHER"],
                ]
            ),
        )
    )

    history = await WaybackClient(http, fast_limiter()).timeline("x.com")

    assert history.total_captures == 3
    assert history.unique_digests == 2


@respx.mock
async def test_reading_stops_where_the_machine_veto_lands(http):
    """제외가 확정된 뒤의 옛 화면 받기는 판정을 못 바꾼다 — 그 자리에서 멈춘다."""
    snaps = [
        Snapshot(timestamp=f"20{y}0101000000", original="http://x.com/p", status_code="200")
        for y in range(10, 20)
    ]
    route = respx.get(url__regex=r"https://web\.archive\.org/web/\d+id_/.*").mock(
        return_value=httpx.Response(200, text="<html><body>본문</body></html>")
    )
    stop = {"after": 3}

    def should_stop():
        return route.call_count >= stop["after"]

    pages = await WaybackClient(http, fast_limiter()).read_subpages(snaps, should_stop=should_stop)

    assert len(pages) == 3
    assert route.call_count == 3


@respx.mock
async def test_version_query_failure_is_reported_not_hidden(http):
    """변경본 목록을 못 받으면 '확인함'이라 말하지 않는다 — 전수 약속이 깨진 것이다."""

    def respond(request):
        if request.url.params.get("collapse", "") == "digest":
            return httpx.Response(500)
        return httpx.Response(
            200,
            json=rows([["20180101000000", "http://x.com/", "200", "text/html", "A"]]),
        )

    respx.get(url__startswith="https://web.archive.org/cdx").mock(side_effect=respond)

    history = await WaybackClient(http, fast_limiter()).collect("x.com")

    assert history.check.status is CheckStatus.UNCHECKED
    assert "전수 확인" in history.check.note


@respx.mock
async def test_unreadable_versions_are_counted_in_coverage(http):
    mock_cdx(
        stats=rows([["20180101000000", "http://x.com/", "200", "text/html", "A"]]),
        versions=rows(
            [
                ["20180101000000", "http://x.com/", "200", "text/html", "A"],
                ["20190101000000", "http://x.com/", "200", "text/html", "B"],
            ]
        ),
        paths=rows([]),
    )
    calls = iter([httpx.Response(200, text="<html>본문</html>"), httpx.Response(404)])
    respx.get(url__regex=r"https://web\.archive\.org/web/\d+id_/.*").mock(
        side_effect=lambda request: next(calls)
    )

    history = await WaybackClient(http, fast_limiter()).collect("x.com")

    assert history.versions_total == 2
    assert history.versions_read == 1
    assert "1장은 열리지 않음" in history.coverage_note


@respx.mock
async def test_empty_cdx_means_no_history(http):
    respx.get(url__startswith="https://web.archive.org/cdx").mock(
        return_value=httpx.Response(200, json=[])
    )
    history = await WaybackClient(http, fast_limiter()).timeline("x.com")

    assert history.check.status is CheckStatus.OK
    assert history.has_history is False
    assert history.excluded is False


@respx.mock
async def test_exclusion_is_distinguished_from_no_history(http):
    respx.get(url__startswith="https://web.archive.org/cdx").mock(
        return_value=httpx.Response(403, text="Blocked Site Error")
    )
    history = await WaybackClient(http, fast_limiter()).timeline("x.com")

    assert history.excluded is True
    assert history.check.status is CheckStatus.UNCHECKED
    assert "차단" in history.check.note


@respx.mock
async def test_429_triggers_rate_drop_and_retry(http):
    route = respx.get(url__startswith="https://web.archive.org/cdx").mock(
        side_effect=[
            httpx.Response(429, text="too many"),
            httpx.Response(
                200,
                json=rows(
                    [
                        ["20180101000000", "http://x.com/", "200", "text/html", "A"],
                        ["20200101000000", "http://x.com/", "301", "text/html", "B"],
                    ]
                ),
            ),
        ]
    )
    limiter = fast_limiter()
    history = await WaybackClient(http, limiter).timeline("x.com")

    assert route.call_count == 2
    assert limiter.rpm == 12  # 429 한 번에 분당 12건으로 하향
    assert history.check.status is CheckStatus.OK
    assert history.total_captures == 2
    assert history.gap_years == [2019]
    assert history.redirect_ratio == 0.5


@pytest.mark.asyncio
@respx.mock
async def test_503_gets_retried_before_giving_up():
    """웨이백이 바쁠 때 던지는 503 한 방에 검사를 접으면 안 된다 — 쉬었다 다시 두드린다."""
    route = respx.get(CDX_URL)
    route.side_effect = [
        httpx.Response(503),
        httpx.Response(200, json=[["timestamp", "original", "statuscode", "mimetype", "digest"],
                                  ["20200101000000", "http://x.com/", "200", "text/html", "AA"]]),
    ]
    async with httpx.AsyncClient() as http:
        history = await WaybackClient(http, fast_limiter()).timeline("x.com")
    assert history.check.ok
    assert history.total_captures == 1

