"""Playwright screenshots of archived pages — runs after every other check.

One shot of the terminal period (말기), one of the risk period when a signal was
found, and one per AI topic period. Screenshots share the global Wayback rate limiter,
and any failure (no browser installed, timeout) is recorded without stopping
the pipeline.
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import Capture, Captures, CheckState, CheckStatus, DomainResult, Snapshot
from .ratelimit import AdaptiveRateLimiter

TIMEOUT_MS = 20_000
VIEWPORT = {"width": 1280, "height": 800}
# The archive injects its own toolbar into playback pages; hide it.
TOOLBAR_CSS = "#wm-ipp-base, #wm-ipp, #donato { display: none !important; }"

_SAFE = re.compile(r"[^a-z0-9.\-]")


def playback_url(timestamp: str, original: str) -> str:
    """Normal playback URL (not `id_`) so the page renders with its assets."""
    return f"https://web.archive.org/web/{timestamp}/{original}"


def plan_targets(result: DomainResult, limit: int = 6) -> list[tuple[str, str, str]]:
    """Return (label, timestamp, url) for the shots worth taking.

    말기 · 위험 신호 시기에 더해, AI가 나눈 주제 시기마다 한 장씩 찍는다 —
    "과거에 무엇으로 운영됐나"를 글이 아니라 사진으로 확인할 수 있게.
    """
    selected = result.wayback.selected
    if not selected:
        return []
    targets: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def add(label: str, snapshot: Snapshot) -> None:
        if snapshot.timestamp in seen:
            return
        seen.add(snapshot.timestamp)
        targets.append(
            (label, snapshot.timestamp, playback_url(snapshot.timestamp, snapshot.original))
        )

    add("말기", selected[-1])
    risky = set(result.rules.risk_timestamps)
    for snapshot in selected:
        if snapshot.timestamp in risky and snapshot.timestamp not in seen:
            add("위험 신호 시기", snapshot)
            break
    for period in result.ai.topic_periods:
        snapshot = _snapshot_for_period(selected, period)
        if snapshot is not None:
            add(period_label(period), snapshot)
    return targets[:limit]


def period_label(period: dict) -> str:
    """'사진 복원 블로그 (2005~2012)' — 캡쳐 밑에 붙는 시기 이름."""
    start = str(period.get("start", ""))[:4]
    end = str(period.get("end", ""))[:4]
    topic = str(period.get("topic", "")).strip()
    span = f"{start}~{end}" if start and end and start != end else (start or end)
    return f"{topic} ({span})" if span else topic


def _snapshot_for_period(selected: list[Snapshot], period: dict) -> Snapshot | None:
    """그 시기 안의 스냅샷 중 한가운데 것 — 초입은 이전 주제가 남아 있을 수 있다."""

    def year_of(value: object) -> int:
        try:
            return int(str(value)[:4])
        except ValueError:
            return 0

    start = year_of(period.get("start"))
    end = year_of(period.get("end"))
    if not start and not end:
        return None
    start = start or end
    end = end or start
    inside = [s for s in selected if start <= s.year <= end]
    return inside[len(inside) // 2] if inside else None


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
