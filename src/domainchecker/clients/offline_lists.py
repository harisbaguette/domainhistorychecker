"""0단 — 공짜 검사. 세계 공개 위험 명단에 이름이 올라 있는지만 본다.

도메인 하나마다 인터넷에 나가지 않는다. 명단 뭉치를 **한 판에 한 번** 내려받아
데이터 폴더에 넣어 두고, 그 파일을 한 번 훑으면서 이번에 검사할 도메인만 걸러 낸다.
그래서 도메인이 1,000개든 1개든 바깥으로 나가는 요청 수는 똑같다.

쓰는 명단: 툴루즈 대학(UT1)이 공개로 관리하는 분류 명단
(https://dsi.ut-capitole.fr/blacklists/ · CC BY-SA · 주 2~3회 갱신).
판정에 쓰는 갈래만 받는다 — 성인물·도박·피싱·악성코드·불법복제물.

명단을 못 받아도 검사는 절대 막지 않는다. 받아 둔 것이 있으면 그것으로 계속하고,
아무것도 없으면 이 단계만 조용히 건너뛴다.
"""

from __future__ import annotations

import contextlib
import tarfile
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import httpx

BASE_URL = "https://dsi.ut-capitole.fr/blacklists/download"

# 명단 갈래 이름 → 화면에 쓸 우리말. 여기 적힌 것만 받는다.
CATEGORY_LABEL = {
    "adult": "성인물",
    "gambling": "도박",
    "phishing": "피싱(남을 속이는 가짜 사이트)",
    "malware": "악성코드",
    "warez": "불법 복제물",
}

MAX_AGE_DAYS = 7  # 이만큼 지난 명단은 새로 받는다
DOWNLOAD_TIMEOUT = 180.0  # 성인물 명단이 18MB라 넉넉히 준다
MAX_BYTES = 80_000_000  # 한 뭉치가 이보다 크면 받다 만다(폭탄 응답 방어)


def cache_dir(base: Path | str) -> Path:
    return Path(base) / "lists" / "ut1"


def candidates(domain: str) -> list[str]:
    """이 도메인과 그 위 도메인들 — 명단이 위 도메인만 들고 있어도 잡히게.

    a.b.example.com → a.b.example.com, b.example.com, example.com
    (점 두 개짜리까지만 본다 — 그 위는 도메인이 아니라 나라·용도 꼬리표다.)
    """
    name = domain.strip().lower().strip(".")
    if name.startswith("www."):
        name = name[4:]
    parts = [p for p in name.split(".") if p]
    if len(parts) < 2:
        return []
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


class OfflineLists:
    """받아 둔 명단 뭉치를 훑어 이번 판의 도메인만 걸러 내는 검사기."""

    def __init__(
        self,
        base: Path | str,
        categories: Iterable[str] = tuple(CATEGORY_LABEL),
        max_age_days: int = MAX_AGE_DAYS,
        url_base: str = BASE_URL,
    ) -> None:
        self.dir = cache_dir(base)
        self.categories = [c for c in categories if c in CATEGORY_LABEL]
        self.max_age_days = max_age_days
        self.url_base = url_base
        self.ready: list[str] = []  # 실제로 쓸 수 있는 명단 갈래
        self.note = "위험 명단을 아직 받지 않았습니다."

    @property
    def available(self) -> bool:
        return bool(self.ready)

    def path(self, category: str) -> Path:
        return self.dir / f"{category}.tar.gz"

    def _stale(self, path: Path) -> bool:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            return True
        return age >= self.max_age_days * 86400

    def _stamp(self, category: str) -> str:
        try:
            when = datetime.fromtimestamp(self.path(category).stat().st_mtime, UTC)
        except OSError:
            return ""
        return when.date().isoformat()

    async def refresh(self, http: httpx.AsyncClient) -> None:
        """묵은 명단만 새로 받는다. 못 받으면 받아 둔 것으로 계속한다."""
        got: list[str] = []
        for category in self.categories:
            path = self.path(category)
            if path.exists() and not self._stale(path):
                got.append(category)
                continue
            if await self._download(http, category) or path.exists():
                got.append(category)
        self.ready = got
        if not got:
            self.note = "위험 명단을 받지 못해 이 검사는 건너뛰었습니다."
            return
        names = ", ".join(CATEGORY_LABEL[c] for c in got)
        self.note = f"공개 위험 명단(UT1 툴루즈) 확인 — {names}. 받은 날짜 {self._stamp(got[0])}."

    async def _download(self, http: httpx.AsyncClient, category: str) -> bool:
        """명단 한 뭉치 받기. 반쪽으로 받힌 파일은 절대 명단으로 쓰지 않는다."""
        url = f"{self.url_base}/{category}.tar.gz"
        target = self.path(category)
        tmp = target.with_name(f"{category}.tar.gz.tmp")
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            size = 0
            with tmp.open("wb") as out:
                async with http.stream("GET", url, timeout=DOWNLOAD_TIMEOUT) as response:
                    if response.status_code != 200:
                        raise ValueError(f"응답 {response.status_code}")
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise ValueError("명단이 너무 큽니다")
                        out.write(chunk)
            # 받은 것이 진짜 명단 뭉치인지 첫 칸만 열어 본다.
            with tarfile.open(tmp, "r:gz") as tar:
                first = tar.next()
                if first is None or not first.name.endswith("/domains"):
                    raise ValueError("명단 뭉치가 아닙니다")
            tmp.replace(target)
            return True
        except Exception:  # noqa: BLE001 — 명단 받기가 어떤 이유로 실패하든 검사를 막지 않는다
            # 무엇이 터지든(응답 오류·끊긴 연결·깨진 뭉치·디스크) 0단만 건너뛰고 간다.
            # 실패했다는 사실은 note 로 화면·보고서에 그대로 남는다(조용히 덮지 않는다).
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)
            return False

    def screen(self, domains: Iterable[str]) -> dict[str, list[str]]:
        """받아 둔 명단을 한 번씩 훑어 걸린 도메인만 돌려준다 — {도메인: [갈래 이름]}.

        파일을 통째로 기억에 올리지 않는다. 한 줄씩 읽으면서 이번 판의 도메인
        목록(많아야 1,000개)과 맞는지만 본다.
        """
        wanted: dict[bytes, str] = {}
        for domain in domains:
            for name in candidates(domain):
                wanted.setdefault(name.encode("utf-8"), domain)
        hits: dict[str, list[str]] = {}
        if not wanted:
            return hits
        for category in self.ready:
            label = f"{CATEGORY_LABEL[category]} 명단(UT1 툴루즈"
            stamp = self._stamp(category)
            label += f", {stamp} 받음)" if stamp else ")"
            try:
                with tarfile.open(self.path(category), "r:gz") as tar:
                    stream = tar.extractfile(f"{category}/domains")
                    if stream is None:
                        continue
                    for raw in stream:
                        domain = wanted.get(raw.strip())
                        if domain is not None and label not in hits.setdefault(domain, []):
                            hits[domain].append(label)
            except (OSError, tarfile.TarError, KeyError, EOFError):
                continue  # 한 갈래가 깨져도 나머지 갈래는 그대로 본다
        return hits
