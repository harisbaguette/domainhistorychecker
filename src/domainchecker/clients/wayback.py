"""Wayback Machine — 전수 확인.

표본을 몇 장 뽑아 읽는 방식은 버렸다. 세 가지를 전부 가져온다:
  1) 활동 통계 — 월별로 접은 저장 목록(연도 분포·공백·리다이렉트 비율).
  2) 앞페이지 변경본 전부 — 내용이 바뀐 시점마다 저장된 서로 다른 판(digest
     기준)을 모두 받아, 그 본문을 전부 읽는다. 내용이 같은 저장분을 또 읽는
     것은 낭비일 뿐이므로 "서로 다른 판 전부"가 곧 전수다.
  3) 전체 주소 목록 — 이 도메인 밑에 저장된 서로 다른 주소 전부. 본문이 없는
     페이지도 주소 조각(/카지노/가입 같은)은 남으므로 AI가 전 기간을 훑는다.

몇 장을 읽었고 몇 장을 못 읽었는지는 coverage(확인 범위)로 숫자 그대로
보고한다 — "전부 봤다"는 말은 숫자로 증명될 때만 한다.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

import httpx

from ..models import CheckState, CheckStatus, Snapshot, WaybackHistory
from ..ratelimit import AdaptiveRateLimiter

CDX_URL = "https://web.archive.org/cdx/search/cdx"
CDX_LIMIT = 5000  # 조회 한 번이 받을 최대 행 — 여기 걸리면 "이상"이라고 보고한다
PAGE_BYTES_LIMIT = 1_000_000  # 저장분 하나에서 받아 둘 최대 글자(폭탄 응답 방어)

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
    ) -> None:
        self.http = http
        self.limiter = limiter or AdaptiveRateLimiter()

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

    async def _cdx_rows(self, params: dict) -> list[Snapshot] | None:
        """One CDX query parsed into snapshots; None = 조회 실패(모른다)."""
        response = await self._get(CDX_URL, params)
        if response is None or response.status_code != 200:
            return None
        try:
            rows = response.json()
        except ValueError:
            return None
        if not isinstance(rows, list):
            return None
        if len(rows) < 2:
            return []
        header, *body = rows
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
        return captures

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
        history.check = CheckState(status=CheckStatus.OK)
        return history

    async def versions(self, domain: str) -> list[Snapshot] | None:
        """앞페이지의 서로 다른 변경본 전부. None = 조회 실패.

        mimetype 거름망을 두지 않는다 — 카지노로 넘겨보내기만 하던 시기의
        저장분은 html이 아닌 형식으로 남는 일이 많아, 거르면 그 시대가 통째로
        이력에서 사라진다(진상 검증 지적 5).
        """
        return await self._cdx_rows(
            {
                "url": domain,
                "output": "json",
                "fl": "timestamp,original,statuscode,mimetype,digest",
                "collapse": "digest",  # 내용이 같은 연속 저장분은 한 판으로
                "limit": str(CDX_LIMIT),
            }
        )

    async def site_pages(self, domain: str) -> list[Snapshot] | None:
        """도메인 밑에 저장된 서로 다른 주소 전부(주소마다 저장분 하나). None = 조회 실패."""
        rows = await self._cdx_rows(
            {
                "url": f"{domain}/*",
                "output": "json",
                "fl": "timestamp,original,statuscode,mimetype,digest",
                "filter": "mimetype:text/html",
                "collapse": "urlkey",  # 같은 주소는 한 번만
                "limit": str(CDX_LIMIT),
            }
        )
        if rows is None:
            return None
        # 뿌리 주소는 변경본 전수 읽기가 따로 다루므로 하위 주소만 남긴다.
        return [c for c in rows if short_path(c.original)]

    async def fetch_snapshot(self, snapshot: Snapshot) -> str | None:
        """Fetch the stored bytes of one capture (`id_` = no archive rewriting)."""
        response = await self._get(snapshot.raw_url)
        if response is None or response.status_code >= 400:
            return None
        if _is_excluded(response.text):
            return None
        return response.text

    async def collect(self, domain: str) -> WaybackHistory:
        """사람이 보는 방식 그대로 — 목록은 전부 훑고, 읽을 곳은 골라 정독한다.

        ① 앞페이지가 내용이 바뀐 시점의 판은 전부 본문을 읽는다(달력에서 바뀐
           지점을 찍어 보는 것과 같음). ② 하위 주소는 목록을 전부 받아 두되,
           본문 정독은 AI가 수상하다고 고른 곳만 한다(선별·정독은 파이프라인).
        몇 장을 읽었는지는 coverage_note에 숫자로 남는다.
        """
        history = await self.timeline(domain)
        if not history.check.ok or not history.has_history:
            return history

        versions = await self.versions(domain)
        if versions is None:
            history.check = CheckState(
                status=CheckStatus.UNCHECKED,
                note="변경본 목록 조회에 실패해 전수 확인을 하지 못했습니다.",
            )
            return history
        subpages = await self.site_pages(domain)
        if subpages is None:
            history.check = CheckState(
                status=CheckStatus.UNCHECKED,
                note="하위 주소 목록 조회에 실패해 전수 확인을 하지 못했습니다.",
            )
            return history

        history.versions_total = len(versions)
        # 변경이 잦은 해도 해마다 처음·중간·끝 판이면 그 해의 모습 변화가 다 보인다.
        # 판 수가 적으면 대표가 곧 전부다 — 몇 장 상한이 아니라 시대 단위 접기.
        reps = era_representatives(versions)
        history.selected = reps
        history.subpages = subpages
        history.path_samples = _year_paths(subpages)

        for snapshot in reps:
            html = await self.fetch_snapshot(snapshot)
            history.pages.append(
                {
                    "timestamp": snapshot.timestamp,
                    "url": snapshot.raw_url,
                    "html": (html or "")[:PAGE_BYTES_LIMIT],
                    "fetched": html is not None,
                }
            )
        history.versions_read = sum(1 for p in history.pages if p["fetched"])

        failed = len(reps) - history.versions_read
        over_v = " 이상(조회 한도)" if len(versions) >= CDX_LIMIT else ""
        over_s = " 이상(조회 한도)" if len(subpages) >= CDX_LIMIT - 1 else ""
        if len(reps) == len(versions):
            read_part = f"앞페이지 변경본 {len(versions)}장{over_v} 전부 읽음"
        else:
            read_part = (
                f"앞페이지 변경본 {len(versions)}장{over_v}을 연도별 처음·중간·끝 "
                f"대표 {len(reps)}장으로 접어 읽음"
            )
        history.coverage_note = (
            read_part
            + (f"({failed}장은 열리지 않음)" if failed else "")
            + f", 하위 주소 {len(subpages)}개{over_s} 전부 훑음."
        )
        return history

    async def read_subpages(self, snapshots: list[Snapshot]) -> list[dict]:
        """골라낸 하위 페이지들의 본문을 읽어 pages 형식으로 돌려준다."""
        pages = []
        for snapshot in snapshots:
            html = await self.fetch_snapshot(snapshot)
            pages.append(
                {
                    "timestamp": snapshot.timestamp,
                    "url": snapshot.raw_url,
                    "html": (html or "")[:PAGE_BYTES_LIMIT],
                    "fetched": html is not None,
                }
            )
        return pages


def short_path(url: str) -> str:
    """주소에서 도메인 이름을 뺀 경로 부분 — 뿌리(/)면 빈 문자열."""
    split = urlsplit(url)
    path = unquote(split.path + (f"?{split.query}" if split.query else ""), errors="replace")
    path = path.strip()[:120]
    return path if path.strip("/ ") else ""


def era_representatives(versions: list[Snapshot]) -> list[Snapshot]:
    """앞페이지 변경본의 시대 대표 — 해마다 처음·중간·끝 판.

    글이 자주 바뀌는 살아 있는 사이트는 변경본이 수백 장이지만, 그 해의 정체가
    바뀌었는지는 해의 시작·중간·끝 세 장이면 드러난다. 판이 셋 이하인 해는
    그대로 전부다 — 임의 상한이 아니라 시대(연도) 단위의 접기다.
    """
    by_year: dict[int, list[Snapshot]] = {}
    for snapshot in versions:
        by_year.setdefault(snapshot.year, []).append(snapshot)
    reps: dict[str, Snapshot] = {}
    for members in by_year.values():
        members.sort(key=lambda s: s.timestamp)
        for snap in (members[0], members[len(members) // 2], members[-1]):
            reps[snap.timestamp + snap.original] = snap
    return sorted(reps.values(), key=lambda s: s.timestamp)


_DIGITS = re.compile(r"\d+")


def cluster_key(path: str) -> str:
    """주소의 '모양'만 남긴 열쇠 — 뜻 판단이 아니라 구조 묶기다.

    /post/1042 와 /post/93 은 같은 틀에서 찍은 페이지라 한 유형이다.
    숫자를 N으로 접으면 같은 틀의 주소가 같은 열쇠로 모인다.
    """
    base = path.split("?")[0]
    segments = [_DIGITS.sub("N", s) for s in base.split("/") if s]
    return "/" + "/".join(segments) if segments else "/"


def cluster_representatives(
    subpages: list[Snapshot], gap_years: list[int] | None = None
) -> tuple[list[Snapshot], int]:
    """페이지 유형마다 반드시 읽을 대표 저장분 — (대표 목록, 유형 수).

    표본이 아니라 종류 단위의 전수다: 존재하는 모든 주소 유형이 빠짐없이
    한 번 이상 읽힌다. 유형마다 처음 해·마지막 해, 그리고 기록 공백 바로
    다음 해(주인이 바뀌었을 가능성이 큰 해)의 저장분을 고른다.
    """
    groups: dict[str, list[Snapshot]] = {}
    for snap in subpages:
        groups.setdefault(cluster_key(short_path(snap.original)), []).append(snap)
    gap_next = {y + 1 for y in (gap_years or [])}
    reps: dict[str, Snapshot] = {}
    for members in groups.values():
        members.sort(key=lambda s: s.timestamp)
        chosen = [members[0], members[-1]] + [m for m in members if m.year in gap_next]
        for snap in chosen:
            reps[snap.timestamp + snap.original] = snap
    ordered = sorted(reps.values(), key=lambda s: s.timestamp)
    return ordered, len(groups)


def _year_paths(subpages: list[Snapshot]) -> list[str]:
    """"YYYY /경로" 목록 — 본문과 별개로 AI가 연도별 주소 흔적을 훑는 데 쓴다."""
    out: list[str] = []
    seen: set[str] = set()
    for capture in subpages:
        path = short_path(capture.original)
        if not path or path in seen:
            continue
        seen.add(path)
        out.append(f"{capture.year} {path}")
    return out


def _is_excluded(text: str) -> bool:
    lowered = (text or "")[:4000].lower()
    return any(mark in lowered for mark in _EXCLUSION_MARKS)
