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
    """시험용 속도 조절기 — 진짜로 기다리지 않는다.

    웨이백이 503·429를 던지면 실제 앱은 두 가지로 물러선다: 3초→6초→12초→24초로
    쉬고(backoff), 분당 30건이던 속도를 12건까지 떨어뜨린다(그러면 한 번 두드릴
    때마다 5초를 기다린다). 시험에서 그 둘을 그대로 두면 실패를 다루는 시험 다섯 개가
    3분을 자고 있었다(실측 183초 중 173초).

    물러서는 계산이 맞는지는 `test_ratelimit.py` 가 가짜 시계로 따로 본다. 여기서 볼
    것은 "쉬었다 다시 두드려서 결국 받아 오는가"뿐이므로, 기다리는 값만 전부 0으로
    둔다 — 물러섰다는 사실 자체는 `degraded` 표시로 그대로 확인할 수 있다.
    """
    return AdaptiveRateLimiter(
        rpm=6000,
        degraded_rpm=6000,
        floor_rpm=6000,
        recover_rpm=6000,
        backoff_base=0.001,
        backoff_max=0.001,
    )


async def full_read(client, domain="x.com"):
    """파이프라인이 실제로 하는 순서 그대로 — 3단(목록 한 번) 다음 4단(전수 정독).

    예전에는 이 둘을 붙여 둔 편의 함수(`collect`)를 시험했는데, 프로덕션은 깔때기라
    둘을 따로 부른다(3단에서 걸러진 도메인은 4단까지 안 간다). 편의 함수를 시험하면
    정작 실제로 도는 길은 아무도 안 보게 되므로, 여기서 그 길을 그대로 밟는다.
    """
    history = await client.timeline(domain)
    if not history.check.ok or not history.has_history:
        return history
    return await client.deep_read(domain, history)


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
async def test_deep_read_reads_every_distinct_front_page_version(http):
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

    history = await full_read(WaybackClient(http, fast_limiter()))

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
async def test_a_page_already_fetched_is_never_fetched_twice(http):
    """3단이 받아 둔 최신 화면 한 장을 4단이 또 받으면 도메인마다 요청 하나가 버려진다.

    받아 둔 본문 장부(부채 대장 1번 상환)가 그 낭비를 없앤다 — 읽는 내용은 그대로이고
    웨이백을 두드리는 횟수만 줄어든다.
    """
    mock_cdx(
        stats=rows([["20200101000000", "http://x.com/", "200", "text/html", "A"]]),
        versions=rows([["20200101000000", "http://x.com/", "200", "text/html", "A"]]),
        paths=rows([]),
    )
    route = respx.get(url__regex=r"https://web\.archive\.org/web/\d+id_/.*").mock(
        return_value=httpx.Response(200, text="<html><body>본문</body></html>")
    )

    client = WaybackClient(http, fast_limiter())
    history = await client.timeline("x.com")
    # 3단 — 가장 최근 화면 한 장만 받아 본다
    assert await client.fetch_snapshot(history.latest) is not None
    assert route.call_count == 1
    # 4단 — 같은 장이 변경본 대표로 다시 나오지만 웨이백을 또 두드리지 않는다
    history = await client.deep_read("x.com", history)

    assert route.call_count == 1  # 두 번째 받기는 없다
    assert history.versions_read == 1  # 그래도 읽은 내용은 그대로다
    assert history.pages[0]["fetched"] is True
    assert "본문" in history.pages[0]["html"]


@respx.mock
async def test_a_failed_fetch_is_not_remembered_as_an_answer(http):
    """웨이백은 바쁜 날 같은 주소를 몇 초 뒤에 열어 준다 — 실패를 장부에 적으면 안 된다."""
    snap = Snapshot(timestamp="20200101000000", original="http://x.com/", digest="A")
    calls = iter([httpx.Response(503), httpx.Response(503), httpx.Response(503),
                  httpx.Response(503), httpx.Response(503),
                  httpx.Response(200, text="<html>본문</html>")])
    respx.get(url__regex=r"https://web\.archive\.org/web/\d+id_/.*").mock(
        side_effect=lambda request: next(calls)
    )

    client = WaybackClient(http, fast_limiter())
    assert await client.fetch_snapshot(snap) is None  # 다섯 번 두드려도 안 열림
    assert await client.fetch_snapshot(snap) == "<html>본문</html>"  # 다시 물어보면 열린다


