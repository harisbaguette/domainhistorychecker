"""User config stored at ~/.domainchecker/config.json (plain text, local single-user tool)."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from pydantic import BaseModel

CONFIG_DIR = Path.home() / ".domainchecker"
CONFIG_PATH = CONFIG_DIR / "config.json"

# AI model fallback chain, fixed by PLAN §3.
# 2026-08-04 live probe: "deepseek-v4-flash-latest" is not a real OpenRouter id
# (400) — the un-suffixed "deepseek-v4-flash" is, so the middle rung uses that.
MODEL_CHAIN = (
    "deepseek/deepseek-v4-flash-0731",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v3.2",
)


class ApiKeys(BaseModel):
    openrouter: str = ""
    serper: str = ""
    openpagerank: str = ""
    safebrowsing: str = ""
    virustotal: str = ""


class Config(BaseModel):
    keys: ApiKeys = ApiKeys()
    model: str = MODEL_CHAIN[0]
    speed_mode: str = "adaptive"  # "adaptive" (30/min 시작) | "safe" (12/min 고정)
    enable_safebrowsing: bool = True
    enable_virustotal: bool = False
    enable_capture: bool = True  # 화면 캡쳐(전 검사 완료 후 후행 실행)
    max_domains: int = 1000
    max_snapshots: int = 6
    ai_input_limit: int = 40_000  # 도메인당 AI 입력 상한(문자)
    snapshot_text_limit: int = 6_000
    concurrency: int = 4
    cache_days: int = 7  # 저장분을 며칠까지 믿을지(0이면 무기한). 지나면 자동으로 다시 검사
    data_dir: str = ""  # 비우면 프로젝트 ./data

    @property
    def start_rpm(self) -> int:
        return 12 if self.speed_mode == "safe" else 30

    def model_chain(self) -> list[str]:
        """Chosen model first, then the remaining fixed fallbacks."""
        chain = [self.model] if self.model else []
        chain += [m for m in MODEL_CHAIN if m != self.model]
        return chain

    def missing_required_keys(self) -> list[str]:
        """✅ 판정을 막는 키만. 색인·권위는 키 없이 도는 무료 대체 검사가 있다."""
        missing = []
        if not self.keys.openrouter:
            missing.append("OpenRouter (AI 분석)")
        return missing

    def free_fallbacks(self) -> list[str]:
        """키 대신 무료 공개 자료로 도는 검사들(정확도만 조금 낮다)."""
        notes = []
        if not self.keys.serper:
            notes.append("색인 검사 — 커먼크롤 공개 색인과 현재 페이지로 대신 봅니다")
        if not self.keys.openpagerank:
            notes.append("권위 점수 — Tranco 인기 도메인 100만 목록으로 대신 봅니다")
        return notes


def load(path: Path | None = None) -> Config:
    """Read config; a missing or damaged file yields safe defaults."""
    target = Path(path) if path else CONFIG_PATH
    if not target.exists():
        return Config()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Config()
    if not isinstance(raw, dict):
        return Config()
    try:
        return Config.model_validate(raw)
    except ValueError:
        return Config()


def save(config: Config, path: Path | None = None) -> Path:
    """Write the config file, asking for owner-only permissions (it holds API keys).

    `chmod` is a no-op on Windows, so the file is only as private as the user
    profile folder there — PLAN §3 already assumes plain local storage.
    """
    target = Path(path) if path else CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(config.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp.replace(target)
    with contextlib.suppress(OSError):
        target.chmod(0o600)
    return target


def data_dir(config: Config) -> Path:
    base = Path(config.data_dir) if config.data_dir else Path.cwd() / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base
