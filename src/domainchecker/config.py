"""User config stored at ~/.domainchecker/config.json (plain text, local single-user tool)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel

CONFIG_DIR = Path.home() / ".domainchecker"
CONFIG_PATH = CONFIG_DIR / "config.json"

# AI model fallback chain, fixed by PLAN §3.
MODEL_CHAIN = (
    "deepseek/deepseek-v4-flash-0731",
    "deepseek/deepseek-v4-flash-latest",
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
    max_domains: int = 1000
    max_snapshots: int = 6
    ai_input_limit: int = 40_000  # 도메인당 AI 입력 상한(문자)
    snapshot_text_limit: int = 6_000
    concurrency: int = 4
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
        """Required-check keys that are absent (✅ 판정 불가 사유)."""
        missing = []
        if not self.keys.serper:
            missing.append("Serper (색인 검사)")
        if not self.keys.openpagerank:
            missing.append("Open PageRank (권위 점수)")
        if not self.keys.openrouter:
            missing.append("OpenRouter (AI 분석)")
        return missing


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
    """Write config with owner-only permissions (it holds API keys)."""
    target = Path(path) if path else CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(config.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def data_dir(config: Config) -> Path:
    base = Path(config.data_dir) if config.data_dir else Path.cwd() / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base
