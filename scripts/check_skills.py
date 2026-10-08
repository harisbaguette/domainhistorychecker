#!/usr/bin/env python3
"""프로젝트 스킬의 이름, 호출 정보와 로컬 문서 연결을 오프라인 검사한다."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

VERSION = "0.1.0"
EXPECTED_SKILLS = ("kh-skill-domain", "kh-skill-branding")
LINK = re.compile(r"\[[^\]\n]+\]\(([^)\n]+)\)")


def check(root: Path) -> tuple[list[str], int]:
    errors: list[str] = []
    documents: list[Path] = []

    def read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"{path}: 읽기 실패 ({exc})")
            return ""

    for name in EXPECTED_SKILLS:
        folder = root / ".agents" / "skills" / name
        path = folder / "SKILL.md"
        body = read(path)
        match = re.match(r"\A---\n(.*?)\n---\n", body, re.DOTALL)
        if not match:
            errors.append(f"{path}: YAML 머리말이 필요합니다")
        else:
            # 이 프로젝트의 두 진입점은 단일 행 name/description만 사용한다.
            fields = {}
            for line in match[1].splitlines():
                key, separator, value = line.partition(":")
                if not separator or key in fields:
                    errors.append(f"{path}: 잘못되거나 중복된 머리말 항목")
                    continue
                fields[key] = value.strip()
            if fields.get("name") != name:
                errors.append(f"{path}: name이 폴더 이름과 다릅니다")
            if not fields.get("description"):
                errors.append(f"{path}: description이 비어 있습니다")
            if set(fields) - {"name", "description"}:
                errors.append(f"{path}: 새 머리말 형식에 맞게 검증기를 갱신해야 합니다")

        ui_path = folder / "agents" / "openai.yaml"
        ui = read(ui_path)
        values = {}
        for line in ui.splitlines():
            if line == "interface:" or not line.strip():
                continue
            item = re.fullmatch(r"  ([a-z_]+): (.+)", line)
            if not item:
                errors.append(f"{ui_path}: 지원하지 않는 메타데이터 형식")
                continue
            key, value = item.groups()
            if key in values:
                errors.append(f"{ui_path}: 중복 항목 {key}")
            try:
                values[key] = json.loads(value)
            except ValueError:
                errors.append(f"{ui_path}: {key}는 큰따옴표 문자열이어야 합니다")
        for key in ("display_name", "short_description", "default_prompt"):
            if not isinstance(values.get(key), str) or not values[key].strip():
                errors.append(f"{ui_path}: {key} 문자열이 필요합니다")
        short = values.get("short_description")
        if isinstance(short, str) and not 25 <= len(short) <= 64:
            errors.append(f"{ui_path}: short_description은 25~64자여야 합니다")
        prompt = values.get("default_prompt")
        if isinstance(prompt, str) and f"${name}" not in prompt:
            errors.append(f"{ui_path}: default_prompt에 ${name} 호출이 필요합니다")
        documents.extend(folder.rglob("*.md"))

    documents.extend(root / name for name in ("AGENTS.md", "CLAUDE.md", "docs/domain-branding.md"))
    for path in documents:
        body = read(path)
        for raw in LINK.findall(body):
            target = raw.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            dest = (path.parent / unquote(parsed.path)).resolve()
            if not dest.exists():
                errors.append(f"{path}: 연결된 파일이 없습니다: {target}")
    return errors, len(documents)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="프로젝트 루트")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args()
    errors, count = check(args.root.resolve())
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print(f"스킬 {len(EXPECTED_SKILLS)}개와 문서 {count}개의 구조·호출 정보·로컬 연결이 정상입니다.")
    print("외부 서비스 접속과 실제 모델의 스킬 선택·판단은 이 검사에 포함되지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
