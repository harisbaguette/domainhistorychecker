"""Playwright screenshots of archived pages — runs after every other check.

PLAN §1[F]: one shot of the terminal period (말기) and, when a risk signal was
found, one of that period. Screenshots share the global Wayback rate limiter,
and any failure (no browser installed, timeout) is recorded without stopping
the pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Capture, Captures, CheckState, CheckStatus, DomainResult
from .ratelimit import AdaptiveRateLimiter

TIMEOUT_MS = 20_000
VIEWPORT = {"width": 1280, "height": 800}
# The archive injects its own toolbar into playback pages; hide it.
TOOLBAR_CSS = "#wm-ipp-base, #wm-ipp, #donato { display: none !important; }"

_SAFE = re.compile(r"[^a-z0-9.\-]")


def playback_url(timestamp: str, original: str) -> str:
    """Normal playback URL (not `id_`) so the page renders with its assets."""
    return f"https://web.archive.org/web/{timestamp}/{original}"


def plan_targets(result: DomainResult, limit: int = 2) -> list[tuple[str, str, str]]:
    """Return (label, timestamp, url) for the shots worth taking."""
    selected = result.wayback.selected
    if not selected:
        return []
    targets: list[tuple[str, str, str]] = []
    last = selected[-1]
    targets.append(("말기", last.timestamp, playback_url(last.timestamp, last.original)))

    risky = set(result.rules.risk_timestamps)
    for snapshot in selected:
        if snapshot.timestamp in risky and snapshot.timestamp != last.timestamp:
            targets.append(
                (
                    "위험 신호 시기",
                    snapshot.timestamp,
                    playback_url(snapshot.timestamp, snapshot.original),
                )
            )
            break
    return targets[:limit]


def capture_dir(base: Path | str) -> Path:
    path = Path(base) / "captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _filename(domain: str, timestamp: str) -> str:
    return f"{_SAFE.sub('_', domain.lower())[:100]}_{timestamp}.png"


async def capture_domain(
    result: DomainResult,
    base: Path | str,
    limiter: AdaptiveRateLimiter | None = None,
    enabled: bool = True,
) -> Captures:
    """Take the planned screenshots for one domain."""
    captures = Captures()
    if not enabled:
        captures.check = CheckState(status=CheckStatus.NOT_RUN, note="캡쳐를 껐습니다.")
        return captures

    targets = plan_targets(result)
    if not targets:
        captures.check = CheckState(
            status=CheckStatus.NOT_RUN, note="캡쳐할 과거 스냅샷이 없습니다."
        )
        return captures

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        captures.check = CheckState(
            status=CheckStatus.NOT_RUN,
            note="Playwright가 설치되지 않아 캡쳐를 건너뛰었습니다.",
        )
        return captures

    out_dir = capture_dir(base)
    failures: list[str] = []
    try:
        async with async_playwright() as driver:
            try:
                browser = await driver.chromium.launch()
            except Exception as exc:  # noqa: BLE001 — 브라우저 미설치 등 무엇이 나와도 검사는 계속한다
                captures.check = CheckState(
                    status=CheckStatus.NOT_RUN,
                    note=(
                        "크롬 브라우저가 설치되지 않아 캡쳐를 건너뛰었습니다 — "
                        f"`uv run playwright install chromium`으로 설치하세요. ({type(exc).__name__})"
                    ),
                )
                return captures
            try:
                page = await browser.new_page(viewport=VIEWPORT)
                page.set_default_timeout(TIMEOUT_MS)
                for label, timestamp, url in targets:
                    if limiter is not None:
                        await limiter.acquire()
                    path = out_dir / _filename(result.domain, timestamp)
                    try:
                        await page.goto(url, timeout=TIMEOUT_MS, wait_until="load")
                        await page.add_style_tag(content=TOOLBAR_CSS)
                        await page.screenshot(path=str(path))
                    except Exception as exc:  # noqa: BLE001 — 한 장 실패가 나머지를 막지 않게
                        failures.append(f"{label}({timestamp[:6]}): {type(exc).__name__}")
                        continue
                    captures.items.append(
                        Capture(
                            label=label,
                            timestamp=timestamp,
                            url=url,
                            file=f"captures/{path.name}",
                        )
                    )
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 — 캡쳐 실패로 파이프라인을 세우지 않는다
        captures.check = CheckState(
            status=CheckStatus.UNCHECKED, note=f"캡쳐 중 오류가 났습니다({type(exc).__name__})."
        )
        return captures

    if captures.items and not failures:
        captures.check = CheckState(status=CheckStatus.OK, note=f"{len(captures.items)}장 저장.")
    elif captures.items:
        captures.check = CheckState(
            status=CheckStatus.OK, note=f"{len(captures.items)}장 저장, 실패 {len(failures)}장: " + ", ".join(failures)
        )
    else:
        captures.check = CheckState(
            status=CheckStatus.UNCHECKED, note="캡쳐에 모두 실패했습니다: " + ", ".join(failures)
        )
    return captures
