import asyncio
import re
import sys
from pathlib import Path

import pytest

import domainchecker.capture as capture_module
from domainchecker.capture import (
    TOOLBAR_CSS,
    capture_domain,
    plan_targets,
    playback_url,
)
from domainchecker.models import CheckStatus, Snapshot
from domainchecker.ratelimit import AdaptiveRateLimiter


def test_playback_url_is_not_the_raw_id_url():
    url = playback_url("20240601000000", "http://example.com/")
    assert url == "https://web.archive.org/web/20240601000000/http://example.com/"
    assert "id_" not in url


def test_toolbar_css_hides_the_archive_bar():
    assert "#wm-ipp-base" in TOOLBAR_CSS and "display: none !important" in TOOLBAR_CSS


def test_plans_the_terminal_shot_only_when_there_is_no_risk(sample_result):
    targets = plan_targets(sample_result)
    assert len(targets) == 1
    assert targets[0][0] == "말기"
    assert targets[0][1] == "20240601000000"  # 마지막 스냅샷


def test_plans_one_shot_per_topic_period(sample_result):
    """주제가 바뀐 시기마다 그 시기의 화면 사진을 한 장씩 찍는다."""
    sample_result.wayback.selected.insert(
        1, Snapshot(timestamp="20150601000000", original="http://example.com/")
    )
    sample_result.ai.topic_periods = [
        {"start": "2010", "end": "2014", "topic": "빵집 블로그"},
        {"start": "2015", "end": "2024", "topic": "베이킹 강좌"},
    ]
    targets = plan_targets(sample_result)

    labels = [t[0] for t in targets]
    assert labels[0] == "말기"
    assert "빵집 블로그 (2010~2014)" in labels
    # 말기(2024)와 같은 스냅샷이 걸리는 시기는 두 번 찍지 않는다
    timestamps = [t[1] for t in targets]
    assert len(timestamps) == len(set(timestamps))


def test_plans_a_second_shot_for_the_risk_period(sample_result):
    sample_result.wayback.selected.insert(
        1, Snapshot(timestamp="20150601000000", original="http://example.com/")
    )
    sample_result.rules.risk_timestamps = ["20150601000000"]
    targets = plan_targets(sample_result)

    assert [t[0] for t in targets] == ["말기", "위험 신호 시기"]
    assert targets[1][1] == "20150601000000"


async def test_disabled_capture_is_not_run(sample_result, tmp_path):
    captures = await capture_domain(sample_result, tmp_path, enabled=False)
    assert captures.check.status is CheckStatus.NOT_RUN
    assert captures.items == []


async def test_no_snapshot_means_not_run(sample_result, tmp_path):
    sample_result.wayback.selected = []
    captures = await capture_domain(sample_result, tmp_path)
    assert captures.check.status is CheckStatus.NOT_RUN
    assert "캡쳐할 과거 스냅샷이 없습니다" in captures.check.note


async def test_missing_browser_degrades_to_not_run(sample_result, tmp_path, monkeypatch):
    """브라우저가 없어도 파이프라인은 계속되어야 한다."""

    class FakeChromium:
        async def launch(self, **kwargs):
            raise RuntimeError("Executable doesn't exist")

    class FakeDriver:
        chromium = FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    fake_module = type("M", (), {"async_playwright": lambda: FakeDriver()})
    monkeypatch.setitem(__import__("sys").modules, "playwright.async_api", fake_module)

    captures = await capture_domain(sample_result, tmp_path)
    assert captures.check.status is CheckStatus.NOT_RUN
    assert "playwright install chromium" in captures.check.note


