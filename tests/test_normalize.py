from domainchecker.normalize import normalize_domain, parse_domains


def test_strips_scheme_www_path_and_port():
    assert normalize_domain("https://WWW.Example.com:8080/path?q=1#x") == "example.com"
    assert normalize_domain("  example.co.kr.  ") == "example.co.kr"
    assert normalize_domain("//sub.example.com/a") == "sub.example.com"


def test_rejects_non_domains():
    for bad in ("", "   ", "not a domain", "example", "http://", "1.2.3.4:80/x", "-bad.com"):
        assert normalize_domain(bad) is None


def test_idn_is_punycoded():
    assert normalize_domain("한국.kr") == "xn--3e0b707e.kr"


def test_parse_splits_on_newline_and_comma_and_dedupes():
    raw = "example.com, www.example.com\nhttps://foo.net\n\n엉뚱한값\nfoo.net"
    result = parse_domains(raw)
    assert result.domains == ["example.com", "foo.net"]
    assert result.duplicates == 2
    assert result.invalid == ["엉뚱한값"]
    assert "중복 2개 제거" in result.notice


def test_parse_caps_at_limit():
    raw = "\n".join(f"d{i}.com" for i in range(1200))
    result = parse_domains(raw)
    assert len(result.domains) == 1000
    assert result.truncated == 200
    assert "나눠서 검사" in result.notice
