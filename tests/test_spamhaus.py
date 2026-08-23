import dns.exception
import dns.resolver

from domainchecker.clients import spamhaus
from domainchecker.models import CheckStatus


class FakeResolver:
    def __init__(self, answer=None, error=None):
        self.answer = answer or []
        self.error = error
        self.asked = None

    async def resolve(self, qname, rdtype):
        self.asked = (qname, rdtype)
        if self.error:
            raise self.error
        return self.answer


async def test_nxdomain_means_clean():
    resolver = FakeResolver(error=dns.resolver.NXDOMAIN())
    result = await spamhaus.check("example.com", resolver)

    assert resolver.asked == ("example.com.dbl.spamhaus.org", "A")
    assert result.check.status is CheckStatus.OK
    assert result.listed is False


async def test_listed_code_is_translated():
    result = await spamhaus.check("bad.com", FakeResolver(answer=["127.0.1.4"]))
    assert result.check.status is CheckStatus.OK
    assert result.listed is True
    assert result.codes == ["피싱 도메인"]


async def test_no_nameservers_is_unchecked_not_clean():
    """NXDOMAIN만 '없음'이다 — 답할 서버가 없는 것을 깨끗함으로 읽으면 안 된다."""
    result = await spamhaus.check("x.com", FakeResolver(error=dns.resolver.NoNameservers()))
    assert result.check.status is CheckStatus.UNCHECKED
    assert result.listed is False
    assert "미확인" in result.check.note


async def test_no_answer_is_unchecked_not_clean():
    result = await spamhaus.check("x.com", FakeResolver(error=dns.resolver.NoAnswer()))
    assert result.check.status is CheckStatus.UNCHECKED
    assert result.listed is False


async def test_blocked_query_is_unchecked_not_clean():
    result = await spamhaus.check("bad.com", FakeResolver(answer=["127.255.255.254"]))
    assert result.check.status is CheckStatus.UNCHECKED
    assert result.listed is False
    # 왜 거절당했는지 사실만 적는다. "DNS를 통신사 자동으로 바꾸면 된다"는 옛 안내는
    # 실측(2026-08-23)으로 거짓이었다 — 통신사 이름 서버도 똑같이 거절당한다.
    assert "공용 DNS" in result.check.note


async def test_dns_failure_is_unchecked():
    result = await spamhaus.check("x.com", FakeResolver(error=dns.exception.Timeout()))
    assert result.check.status is CheckStatus.UNCHECKED


async def test_unknown_answer_is_unchecked():
    result = await spamhaus.check("x.com", FakeResolver(answer=["10.0.0.1"]))
    assert result.check.status is CheckStatus.UNCHECKED
    assert result.listed is False


# ── 공용 이름 서버에 막히던 버그(2026-08-23) 회귀 시험 ──────────────────────
# 저장된 결과 17건에서 이 검사가 100% 미확인이었다. 원인은 컴퓨터에 설정된 이름
# 서버를 거쳐 물어봤기 때문이고(여럿이 함께 쓰는 서버라 스팸하우스가 거절한다),
# 고침은 명단을 들고 있는 서버에게 곧바로 묻는 것이다.


def _routes(monkeypatch, *, servers, direct=None, system=None):
    """직통 서버 목록과 두 통로를 갈아 끼운다."""
    monkeypatch.setattr(spamhaus, "zone_servers", lambda: _done(servers))
    monkeypatch.setattr(spamhaus, "_direct_resolver", lambda _s: direct)
    monkeypatch.setattr(
        spamhaus.dns.asyncresolver, "get_default_resolver", lambda: system or FakeResolver()
    )


async def _done(value):
    return value


async def test_it_asks_the_list_servers_directly_not_the_computers_dns(monkeypatch):
    """고침의 핵심 — 중간 서버를 거치지 않고 명단 서버에게 바로 묻는다."""
    direct = FakeResolver(answer=["127.0.1.2"])
    system = FakeResolver(answer=["127.255.255.254"])
    _routes(monkeypatch, servers=["1.2.3.4"], direct=direct, system=system)

    result = await spamhaus.check("bad.com")

    assert direct.asked == ("bad.com.dbl.spamhaus.org", "A")
    assert system.asked is None  # 컴퓨터 설정 통로는 건드리지도 않았다
    assert result.check.status is CheckStatus.OK
    assert result.listed is True


async def test_direct_route_blocked_falls_back_to_the_computers_dns(monkeypatch):
    """회사 방화벽 등으로 직통이 막히는 망도 있다 — 그때는 예전 길로 한 번 더."""
    direct = FakeResolver(error=dns.exception.Timeout())
    system = FakeResolver(error=dns.resolver.NXDOMAIN())
    _routes(monkeypatch, servers=["1.2.3.4"], direct=direct, system=system)

    result = await spamhaus.check("example.com")

    assert system.asked == ("example.com.dbl.spamhaus.org", "A")
    assert result.check.status is CheckStatus.OK
    assert result.listed is False


async def test_both_routes_blocked_marks_it_skippable_not_a_domain_fault(monkeypatch):
    """두 길 다 막히면 '이 컴퓨터 사정'으로 적고 판정을 막지 않는다.

    이 표(optional)가 없으면 우리 쪽 인터넷 사정 하나로 모든 도메인이 영원히
    노랑에 갇힌다 — 운영자가 본 "전체가 고장 난 것 같다"가 바로 그 화면이다.
    """
    _routes(
        monkeypatch,
        servers=["1.2.3.4"],
        direct=FakeResolver(answer=["127.255.255.254"]),
        system=FakeResolver(answer=["127.255.255.254"]),
    )

    result = await spamhaus.check("example.com")

    assert result.check.status is CheckStatus.NOT_RUN
    assert result.check.optional is True
    assert result.listed is False
    assert "깨끗하다는 뜻은 아닙니다" in result.check.note


async def test_it_never_calls_a_clean_answer_out_of_a_blocked_one(monkeypatch):
    """막힌 답을 '없음'으로 읽으면 진짜 스팸 도메인에 초록이 나간다."""
    _routes(
        monkeypatch,
        servers=[],
        direct=None,
        system=FakeResolver(answer=["127.255.255.254"]),
    )

    result = await spamhaus.check("example.com")

    assert result.listed is False
    assert result.check.status is not CheckStatus.OK
