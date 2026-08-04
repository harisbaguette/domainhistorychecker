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


async def test_blocked_query_is_unchecked_not_clean():
    result = await spamhaus.check("bad.com", FakeResolver(answer=["127.255.255.254"]))
    assert result.check.status is CheckStatus.UNCHECKED
    assert result.listed is False


async def test_dns_failure_is_unchecked():
    result = await spamhaus.check("x.com", FakeResolver(error=dns.exception.Timeout()))
    assert result.check.status is CheckStatus.UNCHECKED


async def test_unknown_answer_is_unchecked():
    result = await spamhaus.check("x.com", FakeResolver(answer=["10.0.0.1"]))
    assert result.check.status is CheckStatus.UNCHECKED
    assert result.listed is False
