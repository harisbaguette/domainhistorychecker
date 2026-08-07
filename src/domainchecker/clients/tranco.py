"""키 없이 도는 권위 점수 — Tranco 인기 도메인 100만 목록.

Open PageRank(키 필요)가 없을 때 이 검사가 대신 돈다. Tranco 는 여러 인기 순위를
합쳐 매일 새로 만드는 공개 목록이고, 키 없이 통째로 내려받을 수 있다. 목록 파일은
data 폴더에 저장해 두고 일주일에 한 번만 다시 받는다(도메인마다 물어보지 않는다).

목록에 없는 도메인은 "권위 0" 이 아니라 "자료 없음"이다 — 만료 도메인 대부분이
여기에 해당하며, Open PageRank 도 같은 경우 자료 없음으로 답한다.
"""

from __future__ import annotations

import contextlib
import io
import math
import time
import zipfile
from pathlib import Path

import httpx

from ..models import Authority, CheckState, CheckStatus
from . import http_reason

LIST_URL = "https://tranco-list.eu/top-1m.csv.zip"
CACHE_NAME = "tranco-top-1m.csv.zip"
MAX_AGE_SECONDS = 7 * 24 * 3600  # 목록을 며칠까지 그대로 믿을지
MAX_BYTES = 80_000_000  # 목록이 갑자기 커져도 디스크를 채우지 않게
LIST_SIZE = 1_000_000


def rank_to_score(rank: int) -> float:
    """순위(1위가 최고)를 0~10 점으로. 자릿수마다 고르게 떨어지는 눈금."""
    if rank <= 0:
        return 0.0
    score = 10.0 * (1.0 - math.log10(rank) / math.log10(LIST_SIZE))
    return round(max(0.0, min(10.0, score)), 2)


def _read_cache(path: Path, fresh_only: bool) -> bytes | None:
    """저장해 둔 목록 파일. 없거나 못 읽으면 None."""
    try:
        if not path.exists():
            return None
        if fresh_only and (time.time() - path.stat().st_mtime) >= MAX_AGE_SECONDS:
            return None
        return path.read_bytes()
    except OSError:
        return None


def _write_cache(path: Path, body: bytes) -> None:
    """받은 목록을 저장. 저장에 실패해도 검사는 그대로 진행한다."""
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)


def _drop_cache(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


async def _list_bytes(path: Path, http: httpx.AsyncClient) -> tuple[bytes | None, str]:
    """목록 파일 내용과 출처 설명. 받지도 못하고 저장분도 없으면 (None, 사유)."""
    saved = _read_cache(path, fresh_only=True)
    if saved is not None:
        return saved, "저장해 둔 목록"

    reason = ""
    try:
        body = b""
        async with http.stream(
            "GET",
            LIST_URL,
            timeout=httpx.Timeout(180.0, connect=15.0),
            follow_redirects=True,
        ) as response:
            if response.status_code != 200:
                reason = http_reason(response.status_code)
            else:
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        reason = "목록 파일이 예상보다 너무 큽니다"
                        body = b""
                        break
    except httpx.HTTPError:
        reason = "인터넷 연결에 실패했습니다"

    if not reason and body:
        _write_cache(path, body)
        return body, "새로 받은 목록"

    stale = _read_cache(path, fresh_only=False)
    if stale is not None:
        return stale, "예전에 저장해 둔 목록"
    return None, f"인기 도메인 목록을 받지 못했습니다 — {reason or '알 수 없는 이유'}."


def ranks_for(payload: bytes, wanted: set[str]) -> dict[str, int]:
    """압축 목록을 한 줄씩 훑어 찾는 도메인의 순위만 뽑는다(전체를 메모리에 안 올림)."""
    found: dict[str, int] = {}
    if not wanted:
        return found
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not names:
            return found
        with archive.open(names[0]) as stream:
            for raw in io.TextIOWrapper(stream, encoding="utf-8", errors="replace"):
                rank, _, name = raw.strip().partition(",")
                name = name.strip().lower()
                if name in wanted and rank.isdigit():
                    found[name] = int(rank)
                    if len(found) == len(wanted):
                        break
    return found


def _key(domain: str) -> str:
    return domain.strip().lower().removeprefix("www.").rstrip(".")


async def fetch_batch(
    domains: list[str], http: httpx.AsyncClient, base_dir: Path | str
) -> dict[str, Authority]:
    """도메인마다 Authority 하나. 목록을 못 받으면 전부 미확인으로 내려간다."""
    if not domains:
        return {}
    path = Path(base_dir) / CACHE_NAME
    payload, source = await _list_bytes(path, http)
    if payload is None:
        state = CheckState(status=CheckStatus.UNCHECKED, note=source)
        return {d: Authority(check=state) for d in domains}

    try:
        ranks = ranks_for(payload, {_key(d) for d in domains})
    except (zipfile.BadZipFile, OSError, UnicodeError):
        _drop_cache(path)  # 깨진 파일은 지워, 다음 검사 때 새로 받게 한다
        state = CheckState(
            status=CheckStatus.UNCHECKED,
            note="인기 도메인 목록 파일이 깨져 있습니다(다음 검사 때 새로 받습니다).",
        )
        return {d: Authority(check=state) for d in domains}

    out: dict[str, Authority] = {}
    for domain in domains:
        rank = ranks.get(_key(domain))
        if rank is None:
            out[domain] = Authority(
                has_data=False,
                check=CheckState(
                    status=CheckStatus.OK,
                    note="인기 도메인 100만 위 안에 없습니다 — 권위 자료 없음(신규·무링크로 추정).",
                ),
            )
        else:
            out[domain] = Authority(
                check=CheckState(
                    status=CheckStatus.OK,
                    note=f"인기 도메인 {rank:,}위 ({source}, 키 없이 받은 공개 자료).",
                ),
                page_rank=rank_to_score(rank),
                rank=rank,
            )
    return out