def install_fake_playwright(monkeypatch, *, blank_timestamps=(), launch_error=None):
    """가짜 브라우저를 끼워 넣는다. blank_timestamps 에 든 날짜는 빈 화면으로 나온다."""
    calls = {"style": [], "goto": [], "shots": [], "closed": False}

    class FakeFrame:
        def __init__(self, page):
            self.page = page

        async def evaluate(self, script):
            return self.page.has_content

    class FakePage:
        def __init__(self):
            self.has_content = True

        @property
        def frames(self):
            return [FakeFrame(self)]

        def set_default_timeout(self, ms):
            calls["timeout"] = ms

        async def goto(self, url, timeout=None, wait_until=None):
            calls["goto"].append((url, timeout))
            self.has_content = not any(ts in url for ts in blank_timestamps)

        async def wait_for_load_state(self, state, timeout=None):
            calls.setdefault("states", []).append(state)

        async def add_style_tag(self, content=""):
            calls["style"].append(content)

        async def screenshot(self, path=""):
            calls["shots"].append(path)
            Path(path).write_bytes(b"png")

    class FakeBrowser:
        async def new_page(self, **kwargs):
            return FakePage()

        async def close(self):
            calls["closed"] = True

    class FakeChromium:
        async def launch(self, **kwargs):
            if launch_error is not None:
                raise launch_error
            return FakeBrowser()

    class FakeDriver:
        chromium = FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    fake_module = type("M", (), {"async_playwright": lambda: FakeDriver()})
    monkeypatch.setitem(__import__("sys").modules, "playwright.async_api", fake_module)
    return calls


async def test_capture_writes_a_file_and_hides_the_toolbar(sample_result, tmp_path, monkeypatch):
    calls = install_fake_playwright(monkeypatch)

    captures = await capture_domain(sample_result, tmp_path)

    assert captures.check.status is CheckStatus.OK
    assert len(captures.items) == 1
    assert captures.items[0].file.startswith("captures/")
    assert (tmp_path / "captures").exists()
    assert calls["goto"][0][1] == capture_module.TIMEOUT_MS  # 20초 제한
    assert calls["style"] == [TOOLBAR_CSS]
    assert calls["closed"] is True


# ---------------------------------------------------------------------------
# 1) 자동 재시작 모드에서 브라우저가 아예 안 열리던 문제
# ---------------------------------------------------------------------------


def test_a_loop_that_cannot_start_other_programs_is_spotted():
    """윈도우 자동 재시작 모드가 골라 주는 일꾼은 브라우저를 못 띄운다 — 미리 알아내야 한다.

    uvicorn 은 reload 를 켜면 윈도우에서 SelectorEventLoop 를 쓰는데, 그 일꾼은
    다른 프로그램을 띄우는 기능이 아예 없어서 크로미움 실행이 NotImplementedError
    로 죽는다. 그걸 실행 전에 알아채는지 확인한다.
    """

    class LoopWithoutSubprocess(asyncio.BaseEventLoop):
        pass

    class LoopWithSubprocess(asyncio.BaseEventLoop):
        async def _make_subprocess_transport(self, *args, **kwargs):  # pragma: no cover
            return None

    assert capture_module._can_start_browser(LoopWithoutSubprocess()) is False
    assert capture_module._can_start_browser(LoopWithSubprocess()) is True


@pytest.mark.skipif(sys.platform != "win32", reason="윈도우에서만 있는 일꾼 조합")
def test_the_real_uvicorn_reload_loop_is_spotted():
    """진짜 uvicorn 이 reload 모드에서 만들어 주는 일꾼으로 확인한다."""
    from uvicorn.loops.asyncio import asyncio_loop_factory

    reload_loop = asyncio_loop_factory(use_subprocess=True)()
    plain_loop = asyncio_loop_factory(use_subprocess=False)()
    try:
        assert capture_module._can_start_browser(reload_loop) is False
        assert capture_module._can_start_browser(plain_loop) is True
    finally:
        reload_loop.close()
        plain_loop.close()


async def test_capture_still_works_on_a_loop_that_cannot_start_programs(
    sample_result, tmp_path, monkeypatch
):
    """브라우저를 못 띄우는 일꾼을 만나면 새 일꾼을 만들어서라도 찍어야 한다."""
    calls = install_fake_playwright(monkeypatch)
    monkeypatch.setattr(capture_module, "_can_start_browser", lambda loop: False)
    limiter = AdaptiveRateLimiter(rpm=6000)  # 시험이 느려지지 않게 아주 빠르게

    captures = await capture_domain(sample_result, tmp_path, limiter)

    assert captures.check.status is CheckStatus.OK
    assert len(captures.items) == 1
    assert calls["shots"], "다른 일꾼에서라도 사진을 찍어야 한다"


