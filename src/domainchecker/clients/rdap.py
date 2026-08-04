"""Registration data: RDAP bootstrap first, raw whois (port 43) as the fallback."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable

import httpx

from ..models import CheckState, CheckStatus, Registration

RDAP_URL = "https://rdap.org/domain/{domain}"

WHOIS_SERVERS = {
    "kr": "whois.kr",
    "com": "whois.verisign-grs.com",
    "net": "whois.verisign-grs.com",
    "org": "whois.pir.org",
    "info": "whois.afilias.net",
    "io": "whois.nic.io",
    "co": "whois.nic.co",
    "me": "whois.nic.me",
    "dev": "whois.nic.google",
    "app": "whois.nic.google",
    "xyz": "whois.nic.xyz",
    "shop": "whois.nic.shop",
    "jp": "whois.jprs.jp",
    "cn": "whois.cnnic.cn",
    "uk": "whois.nic.uk",
    "de": "whois.denic.de",
}

_CREATED_KEYS = ("creation date", "created", "registered date", "registration time", "등록일")
_EXPIRES_KEYS = (
    "registry expiry date",
    "expiration date",
    "expires on",
    "expiry date",
    "사용 종료일",
    "만료일",
)
_REGISTRAR_KEYS = ("registrar", "sponsoring registrar", "등록대행자")

# RDAP/EPP status -> 사용자에게 보여줄 취득 상태.
# 비교는 영문자만 남긴 압축형으로 한다("redemptionPeriod"와 "redemption period"를 함께 잡기 위해).
# 순서가 곧 우선순위다. redemptionPeriod와 pendingDelete가 함께 오면 아직
# 원주인이 되찾을 수 있는 복원 기간이므로 "곧 등록 가능"으로 보여 주면 안 된다.
_ACQUISITION = [
    ("redemptionperiod", "복원 기간(경매·복원 대상)"),
    ("pendingdelete", "삭제 대기(곧 등록 가능)"),
    ("pendingrestore", "복원 진행 중"),
    ("autorenewperiod", "자동 갱신 기간"),
    ("transferprohibited", "등록 중(이전 잠금)"),
    ("clienthold", "등록 중(보류)"),
    ("serverhold", "등록 중(보류)"),
    ("inactive", "등록 중(네임서버 없음)"),
    ("active", "등록 중"),
]


class RdapClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        whois_query: Callable[[str, str], Awaitable[str]] | None = None,
    ) -> None:
        self.http = http
        self.whois_query = whois_query or whois_lookup

    async def fetch(self, domain: str) -> Registration:
        registration = await self._rdap(domain)
        if registration.check.ok:
            return registration
        return await self._whois(domain)

    async def _rdap(self, domain: str) -> Registration:
        result = Registration(source="rdap")
        try:
            response = await self.http.get(
                RDAP_URL.format(domain=domain),
                headers={"Accept": "application/rdap+json"},
                follow_redirects=True,
            )
        except httpx.HTTPError:
            result.check = CheckState(status=CheckStatus.UNCHECKED, note="RDAP 접속 실패.")
            return result
        if response.status_code == 404:
            # 등록 기록 없음 = 지금 등록 가능한 상태.
            result.acquisition = "구매 가능(미등록)"
            result.check = CheckState(status=CheckStatus.OK, note="등록 기록이 없습니다.")
            return result
        if response.status_code != 200:
            result.check = CheckState(
                status=CheckStatus.UNCHECKED, note=f"RDAP 응답 오류({response.status_code})."
            )
            return result
        try:
            data = response.json()
        except ValueError:
            result.check = CheckState(status=CheckStatus.UNCHECKED, note="RDAP 응답 해석 실패.")
            return result

        events = data.get("events") or []
        registrations = []
        for event in events:
            action = str(event.get("eventAction", "")).lower()
            date = str(event.get("eventDate", ""))[:10]
            if action == "registration":
                registrations.append(date)
            elif action == "expiration":
                result.expires = date
        if registrations:
            result.created = min(registrations)
            result.redropped = len(registrations) > 1
        result.statuses = [str(s) for s in (data.get("status") or [])]
        result.registrar = _rdap_registrar(data)
        result.acquisition = acquisition_label(result.statuses, bool(result.created))
        result.check = CheckState(status=CheckStatus.OK)
        return result

    async def _whois(self, domain: str) -> Registration:
        result = Registration(source="whois")
        tld = domain.rsplit(".", 1)[-1]
        server = WHOIS_SERVERS.get(tld, f"{tld}.whois-servers.net")
        try:
            text = await self.whois_query(domain, server)
        except (TimeoutError, OSError):
            result.check = CheckState(
                status=CheckStatus.UNCHECKED, note="RDAP·whois 모두 조회에 실패했습니다."
            )
            return result
        if not text.strip():
            result.check = CheckState(status=CheckStatus.UNCHECKED, note="whois 응답이 비었습니다.")
            return result
        return parse_whois(text, result)


async def whois_lookup(domain: str, server: str, timeout: float = 10.0) -> str:
    """Raw whois over TCP/43 with EUC-KR auto-detection (KRNIC responses)."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(server, 43), timeout)
    try:
        query = f"{domain}\r\n"
        if server == "whois.kr":
            query = f"{domain}/e\r\n"  # /e = English section as well
        writer.write(query.encode("utf-8"))
        await writer.drain()
        payload = await asyncio.wait_for(reader.read(-1), timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
    return decode_whois(payload)


def decode_whois(payload: bytes) -> str:
    """UTF-8 first; Korean registries still answer in EUC-KR."""
    for encoding in ("utf-8", "euc-kr"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def parse_whois(text: str, result: Registration | None = None) -> Registration:
    result = result or Registration(source="whois")
    lowered_lines = [line.strip() for line in text.splitlines() if ":" in line]
    statuses: list[str] = []
    for line in lowered_lines:
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue
        if not result.created and any(k == key or key.startswith(k) for k in _CREATED_KEYS):
            result.created = _clean_date(value)
        elif not result.expires and any(k == key or key.startswith(k) for k in _EXPIRES_KEYS):
            result.expires = _clean_date(value)
        elif not result.registrar and any(k == key for k in _REGISTRAR_KEYS):
            result.registrar = value
        elif key in ("domain status", "status"):
            statuses.append(value)
    result.statuses = statuses
    if re.search(r"no match|not found|검색된 자료가 없습니다|no entries found", text, re.IGNORECASE):
        result.acquisition = "구매 가능(미등록)"
        result.check = CheckState(status=CheckStatus.OK, note="등록 기록이 없습니다.")
        return result
    result.acquisition = acquisition_label(statuses, bool(result.created))
    result.check = CheckState(status=CheckStatus.OK)
    return result


def acquisition_label(statuses: list[str], registered: bool) -> str:
    compact = re.sub(r"[^a-z]", "", " ".join(statuses).lower())
    for needle, label in _ACQUISITION:
        if needle in compact:
            return label
    if any(re.sub(r"[^a-z]", "", s.lower()) == "ok" for s in statuses):
        return "등록 중"
    if registered:
        return "등록 중"
    return "알 수 없음"


def _rdap_registrar(data: dict) -> str:
    for entity in data.get("entities") or []:
        roles = [str(r).lower() for r in entity.get("roles") or []]
        if "registrar" not in roles:
            continue
        for item in entity.get("vcardArray", [None, []])[1] or []:
            if isinstance(item, list) and len(item) >= 4 and item[0] == "fn":
                return str(item[3])
    return ""


def _clean_date(value: str) -> str:
    """Registries print dates every which way; keep YYYY-MM-DD when we can find it."""
    match = re.search(r"(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})", value)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return value[:32]
