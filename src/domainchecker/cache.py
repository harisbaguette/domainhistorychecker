"""Per-domain result cache — the basis for stop/resume."""

from __future__ import annotations

import json
import re
from pathlib import Path

_SAFE = re.compile(r"[^a-z0-9.\-]")


def cache_dir(base: Path | str) -> Path:
    path = Path(base) / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(domain: str, base: Path | str) -> Path:
    name = _SAFE.sub("_", domain.lower())[:120] or "unknown"
    return cache_dir(base) / f"{name}.json"


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
