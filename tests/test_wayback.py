import httpx
import pytest
import respx

from domainchecker.clients.wayback import WaybackClient, path_samples, select_snapshots
from domainchecker.models import CheckStatus, Snapshot
from domainchecker.ratelimit import AdaptiveRateLimiter


def snapshots_for(years, url="http://x.com/"):
    return [
        Snapshot(timestamp=f"{y}0601120000", original=url, status_code="200")
        for y in years
    ]


def test_selection_always_keeps_the_last_two_years():
    picked = select_snapshots(snapshots_for(range(2005, 2021)), 6)
    years = [s.year for s in picked]

    assert len(picked) == 6
    assert 2019 in years and 2020 in years  # 말기 2년 필수
    assert years == sorted(years)


def test_selection_handles_short_history():
    assert len(select_snapshots(snapshots_for([2019]), 6)) == 1
    assert len(select_snapshots(snapshots_for([2018, 2019]), 6)) == 2
    assert select_snapshots([], 6) == []


def test_selection_prioritises_the_year_right_after_a_gap():
    """죽었다 되살아난 해는 스팸 업자가 주워 쓰기 시작한 해일 가능성이 가장 높다."""
    # 2005~2010 운영 → 2011~2014 공백 → 2015 부활 → … → 2020 말기
    picked = select_snapshots(snapshots_for([2005, 2006, 2007, 2008, 2009, 2010, 2015, 2019, 2020]), 4)
    years = [s.year for s in picked]

    assert 2015 in years  # 공백 직후 해가 반드시 들어간다
    assert 2019 in years and 2020 in years


def test_path_samples_keep_year_and_decoded_path_without_the_domain_name():
    captures = (
        snapshots_for([2012], url="http://x.com/blog/hello")
        + snapshots_for([2016], url="http://x.com/%EC%B9%B4%EC%A7%80%EB%85%B8/join")
        + snapshots_for([2016], url="http://x.com/")  # 뿌리 주소는 흔적이 아니다
    )
    samples = path_samples(captures)

    assert samples == ["2012 /blog/hello", "2016 /카지노/join"]
    assert all("x.com" not in s for s in samples)


def test_path_samples_cap_per_year():
    captures = [
        Snapshot(timestamp=f"2016{m:02d}01000000", original=f"http://x.com/page-{m}", status_code="200")
        for m in range(1, 13)
    ]
    samples = path_samples(captures, per_year=5)
    assert len(samples) == 5  # 한 해 표본은 5개까지만


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def fast_limiter():
    return AdaptiveRateLimiter(rpm=6000)


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
                json=[
                    ["timestamp", "original", "statuscode", "mimetype", "digest"],
                    ["20180101000000", "http://x.com/", "200", "text/html", "A"],
                    ["20200101000000", "http://x.com/", "301", "text/html", "B"],
                ],
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
