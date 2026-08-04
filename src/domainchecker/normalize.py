"""Input parsing: split, strip scheme/www, de-duplicate, cap at 1000."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

MAX_DOMAINS = 1000

_SPLIT = re.compile(r"[\s,;]+")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
_LABEL = r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
_VALID = re.compile(rf"^(?:{_LABEL}\.)+[a-z]{{2,63}}$")


class ParseResult(BaseModel):
    domains: list[str] = Field(default_factory=list)
    invalid: list[str] = Field(default_factory=list)
    duplicates: int = 0
    truncated: int = 0  # 상한 초과로 잘려나간 개수

    @property
    def notice(self) -> str:
        """Korean summary for the UI."""
        parts = [f"{len(self.domains)}개 검사 예정"]
        if self.duplicates:
            parts.append(f"중복 {self.duplicates}개 제거")
        if self.invalid:
            parts.append(f"형식 오류 {len(self.invalid)}개 제외")
        if self.truncated:
            parts.append(f"상한 {MAX_DOMAINS}개 초과분 {self.truncated}개는 나눠서 검사하세요")
        return " · ".join(parts)


def normalize_domain(raw: str) -> str | None:
    """Return the bare registrable host, or None when it is not a domain."""
    value = (raw or "").strip().strip("\"'<>()[]").lower()
    if not value:
        return None
    value = _SCHEME.sub("", value).lstrip("/")
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in value:  # e-mail pasted by mistake
        value = value.rsplit("@", 1)[1]
    value = value.split(":", 1)[0]  # drop port
    value = value.strip(".")
    if not value:
        return None
    if not value.isascii():
        try:
            value = value.encode("idna").decode("ascii")
        except (UnicodeError, ValueError):
            return None
    value = value.removeprefix("www.")
    if len(value) > 253 or not _VALID.match(value):
        return None
    return value


def parse_domains(raw: str, limit: int = MAX_DOMAINS) -> ParseResult:
    """Parse a pasted blob (newline/comma separated) into a clean domain list."""
    result = ParseResult()
    seen: set[str] = set()
    for token in _SPLIT.split(raw or ""):
        if not token.strip():
            continue
        domain = normalize_domain(token)
        if domain is None:
            result.invalid.append(token.strip())
            continue
        if domain in seen:
            result.duplicates += 1
            continue
        seen.add(domain)
        result.domains.append(domain)
    if len(result.domains) > limit:
        result.truncated = len(result.domains) - limit
        result.domains = result.domains[:limit]
    return result