# ---------------------------------------------------------------------------
# 2) 빈 흰 화면이 정상 캡쳐인 척 저장되던 문제
# ---------------------------------------------------------------------------


async def test_blank_page_is_replaced_by_a_neighbouring_snapshot(
    sample_result, tmp_path, monkeypatch
):
    """고른 날짜가 빈 종이로 나오면 이웃 날짜로 바꿔 찍는다."""
    install_fake_playwright(monkeypatch, blank_timestamps=("20240601000000",))

    captures = await capture_domain(sample_result, tmp_path)

    assert captures.check.status is CheckStatus.OK
    assert len(captures.items) == 1
    # 빈 종이였던 2024년 대신 이웃한 2010년 화면이 저장돼야 한다
    assert captures.items[0].timestamp == "20100601000000"


async def test_all_blank_pages_are_not_saved_as_captures(sample_result, tmp_path, monkeypatch):
    """전부 빈 종이면 사진을 만들지 않고, 왜 없는지 쉬운 우리말로 알려 준다."""
    calls = install_fake_playwright(
        monkeypatch, blank_timestamps=("20240601000000", "20100601000000")
    )

    captures = await capture_domain(sample_result, tmp_path)

    assert captures.items == []
    assert calls["shots"] == [], "빈 종이는 파일로 저장하면 안 된다"
    assert "빈 종이" in captures.check.note


# ---------------------------------------------------------------------------
# 3) 영어 오류 이름이 사용자 화면에 그대로 나가던 문제
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        NotImplementedError(),
        RuntimeError("Executable doesn't exist at /ms-playwright/chromium"),
        TimeoutError("Timeout 20000ms exceeded."),
        Exception("net::ERR_NAME_NOT_RESOLVED at https://web.archive.org/"),
        ValueError("something odd"),
    ],
)
def test_failure_reasons_are_plain_korean_only(exc):
    reason = capture_module.failure_reason(exc)
    assert reason
    assert type(exc).__name__ not in reason
    assert not re.search(r"[A-Za-z]{4,}", reason), f"영어가 남아 있다: {reason}"


async def test_capture_notes_never_leak_english_exception_names(
    sample_result, tmp_path, monkeypatch
):
    """캡쳐가 통째로 실패해도 화면에는 영어 오류 이름이 안 보여야 한다."""
    install_fake_playwright(monkeypatch, launch_error=NotImplementedError())

    captures = await capture_domain(sample_result, tmp_path)

    assert "NotImplementedError" not in captures.check.note
    assert "크롬 브라우저가 없어" in captures.check.note


def test_when_label_shows_a_date_a_person_can_read():
    assert capture_module.when_label("20230525221137") == "2023년 5월"
    assert capture_module.when_label("20010000000000") == "2001년"


# ---------------------------------------------------------------------------
# 4) 대체 스냅샷 고르기
# ---------------------------------------------------------------------------


def test_alternates_prefer_the_same_topic_period(sample_result):
    """대신 찍을 날짜는 같은 주제 시기 안에서 먼저 고른다 — 엉뚱한 시대 사진을 막는다."""
    sample_result.wayback.selected = [
        Snapshot(timestamp="20100601000000", original="http://example.com/"),
        Snapshot(timestamp="20110601000000", original="http://example.com/"),
        Snapshot(timestamp="20230601000000", original="http://example.com/"),
        Snapshot(timestamp="20240601000000", original="http://example.com/"),
    ]
    sample_result.ai.topic_periods = [{"start": "2023", "end": "2024", "topic": "베이킹 강좌"}]

    alternates = capture_module.alternate_snapshots(sample_result, "20230601000000")

    assert alternates[0].timestamp == "20240601000000"  # 같은 시기 것이 먼저
