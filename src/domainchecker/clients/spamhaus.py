"""Spamhaus DBL lookup — 명단을 관리하는 서버에게 **직접** 묻는다.

왜 직접 묻는가(2026-08-23 실측):
  이 검사는 이름 묻기(DNS) 방식이라, 보통은 컴퓨터에 설정된 이름 서버를 거쳐
  간다. 그런데 스팸하우스는 **여러 사람이 함께 쓰는 이름 서버**(구글 8.8.8.8,
  클라우드플레어 1.1.1.1, 통신사 것까지 포함)로 들어오는 물음은 남용을 막으려고
  거절하고, 답 대신 127.255.255.x 라는 "너는 공용 서버로 물었다" 표를 보낸다.
  이 컴퓨터는 통신사 이름 서버(210.220.163.82)를 쓰는데도 dbltest.com·example.com
  둘 다 127.255.255.254 가 왔다 — 즉 사용자가 무엇을 고르든 이 길로는 거의 항상
  막힌다. 저장된 결과 17건에서 스팸하우스가 100% 미확인이던 원인이 이것이다.

  그래서 중간 서버를 거치지 않고, 이 명단을 실제로 들고 있는 서버
  (a~e.gns.spamhaus.org)에게 곧바로 묻는다. 같은 컴퓨터에서 실측:
  dbltest.com → 127.0.1.2(등재), example.com → 없음(NXDOMAIN). 정상 동작.

답 읽는 법: 127.0.1.x = 명단에 있음, 없다는 답(NXDOMAIN) = 깨끗함,
127.255.255.x = 우리를 거절한 것이라 "확인"이 아니다.
"""

from __future__ import annotations

import asyncio

import dns.asyncresolver
import dns.exception
import dns.resolver

from ..models import CheckState, CheckStatus, Reputation

ZONE = "dbl.spamhaus.org"

QUERY_TIMEOUT = 6.0  # 한 서버를 기다리는 시간(초)
QUERY_LIFETIME = 12.0  # 한 번의 물음이 통째로 걸릴 수 있는 시간(초)

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

_BLOCKED_NOTE = (
    "스팸하우스가 우리 물음을 거절했습니다 — 여러 사람이 함께 쓰는 이름 서버(공용 DNS)로 "
    "들어온 물음은 받지 않기 때문입니다."
)

# 명단 서버 주소는 한 번만 알아내고 프로그램이 도는 동안 기억한다 —
# 도메인마다 다시 찾으면 그것만으로 요청이 두 배가 된다.
_servers: list[str] = []
_servers_lock = asyncio.Lock()


def reset_servers_cache() -> None:
    """테스트용 — 기억해 둔 명단 서버 주소를 지운다."""
    _servers.clear()


async def zone_servers() -> list[str]:
    """이 명단을 실제로 들고 있는 서버들의 주소. 못 알아내면 빈 목록."""
    async with _servers_lock:
        if _servers:
            return list(_servers)
        try:
            answer = await dns.asyncresolver.resolve(ZONE, "NS", lifetime=QUERY_LIFETIME)
        except (dns.exception.DNSException, OSError):
            return []
        names = [str(row.target).rstrip(".") for row in answer]
        found: list[str] = []
        for name in names[:4]:  # 서너 곳이면 충분하다(한 곳이 쉬어도 나머지가 답한다)
            try:
                addresses = await dns.asyncresolver.resolve(name, "A", lifetime=QUERY_LIFETIME)
            except (dns.exception.DNSException, OSError):
                continue
            found += [str(row) for row in addresses]
        _servers.extend(dict.fromkeys(found))
        return list(_servers)


def _direct_resolver(servers: list[str]) -> dns.asyncresolver.Resolver:
    """명단 서버에게 곧바로 묻는 전용 물음 통로(컴퓨터 설정을 타지 않는다)."""
    resolver = dns.asyncresolver.Resolver(configure=False)
    resolver.nameservers = list(servers)
    resolver.timeout = QUERY_TIMEOUT
    resolver.lifetime = QUERY_LIFETIME
    return resolver


