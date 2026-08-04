"""Spamhaus DBL lookup over the system resolver.

DBL answers 127.0.1.x for a listed domain, NXDOMAIN for a clean one, and
127.255.255.x when our resolver is blocked (public DNS, quota) — that is not a
clean answer, so it is recorded as 미확인.
"""

from __future__ import annotations

import dns.asyncresolver
import dns.exception
import dns.resolver

from ..models import CheckState, CheckStatus, Reputation

ZONE = "dbl.spamhaus.org"

# 127.0.1.x return codes -> Korean meaning (abridged official list).
_CODES = {
    "127.0.1.2": "스팸 도메인",
    "127.0.1.4": "피싱 도메인",
    "127.0.1.5": "악성코드 도메인",
    "127.0.1.6": "봇넷 C&C 도메인",
    "127.0.1.102": "스팸 발송 이력(악용 의심)",
    "127.0.1.103": "피싱 이력(악용 의심)",
    "127.0.1.104": "악성코드 이력(악용 의심)",
    "127.0.1.105": "봇넷 이력(악용 의심)",
}


async def check(domain: str, resolver=None) -> Reputation:
    """Return listed/clean/미확인 for one domain."""
    result = Reputation()
    resolver = resolver or dns.asyncresolver.get_default_resolver()
    try:
        answer = await resolver.resolve(f"{domain}.{ZONE}", "A")
    except dns.resolver.NXDOMAIN:
        result.check = CheckState(status=CheckStatus.OK, note="블랙리스트에 없습니다.")
        return result
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        result.check = CheckState(status=CheckStatus.OK, note="블랙리스트에 없습니다.")
        return result
    except (dns.exception.DNSException, OSError) as exc:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note=f"블랙리스트 조회 실패({type(exc).__name__})."
        )
        return result

    addresses = [str(rdata) for rdata in answer]
    blocked = [a for a in addresses if a.startswith("127.255.")]
    listed = [a for a in addresses if a.startswith("127.0.1.")]
    if blocked and not listed:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED,
            note="조회가 차단되었습니다(공용 DNS 사용 또는 한도 초과) — 미확인.",
        )
        return result
    if listed:
        result.listed = True
        result.codes = [_CODES.get(a, f"등재({a})") for a in listed]
        result.check = CheckState(
            status=CheckStatus.OK, note="블랙리스트에 등재되어 있습니다: " + ", ".join(result.codes)
        )
        return result
    result.check = CheckState(
        status=CheckStatus.UNCHECKED, note=f"해석할 수 없는 응답({', '.join(addresses)}) — 미확인."
    )
    return result
