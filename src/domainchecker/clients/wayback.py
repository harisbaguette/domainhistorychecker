"""Wayback Machine: one CDX timeline call, then a few representative snapshots."""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

import httpx

from ..models import CheckState, CheckStatus, Snapshot, WaybackHistory
from ..ratelimit import AdaptiveRateLimiter

CDX_URL = "https://web.archive.org/cdx/search/cdx"
MAX_SNAPSHOTS = 8
PATHS_PER_YEAR = 5  # AI에게 줄 연도별 주소 흔적 표본 수
PATH_SAMPLE_LIMIT = 60
# CDX column names -> model field names.
_FIELD_ALIASES = {"statuscode": "status_code"}
# Text seen on archive.org when a site is excluded from playback.
_EXCLUSION_MARKS = (
    "excluded from the wayback machine",
    "blocked site error",
    "robots.txt query exclusion",
)


class WaybackClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        limiter: AdaptiveRateLimiter | None = None,
        max_snapshots: int = MAX_SNAPSHOTS,
    ) -> None:
        self.http = http
        self.limiter = limiter or AdaptiveRateLimiter()
        self.max_snapshots = max_snapshots

    async def _get(self, url: str, params: dict | None = None) -> httpx.Response | None:
        """One rate-limited GET with a single retry after a 429."""
        for attempt in range(2):
            await self.limiter.acquire()
            try:
                response = await self.http.get(url, params=params)
            except httpx.HTTPError:
                return None
            if response.status_code == 429:
                self.limiter.note_429()
                if attempt == 0:
                    continue
                return response
            self.limiter.note_success()
            return response
        return None

    async def timeline(self, domain: str) -> WaybackHistory:
        """Single CDX query giving the year distribution, gaps and status codes."""
        history = WaybackHistory()
        params = {
            "url": f"{domain}/*",
            "output": "json",
            "fl": "timestamp,original,statuscode,mimetype,digest",
            "filter": "mimetype:text/html",
            "collapse": "timestamp:6",  # one row per month
            "limit": "2000",
        }
        response = await self._get(CDX_URL, params)
        if response is None:
            history.check = CheckState(
                status=CheckStatus.UNCHECKED, note="웨이백 접속에 실패했습니다."
            )
            return history
        if response.status_code == 403 or _is_excluded(response.text):
            history.excluded = True
            history.check = CheckState(
                status=CheckStatus.UNCHECKED,
                note="이 도메인은 웨이백에서 열람이 차단되어 있습니다(이력 은폐 가능성).",
            )
            return history
        if response.status_code != 200:
            history.check = CheckState(
                status=CheckStatus.UNCHECKED,
                note=f"웨이백 응답 오류({response.status_code}).",
            )
            return history
        try:
            rows = response.json()
        except ValueError:
            history.check = CheckState(
                status=CheckStatus.UNCHECKED, note="웨이백 응답을 읽을 수 없습니다."
            )
            return history
        if not isinstance(rows, list) or len(rows) < 2:
            history.check = CheckState(status=CheckStatus.OK, note="저장된 이력이 없습니다.")
            return history

        header, *body = rows
        # CDX calls the column "statuscode"; the model field is status_code.
        fields = [_FIELD_ALIASES.get(str(name), str(name)) for name in header]
        allowed = set(Snapshot.model_fields)
        captures = [
            Snapshot(
                **{k: v for k, v in zip(fields, row, strict=False) if k in allowed and v is not None}
            )
            for row in body
        ]
        captures = [c for c in captures if len(c.timestamp) >= 8 and c.timestamp[:4].isdigit()]
        captures.sort(key=lambda c: c.timestamp)
        if not captures:
            history.check = CheckState(status=CheckStatus.OK, note="저장된 이력이 없습니다.")
            return history

        history.total_captures = len(captures)
        history.first_seen = captures[0].timestamp
        history.last_seen = captures[-1].timestamp
        for capture in captures:
            key = capture.timestamp[:4]
            history.year_counts[key] = history.year_counts.get(key, 0) + 1
        years = sorted(int(y) for y in history.year_counts)
        history.gap_years = [y for y in range(years[0], years[-1] + 1) if str(y) not in history.year_counts]
        redirects = sum(1 for c in captures if c.status_code.startswith("3"))
        history.redirect_ratio = round(redirects / len(captures), 3)
        history.selected = select_snapshots(captures, self.max_snapshots)
        history.path_samples = path_samples(captures)
        history.check = CheckState(status=CheckStatus.OK)
        return history

    async def fetch_snapshot(self, snapshot: Snapshot) -> str | None:
        """Fetch the stored bytes of one capture (`id_` = no archive rewriting)."""
        response = await self._get(snapshot.raw_url)
        if response is None or response.status_code >= 400:
            return None
        if _is_excluded(response.text):
            return None
        return response.text

    async def collect(self, domain: str) -> WaybackHistory:
        """Timeline plus the raw HTML of every selected snapshot."""
        history = await self.timeline(domain)
        for snapshot in history.selected:
            html = await self.fetch_snapshot(snapshot)
            history.pages.append(
                {
                    "timestamp": snapshot.timestamp,
                    "url": snapshot.raw_url,
                    "html": html or "",
                    "fetched": html is not None,
                }
            )
        return history


