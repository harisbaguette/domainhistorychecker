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
    # 사용자가 스스로 고칠 수 있게 원인과 해결법을 적어 준다.
    assert "공용 DNS" in result.check.note
    assert "통신사 자동" in result.check.note


async def test_dns_failure_is_unchecked():
    result = await spamhaus.check("x.com", FakeResolver(error=dns.exception.Timeout()))
    assert result.check.status is CheckStatus.UNCHECKED


async def test_unknown_answer_is_unchecked():
    result = await spamhaus.check("x.com", FakeResolver(answer=["10.0.0.1"]))
    assert result.check.status is CheckStatus.UNCHECKED
    assert result.listed is False
