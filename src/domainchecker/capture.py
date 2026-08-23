"""Playwright screenshots of archived pages — runs after every other check.

One shot of the terminal period (말기), one of the risk period when a signal was
found, and one per AI topic period. Screenshots share the global Wayback rate limiter,
and any failure (no browser installed, timeout) is recorded without stopping
the pipeline.

두 가지를 특히 조심한다.

1. 브라우저는 "다른 프로그램을 띄울 수 있는 일꾼"에서만 열린다. 서버를 자동
   재시작 모드로 켜면 uvicorn 이 윈도우에서 그런 일을 못 하는 일꾼을 골라 주기
   때문에, 그럴 때는 이쪽에서 따로 일꾼을 만들어 브라우저를 연다.
2. 옛 화면이 통째로 빈 종이로 찍히는 경우가 많다. 그 시기 화면이 창고에 제대로
   안 담겼기 때문인데, 이걸 그냥 저장하면 사용자는 하얀 사진만 보게 된다.
   그래서 찍기 전에 "화면에 보이는 게 정말 있는지" 확인하고, 없으면 같은 시기의
   다른 날짜 화면으로 바꿔 찍는다.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import suppress
from pathlib import Path

from .cache import safe_name
from .models import Capture, Captures, CheckState, CheckStatus, DomainResult, Snapshot
from .ratelimit import AdaptiveRateLimiter

TIMEOUT_MS = 20_000
SETTLE_MS = 8_000  # 다 불러온 뒤 조용해질 때까지 더 기다려 보는 시간
BLANK_RETRY_MS = 2_000  # 빈 화면처럼 보일 때 한 번 더 기다려 보는 시간
MAX_ALTERNATES = 2  # 한 장을 살리려고 다른 날짜를 최대 몇 번까지 더 시도할지
VIEWPORT = {"width": 1280, "height": 800}
# The archive injects its own toolbar into playback pages; hide it.
TOOLBAR_CSS = "#wm-ipp-base, #wm-ipp, #donato { display: none !important; }"

# 눈에 보이는 알맹이가 있는지 브라우저에게 직접 묻는 검사.
# 창고에 없는 화면은 archive.org 의 안내문이 크기 0 짜리 틀 안에 들어와서
# 화면에는 아무것도 안 그려진다 — 그래서 "자리를 차지하는 틀"만 인정한다.
HAS_CONTENT_JS = r"""() => {
  const d = document;
  if (!d.body) return false;                       // 틀(frameset) 문서엔 본문이 없다
  const r = d.body.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return false;   // 자리를 안 차지하면 안 보이는 것
  const text = (d.body.innerText || '').replace(/\s+/g, ' ').trim();
  if (text.length >= 10) return true;
  for (const img of d.images) {
    if (img.naturalWidth > 32 && img.naturalHeight > 32) {
      const ir = img.getBoundingClientRect();
      if (ir.width > 32 && ir.height > 32) return true;
    }
  }
  return false;
}"""


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


def _year_of(value: object) -> int:
    try:
        return int(str(value)[:4])
    except ValueError:
        return 0


def _snapshot_for_period(selected: list[Snapshot], period: dict) -> Snapshot | None:
    """그 시기 안의 스냅샷 중 한가운데 것 — 초입은 이전 주제가 남아 있을 수 있다."""
    start = _year_of(period.get("start"))
    end = _year_of(period.get("end"))
    if not start and not end:
        return None
    start = start or end
    end = end or start
    inside = [s for s in selected if start <= s.year <= end]
    return inside[len(inside) // 2] if inside else None


def alternate_snapshots(
    result: DomainResult, timestamp: str, limit: int = MAX_ALTERNATES
) -> list[Snapshot]:
    """고른 화면이 못 쓰게 나왔을 때 대신 찍어 볼 이웃 날짜들.

    같은 주제 시기 안의 날짜를 먼저, 그다음 시간상 가까운 날짜 순으로 준다 —
    시기가 통째로 어긋난 사진을 보여 주지 않기 위해서다.
    """
    selected = result.wayback.selected
    target_year = _year_of(timestamp)
    span: tuple[int, int] | None = None
    for period in result.ai.topic_periods:
        start = _year_of(period.get("start"))
        end = _year_of(period.get("end")) or start
        start = start or end
        if start and start <= target_year <= end:
            span = (start, end)
            break

    others = [s for s in selected if s.timestamp != timestamp]
    others.sort(key=lambda s: abs(int(s.timestamp[:8] or 0) - int(timestamp[:8] or 0)))
    if span is None:
        return others[:limit]
    inside = [s for s in others if span[0] <= s.year <= span[1]]
    outside = [s for s in others if not (span[0] <= s.year <= span[1])]
    return (inside + outside)[:limit]


def capture_dir(base: Path | str) -> Path:
    path = Path(base) / "captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _filename(domain: str, timestamp: str) -> str:
    return f"{safe_name(domain)}_{timestamp}.png"


def when_label(timestamp: str) -> str:
    """'20230525...' → '2023년 5월' — 화면에 영어·숫자덩어리를 내보내지 않는다."""
    year, month = timestamp[:4], timestamp[4:6]
    if not year.isdigit():
        return "시기 미상"
    if month.isdigit() and month != "00":
        return f"{year}년 {int(month)}월"
    return f"{year}년"


def failure_reason(exc: BaseException) -> str:
    """무슨 일이 났는지 쉬운 우리말 한 문장으로 — 영어 오류 이름은 내보내지 않는다."""
    text = str(exc)
    if "Executable doesn't exist" in text or "playwright install" in text:
        return "크롬 브라우저가 설치되어 있지 않습니다"
    if "Timeout" in type(exc).__name__ or "Timeout" in text or "timeout" in text:
        return "정해진 시간 안에 화면이 열리지 않았습니다"
    if "ERR_INTERNET_DISCONNECTED" in text or "ERR_NAME_NOT_RESOLVED" in text:
        return "인터넷에 연결되지 않아 화면을 열지 못했습니다"
    if "net::" in text or "NS_ERROR" in text:
        return "옛 화면 창고에 연결하지 못했습니다"
    return "화면을 여는 중 문제가 생겼습니다"


def _can_start_browser(loop: asyncio.AbstractEventLoop) -> bool:
    """이 일꾼(이벤트 루프)이 브라우저 같은 다른 프로그램을 띄울 수 있는가.

    윈도우에서 서버를 자동 재시작 모드로 켜면 uvicorn 이 다른 프로그램을 못 띄우는
    일꾼을 골라 준다. 그때 브라우저를 열려고 하면 그대로 실패한다 — 그래서 찍기
    전에 물어본다. 실제로 실패를 내는 바로 그 기능이 갖춰져 있는지를 본다.
    """
    maker = getattr(type(loop), "_make_subprocess_transport", None)
    if maker is None:
        return False
    return maker is not asyncio.BaseEventLoop._make_subprocess_transport


def _new_capable_loop() -> asyncio.AbstractEventLoop:
    """브라우저를 띄울 수 있는 일꾼을 새로 하나 만든다."""
    if sys.platform == "win32":
        return asyncio.ProactorEventLoop()
    return asyncio.new_event_loop()


async def _wait_for_slot(
    limiter: AdaptiveRateLimiter | None, host_loop: asyncio.AbstractEventLoop | None
) -> None:
    """차례를 기다린다 — 다른 실에서 돌더라도 순서표는 원래 것 하나만 쓴다."""
    if limiter is None:
        return
    if host_loop is None:
        await limiter.acquire()
        return
    await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(limiter.acquire(), host_loop))


async def _page_has_content(page) -> bool:
    """지금 이 화면에 눈에 보이는 알맹이가 있는지 브라우저에게 물어본다."""
    for frame in page.frames:
        try:
            if await frame.evaluate(HAS_CONTENT_JS):
                return True
        except Exception:  # noqa: BLE001 — 사라진 틀 하나 때문에 판정을 멈추지 않는다
            continue
    return False


async def _open_and_settle(page, url: str) -> bool:
    """화면을 열고, 다 그려질 때까지 기다린 뒤, 볼 게 있는지 알려 준다."""
    await page.goto(url, timeout=TIMEOUT_MS, wait_until="load")
    # 끝없이 통신하는 옛 화면도 있다 — 조용해지지 않아도 그냥 넘어간다.
    with suppress(Exception):
        await page.wait_for_load_state("networkidle", timeout=SETTLE_MS)
    # 툴바 가리기에 실패해도 사진은 찍는다.
    with suppress(Exception):
        await page.add_style_tag(content=TOOLBAR_CSS)
    if await _page_has_content(page):
        return True
    # 늦게 그려지는 화면일 수 있으니 딱 한 번만 더 기다려 본다.
    await asyncio.sleep(BLANK_RETRY_MS / 1000)
    return await _page_has_content(page)


async def _shoot_all(
    captures: Captures,
    plans: list[tuple[str, list[tuple[str, str]]]],
    out_dir: Path,
    domain: str,
    limiter: AdaptiveRateLimiter | None,
    host_loop: asyncio.AbstractEventLoop | None,
) -> list[str]:
    """브라우저를 한 번 열어 계획한 사진을 모두 찍는다. 못 찍은 사유 목록을 돌려준다."""
    from playwright.async_api import async_playwright

    failures: list[str] = []
    used: set[str] = set()
    async with async_playwright() as driver:
        try:
            browser = await driver.chromium.launch()
        except Exception as exc:  # 브라우저 미설치 등 무엇이 나와도 검사는 계속한다
            captures.check = CheckState(
                status=CheckStatus.NOT_RUN,
                note=(
                    "크롬 브라우저가 없어 캡쳐를 건너뛰었습니다 — "
                    "`uv run playwright install chromium`으로 설치하세요."
                ),
            )
            raise _Skipped from exc
        try:
            page = await browser.new_page(viewport=VIEWPORT)
            page.set_default_timeout(TIMEOUT_MS)
            for label, candidates in plans:
                reason = "찍을 화면을 찾지 못했습니다"
                shot_when = ""
                for timestamp, url in candidates:
                    if timestamp in used:
                        continue
                    shot_when = when_label(timestamp)
                    await _wait_for_slot(limiter, host_loop)
                    try:
                        visible = await _open_and_settle(page, url)
                    except Exception as exc:  # noqa: BLE001 — 한 장 실패가 나머지를 막지 않게
                        reason = failure_reason(exc)
                        continue
                    if not visible:
                        reason = "그 시기 화면이 창고에 담겨 있지 않아 빈 종이로만 나옵니다"
                        continue
                    path = out_dir / _filename(domain, timestamp)
                    try:
                        await page.screenshot(path=str(path))
                    except Exception as exc:  # noqa: BLE001 — 저장 실패도 한 장으로 끝낸다
                        reason = failure_reason(exc)
                        continue
                    used.add(timestamp)
                    captures.items.append(
                        Capture(
                            label=label,
                            timestamp=timestamp,
                            url=url,
                            file=f"captures/{path.name}",
                        )
                    )
                    break
                else:
                    where = f"({shot_when})" if shot_when else ""
                    failures.append(f"{label}{where}: {reason}")
        finally:
            await browser.close()
    return failures


class _Skipped(Exception):
    """캡쳐를 아예 건너뛴 경우 — 사유는 이미 적어 두었다."""


def _shoot_in_own_loop(
    captures: Captures,
    plans: list[tuple[str, list[tuple[str, str]]]],
    out_dir: Path,
    domain: str,
    limiter: AdaptiveRateLimiter | None,
    host_loop: asyncio.AbstractEventLoop,
) -> list[str]:
    """브라우저를 못 띄우는 일꾼을 만났을 때 — 새 일꾼을 만들어 거기서 찍는다."""
    loop = _new_capable_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            _shoot_all(captures, plans, out_dir, domain, limiter, host_loop)
        )
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def build_plans(result: DomainResult) -> list[tuple[str, list[tuple[str, str]]]]:
    """사진 한 장마다 '첫 후보 + 대신 쓸 이웃 날짜'를 묶어 둔다."""
    plans: list[tuple[str, list[tuple[str, str]]]] = []
    for label, timestamp, url in plan_targets(result):
        candidates = [(timestamp, url)]
        for snapshot in alternate_snapshots(result, timestamp):
            candidates.append(
                (snapshot.timestamp, playback_url(snapshot.timestamp, snapshot.original))
            )
        plans.append((label, candidates))
    return plans


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

    plans = build_plans(result)
    if not plans:
        captures.check = CheckState(
            status=CheckStatus.NOT_RUN, note="캡쳐할 과거 스냅샷이 없습니다."
        )
        return captures

    try:
        import playwright.async_api  # noqa: F401
    except ImportError:
        captures.check = CheckState(
            status=CheckStatus.NOT_RUN,
            note="Playwright가 설치되지 않아 캡쳐를 건너뛰었습니다.",
        )
        return captures

    out_dir = capture_dir(base)
    loop = asyncio.get_running_loop()
    try:
        if _can_start_browser(loop):
            failures = await _shoot_all(captures, plans, out_dir, result.domain, limiter, None)
        else:
            failures = await asyncio.to_thread(
                _shoot_in_own_loop, captures, plans, out_dir, result.domain, limiter, loop
            )
    except _Skipped:
        return captures
    except Exception as exc:  # noqa: BLE001 — 캡쳐 실패로 파이프라인을 세우지 않는다
        captures.check = CheckState(
            status=CheckStatus.UNCHECKED,
            note=f"캡쳐 중 문제가 생겼습니다 — {failure_reason(exc)}.",
        )
        return captures

    if captures.items and not failures:
        captures.check = CheckState(status=CheckStatus.OK, note=f"{len(captures.items)}장 저장.")
    elif captures.items:
        captures.check = CheckState(
            status=CheckStatus.OK,
            note=f"{len(captures.items)}장 저장, {len(failures)}장은 못 찍었습니다: "
            + ", ".join(failures),
        )
    else:
        captures.check = CheckState(
            status=CheckStatus.UNCHECKED,
            note="화면 사진을 한 장도 만들지 못했습니다: " + ", ".join(failures),
        )
    return captures