def _read(addresses: list[str]) -> Reputation:
    """받은 답을 등재/거절/모름으로 읽는다."""
    result = Reputation()
    blocked = [a for a in addresses if a.startswith("127.255.")]
    listed = [a for a in addresses if a.startswith("127.0.1.")]
    if listed:
        result.listed = True
        result.codes = [_CODES.get(a, f"등재({a})") for a in listed]
        result.check = CheckState(
            status=CheckStatus.OK, note="블랙리스트에 등재되어 있습니다: " + ", ".join(result.codes)
        )
        return result
    if blocked:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note=_BLOCKED_NOTE)
        return result
    result.check = CheckState(
        status=CheckStatus.UNCHECKED, note=f"해석할 수 없는 응답({', '.join(addresses)}) — 미확인."
    )
    return result


async def ask(domain: str, resolver) -> Reputation:
    """물음 통로 하나로 한 번 물어보고 답을 읽는다."""
    try:
        answer = await resolver.resolve(f"{domain}.{ZONE}", "A")
    except dns.resolver.NXDOMAIN:
        result = Reputation()
        result.check = CheckState(status=CheckStatus.OK, note="블랙리스트에 없습니다.")
        return result
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        # NXDOMAIN만 "없음"이다. 답이 비었거나 응답할 서버가 없는 것은 확인이 아니다.
        result = Reputation()
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note="블랙리스트 조회 실패 — 미확인."
        )
        return result
    except (dns.exception.DNSException, OSError) as exc:
        result = Reputation()
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note=f"블랙리스트 조회 실패({type(exc).__name__})."
        )
        return result
    return _read([str(rdata) for rdata in answer])


def _unavailable(last: Reputation | None) -> Reputation:
    """끝내 못 물어봤을 때 — 이건 도메인 사정이 아니라 **이 컴퓨터 사정**이다.

    그래서 `optional` 표를 달아 둔다. 이 표가 붙은 검사는 최종 판정(초록/노랑/빨강)을
    막지 않는다 — 우리 쪽 사정 하나로 온 세상 도메인이 영원히 노랑에 갇히면
    그게 바로 "시스템이 고장 난 것처럼 보이는" 상태다. 대신 "안 돌림" 목록에
    이름이 그대로 남아, 못 본 것을 깨끗한 것으로 읽는 일은 없다.
    """
    result = Reputation()
    why = (last.check.note if last is not None else "") or "이름 묻기(DNS)가 막혔습니다."
    result.check = CheckState(
        status=CheckStatus.NOT_RUN,
        note=(
            f"스팸 블랙리스트는 이 컴퓨터의 인터넷 환경 때문에 확인하지 못했습니다 — {why} "
            "이 검사만 빼고 나머지 검사로 판정했습니다(깨끗하다는 뜻은 아닙니다)."
        ),
        optional=True,
    )
    return result


async def check(domain: str, resolver=None) -> Reputation:
    """Return listed/clean/미확인 for one domain.

    `resolver`를 넘기면 그 통로만 쓴다(시험·특수 설정용). 안 넘기면 명단 서버에게
    직접 묻고, 그게 막히면 컴퓨터 설정 통로로 한 번 더 물어본 뒤, 둘 다 안 되면
    "이 컴퓨터에서는 못 하는 검사"로 적는다.
    """
    if resolver is not None:
        return await ask(domain, resolver)

    last: Reputation | None = None
    servers = await zone_servers()
    if servers:
        result = await ask(domain, _direct_resolver(servers))
        if result.check.ok:
            return result
        last = result
    # 직통이 막히는 망(회사 방화벽 등)도 있다 — 컴퓨터 설정 통로로 한 번 더.
    try:
        system = dns.asyncresolver.get_default_resolver()
    except Exception:  # noqa: BLE001 — 이름 서버 설정을 못 읽어도 검사는 계속한다
        return _unavailable(last)
    result = await ask(domain, system)
    if result.check.ok:
        return result
    return _unavailable(result if last is None else last)
