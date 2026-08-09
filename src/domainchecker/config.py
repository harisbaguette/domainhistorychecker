"""User config stored at ~/.domainchecker/config.json (plain text, local single-user tool).

배포한 서버(서버리스)는 자기 폴더가 잠깐 있다 지워져서 저 파일이 남지 않는다.
그래서 키는 서버 설정값(환경변수)에서도 읽어 온다 — 파일에 적힌 키가 먼저,
비어 있을 때만 환경변수 값을 쓴다.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

from pydantic import BaseModel

CONFIG_DIR = Path.home() / ".domainchecker"
CONFIG_PATH = CONFIG_DIR / "config.json"

# 키 이름 → 서버 설정값(환경변수) 이름. 키는 AI 분석용 OpenRouter 딱 하나다 —
# 나머지 검사는 전부 키 없는 공개 자료로 돈다(관리할 키를 늘리지 않는다).
KEY_ENV_NAMES = {
    "openrouter": "OPENROUTER_API_KEY",
}

# AI model fallback chain — 기본 모델이 실패하면 아래 순서로 갈아탄다.
# 2026-08-04 live probe: "deepseek-v4-flash-latest" is not a real OpenRouter id
# (400) — the un-suffixed "deepseek-v4-flash" is, so the middle rung uses that.
MODEL_CHAIN = (
    "deepseek/deepseek-v4-flash-0731",
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v3.2",
)


class ApiKeys(BaseModel):
    openrouter: str = ""


class Config(BaseModel):
    keys: ApiKeys = ApiKeys()
    model: str = MODEL_CHAIN[0]
    speed_mode: str = "adaptive"  # "adaptive" (30/min 시작) | "safe" (12/min 고정)
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
        """✅ 판정을 막는 키. 이제 없다 — 필수 검사는 전부 키 없이 돈다."""
        return []

    def free_fallbacks(self) -> list[str]:
        """키가 없어 대신 도는 것들(정확도만 조금 낮다)."""
        if not self.keys.openrouter:
            return ["과거 내용 판단 — AI 없이 규칙 검사(기계적 판단)만으로 봅니다"]
        return []


def env_keys() -> dict[str, str]:
    """서버 설정값(환경변수)에 들어 있는 키만 모아서 돌려준다."""
    found = {}
    for name, env_name in KEY_ENV_NAMES.items():
        value = os.environ.get(env_name, "").strip()
        if value:
            found[name] = value
    return found


def _fill_from_env(config: Config) -> Config:
    """비어 있는 키만 서버 설정값으로 채운다(사용자가 직접 넣은 키가 우선)."""
    for name, value in env_keys().items():
        if not getattr(config.keys, name):
            setattr(config.keys, name, value)
    return config


def load(path: Path | None = None) -> Config:
    """Read config; a missing or damaged file yields safe defaults."""
    target = Path(path) if path else CONFIG_PATH
    if not target.exists():
        return _fill_from_env(Config())
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _fill_from_env(Config())
    if not isinstance(raw, dict):
        return _fill_from_env(Config())
    try:
        return _fill_from_env(Config.model_validate(raw))
    except ValueError:
        return _fill_from_env(Config())


def save(config: Config, path: Path | None = None) -> Path:
    """Write the config file, asking for owner-only permissions (it holds API keys).

    `chmod` is a no-op on Windows, so the file is only as private as the user
    profile folder there — a plain local file is this tool's storage model.
    """
    target = Path(path) if path else CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump()
    # 서버 설정값에서 온 키는 파일에 다시 적지 않는다 — 나중에 키를 바꿨을 때
    # 파일에 남은 옛 키가 이겨서 새 키가 안 먹는 일을 막는다.
    for name, value in env_keys().items():
        if data["keys"].get(name) == value:
            data["keys"][name] = ""
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    with contextlib.suppress(OSError):
        target.chmod(0o600)
    return target


def data_dir(config: Config) -> Path:
    base = Path(config.data_dir) if config.data_dir else Path.cwd() / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base
