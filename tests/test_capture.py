from pathlib import Path

import domainchecker.capture as capture_module
from domainchecker.capture import (
    TOOLBAR_CSS,
    capture_domain,
    plan_targets,
    playback_url,
)
from domainchecker.models import CheckStatus, Snapshot


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


async def test_capture_writes_a_file_and_hides_the_toolbar(sample_result, tmp_path, monkeypatch):
    calls = {"style": [], "goto": [], "shots": []}

    class FakePage:
        def set_default_timeout(self, ms):
            calls["timeout"] = ms

        async def goto(self, url, timeout=None, wait_until=None):
            calls["goto"].append((url, timeout))

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
            return FakeBrowser()

    class FakeDriver:
        chromium = FakeChromium()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    fake_module = type("M", (), {"async_playwright": lambda: FakeDriver()})
    monkeypatch.setitem(__import__("sys").modules, "playwright.async_api", fake_module)

    captures = await capture_domain(sample_result, tmp_path)

    assert captures.check.status is CheckStatus.OK
    assert len(captures.items) == 1
    assert captures.items[0].file.startswith("captures/")
    assert (tmp_path / "captures").exists()
    assert calls["goto"][0][1] == capture_module.TIMEOUT_MS  # 20초 제한
    assert calls["style"] == [TOOLBAR_CSS]
    assert calls["closed"] is True