@respx.mock
async def test_timeline_counts_how_many_different_contents_there_were(http):
    """3단은 목록 조회 한 번으로 '서로 다른 내용이 몇 가지였나'까지 뽑는다.

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
async def test_latest_screen_comes_from_the_front_page_not_the_truncated_list(http):
    """3단이 보는 '가장 최근 화면'은 잘린 목록의 마지막이 아니라 앞페이지의 최신본이다.

    주소 목록 조회는 사전순으로 앞에서 2000행만 잘라 주므로, 주소가 많은 도메인은
    진짜 최신 저장분이 통째로 빠진다. 그 잘린 목록에서 고른 장으로 탈락을 정하면
    이력이 두꺼운(=살 가치가 큰) 도메인일수록 엉뚱하게 떨어진다.
    """
    truncated = rows([["20200101000000", "http://x.com/a", "200", "text/html", "OLD"]])
    real_latest = rows([["20260820221136", "http://x.com/", "200", "text/html", "NEW"]])

    def respond(request):
        # 앞페이지를 정확히 물으면서 뒤에서부터 세는 조회(limit 음수)만 진짜 최신을 준다
        if request.url.params.get("limit", "").startswith("-"):
            return httpx.Response(200, json=real_latest)
        return httpx.Response(200, json=truncated)

    respx.get(url__startswith="https://web.archive.org/cdx").mock(side_effect=respond)

    history = await WaybackClient(http, fast_limiter()).timeline("x.com")

    assert history.latest is not None
    assert history.latest.timestamp == "20260820221136"


@respx.mock
async def test_latest_screen_falls_back_when_the_front_page_query_fails(http):
    """앞페이지 조회가 실패해도 '최근 화면'이 통째로 없어지면 안 된다 — 있는 것으로 대신한다."""

    def respond(request):
        if request.url.params.get("limit", "").startswith("-"):
            return httpx.Response(503)
        return httpx.Response(
            200, json=rows([["20200101000000", "http://x.com/a", "200", "text/html", "OLD"]])
        )

    respx.get(url__startswith="https://web.archive.org/cdx").mock(side_effect=respond)

    history = await WaybackClient(http, fast_limiter()).timeline("x.com")

    assert history.latest is not None
    assert history.latest.timestamp == "20200101000000"


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

    history = await full_read(WaybackClient(http, fast_limiter()))

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

    history = await full_read(WaybackClient(http, fast_limiter()))

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
            # 목록 조회 뒤에 앞페이지 최신본을 한 번 더 묻는다(가장 최근 화면 확정용)
            httpx.Response(
                200,
                json=rows([["20200101000000", "http://x.com/", "200", "text/html", "B"]]),
            ),
        ]
    )
    limiter = fast_limiter()
    history = await WaybackClient(http, limiter).timeline("x.com")

    assert route.call_count == 3
    # 429를 받고 속도를 낮췄다는 사실 — 얼마나 낮추는지(분당 12건)의 계산은
    # `test_ratelimit.py` 가 가짜 시계로 따로 본다(여기서 재면 시험이 5초를 잔다).
    assert limiter.degraded is True
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
        # 앞페이지 최신본을 확정하는 두 번째 조회
        httpx.Response(200, json=[["timestamp", "original", "statuscode", "mimetype", "digest"],
                                  ["20200101000000", "http://x.com/", "200", "text/html", "AA"]]),
    ]
    async with httpx.AsyncClient() as http:
        history = await WaybackClient(http, fast_limiter()).timeline("x.com")
    assert history.check.ok
    assert history.total_captures == 1