def _is_excluded(text: str) -> bool:
    lowered = (text or "")[:4000].lower()
    return any(mark in lowered for mark in _EXCLUSION_MARKS)


def select_snapshots(captures: list[Snapshot], limit: int = MAX_SNAPSHOTS) -> list[Snapshot]:
    """Pick <=limit representative captures.

    우선순위 — 스팸 이력을 놓치지 않는 순서다:
      1. 말기(마지막 활동 2년) — 폐쇄 직전 상태는 가장 중요한 증거.
      2. 기록 공백 바로 다음 해 — 도메인이 죽었다 되살아난 해는 스팸 업자가
         주워서 쓰기 시작한 해일 가능성이 가장 높다.
      3. 나머지 활동 연도에서 고르게.
    """
    if not captures or limit <= 0:
        return []
    by_year: dict[int, list[Snapshot]] = {}
    for capture in captures:
        by_year.setdefault(capture.year, []).append(capture)
    years = sorted(by_year)

    terminal = years[-2:]  # 말기
    chosen: dict[str, Snapshot] = {}
    for year in terminal:
        pick = by_year[year][-1]
        chosen[pick.timestamp] = pick

    remaining = [y for y in years if y not in terminal]
    # 공백 다음 해 — 그 전 해가 비어 있는(기록이 끊겼던) 해를 먼저 채운다.
    revived = [y for y in remaining if (y - 1) not in by_year and y != years[0]]
    for year in revived[: max(0, limit - len(chosen))]:
        pick = by_year[year][0]
        chosen[pick.timestamp] = pick

    remaining = [y for y in remaining if y not in revived]
    slots = limit - len(chosen)
    if slots > 0 and remaining:
        if len(remaining) <= slots:
            picks = remaining
        else:
            step = (len(remaining) - 1) / (slots - 1) if slots > 1 else 0
            picks = sorted({remaining[round(i * step)] for i in range(slots)})
        for year in picks:
            pick = by_year[year][0]
            chosen[pick.timestamp] = pick
    return sorted(chosen.values(), key=lambda c: c.timestamp)[:limit]


def path_samples(captures: list[Snapshot], per_year: int = PATHS_PER_YEAR) -> list[str]:
    """연도별 주소 흔적 표본 — "YYYY /경로" 목록.

    본문을 안 읽은 해도 주소 조각(/카지노/가입 같은)은 남는다. 표본을 AI에게
    넘겨 모든 활동 연도를 훑게 한다 — 추가 네트워크 호출 없이 재현율을 올린다.
    """
    seen_per_year: dict[int, list[str]] = {}
    samples: list[str] = []
    for capture in captures:
        split = urlsplit(capture.original)
        path = unquote(split.path + (f"?{split.query}" if split.query else ""), errors="replace")
        path = path.strip()[:120]
        if not path.strip("/ "):
            continue
        bucket = seen_per_year.setdefault(capture.year, [])
        if path in bucket or len(bucket) >= per_year:
            continue
        bucket.append(path)
        samples.append(f"{capture.year} {path}")
        if len(samples) >= PATH_SAMPLE_LIMIT:
            break
    return samples
