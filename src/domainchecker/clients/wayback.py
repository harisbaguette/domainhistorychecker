"""Wayback Machine: one CDX timeline call, then a few representative snapshots."""

from __future__ import annotations

import httpx

from ..models import CheckState, CheckStatus, Snapshot, WaybackHistory
from ..ratelimit import AdaptiveRateLimiter

CDX_URL = "https://web.archive.org/cdx/search/cdx"
MAX_SNAPSHOTS = 6
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
    """Pick <=limit representative captures; the last two active years are mandatory.

    말기(마지막 활동 2년)는 폐쇄 직전 상태라 가장 중요한 증거이므로 항상 포함한다.
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
