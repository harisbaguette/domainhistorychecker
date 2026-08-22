"""Per-domain result cache — the basis for stop/resume."""

from __future__ import annotations

import json
import re
from pathlib import Path

_SAFE = re.compile(r"[^a-z0-9.\-]")


def safe_name(domain: str) -> str:
    """도메인 이름을 파일 이름으로 쓸 수 있게 다듬는다 — 저장분·사진·보고서 공용.

    셋이 각자 다듬으면 자른 길이가 어긋나서, 아주 긴 도메인은 저장분을 지워도
    화면 사진만 남는다(지우기가 사진 이름을 저장분 이름으로 찾기 때문). 그래서
    다듬는 자리는 여기 한 곳뿐이다.
    """
    return _SAFE.sub("_", domain.lower())[:120] or "unknown"


def cache_dir(base: Path | str) -> Path:
    path = Path(base) / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(domain: str, base: Path | str) -> Path:
    return cache_dir(base) / f"{safe_name(domain)}.json"


def load(domain: str, base: Path | str) -> dict | None:
    """Return the cached payload, or None when absent or damaged."""
    path = cache_path(domain, base)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save(domain: str, payload: dict, base: Path | str) -> Path:
    path = cache_path(domain, base)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def cached_domains(base: Path | str) -> set[str]:
    return {p.stem for p in cache_dir(base).glob("*.json")}
