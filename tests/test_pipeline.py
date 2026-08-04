"""파이프라인 통합 1건 — 모든 클라이언트를 목(mock)으로 대체한다(실 네트워크 호출 없음)."""

import json
import re

import dns.resolver
import httpx
import pytest
import respx

from domainchecker.config import ApiKeys, Config
from domainchecker.models import CheckStatus, Verdict
from domainchecker.pipeline import Pipeline
from domainchecker.ratelimit import AdaptiveRateLimiter

DOMAIN = "example.com"
YEARS = list(range(2015, 2025))

# 연도마다 겹치지 않는 낱말 묶음 — 실제 사이트처럼 내용이 해마다 달라진다.
POOLS = [
    "빵집 반죽 발효 오븐 통밀 버터 우유 소금 설탕 계량",
    "등산 배낭 지도 나침반 텐트 침낭 능선 계곡 물통 장갑",
    "사진 렌즈 조리개 셔터 필름 현상 인화 삼각대 노출 초점",
    "화분 흙갈이 물주기 햇빛 분갈이 잎사귀 뿌리 씨앗 새싹 거름",
    "자전거 안장 체인 기어 바퀴 헬멧 라이딩 코스 정비 공기압",
    "커피 원두 로스팅 추출 드리퍼 필터 그라인더 온도계 저울 향미",
    "목공 대패 끌 사포 도면 접착 마감 오일 경첩 서랍",
    "낚시 미끼 찌 릴 낚싯대 물때 갯바위 방파제 채비 손질",
    "도자기 물레 유약 가마 소성 흙물 성형 굽기 붓질 무늬",
    "서예 붓 먹 벼루 화선지 획 자세 임서 낙관 표구",
]


def page_html(year: int) -> str:
    words = POOLS[(year - 2015) % len(POOLS)]
    body = " ".join(f"<p>{words}</p>" for _ in range(20))
    return f"<html><head><title>{year}년 기록</title></head><body>{body}</body></html>"


CDX_ROWS = [["timestamp", "original", "statuscode", "mimetype", "digest"]] + [
    [f"{year}0601120000", f"http://{DOMAIN}/", "200", "text/html", f"D{year}"] for year in YEARS
]

RDAP_JSON = {
    "events": [
        {"eventAction": "registration", "eventDate": "2014-05-01T00:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2026-05-01T00:00:00Z"},
    ],
    "status": ["pending delete"],
    "entities": [],
}

SERPER_JSON = {
    "searchInformation": {"totalResults": "24"},
    "organic": [{"title": "빵집 이야기", "snippet": "동네 빵집의 기록"}],
}

OPR_JSON = {
    "response": [
        {"domain": DOMAIN, "status_code": 200, "page_rank_decimal": "3.0", "rank": "120000"}
    ]
}

AI_JSON = {
    "topic_history": "2015년 빵집 기록으로 시작해 취미 기록으로 이어짐",
    "spam": {"verdict": "clean", "confidence": 0.9, "quotes": []},
    "transition": "생활 취미 주제를 계속 유지",
    "content_quality": "직접 쓴 기록으로 보임",
    "trademark": "상표 문제 없음",
    "trademark_risk": False,
    "recommended_topics": [{"topic": "홈베이킹 기록", "reason": "과거 주제와 인접"}],
    "one_liner": "꾸준히 운영된 생활 기록 사이트",
}


class FakeResolver:
    """스팸하우스 조회: NXDOMAIN = 블랙리스트에 없음."""

    async def resolve(self, qname, rdtype):
        raise dns.resolver.NXDOMAIN()


def mock_all() -> None:
    respx.get(url__startswith="https://web.archive.org/cdx/search/cdx").mock(
        return_value=httpx.Response(200, json=CDX_ROWS)
    )

    def snapshot_response(request):
        year = int(re.search(r"/web/(\d{4})", str(request.url)).group(1))
        return httpx.Response(200, text=page_html(year))

    respx.get(url__regex=r"https://web\.archive\.org/web/\d+id_/.*").mock(
        side_effect=snapshot_response
    )
    respx.get(f"https://rdap.org/domain/{DOMAIN}").mock(
        return_value=httpx.Response(200, json=RDAP_JSON)
    )
    respx.post("https://google.serper.dev/search").mock(
        return_value=httpx.Response(200, json=SERPER_JSON)
    )
    respx.get(url__startswith="https://openpagerank.com/api/v1.0/getPageRank").mock(
        return_value=httpx.Response(200, json=OPR_JSON)
    )
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(AI_JSON, ensure_ascii=False)}}
                ],
                "model": "deepseek/deepseek-v4-flash-0731",
            },
        )
    )


@pytest.fixture
def config(tmp_path):
    return Config(
        keys=ApiKeys(serper="s-key", openpagerank="o-key", openrouter="r-key"),
        enable_safebrowsing=False,
        enable_virustotal=False,
        data_dir=str(tmp_path / "data"),
    )


async def no_whois(domain, server):
    raise OSError("테스트에서는 whois 소켓을 열지 않는다")


def fast_pipeline(config, events) -> Pipeline:
    pipeline = Pipeline(
        config, on_event=events.append, resolver=FakeResolver(), whois_query=no_whois
    )
    # 테스트에서는 실제로 기다리지 않는다.
    pipeline.wayback_limiter = AdaptiveRateLimiter(rpm=6000)
    return pipeline


@respx.mock
async def test_full_run_produces_a_buy_verdict(config, tmp_path):
    mock_all()
    events = []
    pipeline = fast_pipeline(config, events)

    results = await pipeline.run([DOMAIN])

    assert len(results) == 1
    result = results[0]
    assert result.domain == DOMAIN

    # 모든 필수 검사가 확인됨
    assert result.wayback.check.status is CheckStatus.OK
    assert result.registration.check.status is CheckStatus.OK
    assert result.spamhaus.check.status is CheckStatus.OK
    assert result.index.check.status is CheckStatus.OK
    assert result.ai.check.status is CheckStatus.OK
    assert result.authority.check.status is CheckStatus.OK

    # 수집 내용
    assert result.wayback.total_captures == len(YEARS)
    assert len(result.wayback.selected) == config.max_snapshots
    assert result.wayback.selected[-1].timestamp.startswith("2024")  # 말기 포함
    assert result.registration.created == "2014-05-01"
    assert result.registration.acquisition == "삭제 대기(곧 등록 가능)"
    assert result.authority.page_rank == pytest.approx(3.0)
    assert result.index.indexed_count == 24
    assert result.ai.model == "deepseek/deepseek-v4-flash-0731"
    assert result.ai.fallback_used is False

    # 옵션 검사는 미실시로 남고 ✅를 막지 않는다
    assert result.safebrowsing.check.status is CheckStatus.NOT_RUN
    assert result.virustotal.check.status is CheckStatus.NOT_RUN

    assert result.fatal_reasons == []
    assert result.warn_reasons == []
    assert result.verdict is Verdict.BUY
    assert result.score >= 75
    assert result.one_liner == AI_JSON["one_liner"]

    # 원본 HTML은 저장하지 않는다
    assert all("html" not in page for page in result.wayback.pages)

    # 진행 이벤트 + 결과 파일
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "finished"
    assert "domain_done" in kinds
    saved = json.loads((tmp_path / "data" / "results.json").read_text(encoding="utf-8"))
    assert saved["count"] == 1
    assert saved["results"][0]["verdict"] == "BUY"


@respx.mock
async def test_second_run_uses_the_cache(config):
    mock_all()
    events = []
    first = await fast_pipeline(config, events).run([DOMAIN])
    calls_after_first = len(respx.calls)

    events2 = []
    second = await fast_pipeline(config, events2).run([DOMAIN])

    assert len(respx.calls) == calls_after_first  # 새 네트워크 호출 없음
    assert second[0].verdict == first[0].verdict
    assert any(e.get("cached") for e in events2 if e["type"] == "domain_done")


@respx.mock
async def test_failed_checks_degrade_to_unchecked_and_block_buy(config):
    respx.get(url__startswith="https://web.archive.org/cdx/search/cdx").mock(
        side_effect=httpx.ConnectError("boom")
    )
    respx.get(f"https://rdap.org/domain/{DOMAIN}").mock(return_value=httpx.Response(500))
    respx.post("https://google.serper.dev/search").mock(return_value=httpx.Response(500))
    respx.get(url__startswith="https://openpagerank.com/api/v1.0/getPageRank").mock(
        return_value=httpx.Response(500)
    )
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(500)
    )

    pipeline = fast_pipeline(config, [])
    results = await pipeline.run([DOMAIN], use_cache=False)
    result = results[0]

    assert result.verdict is not Verdict.BUY
    assert result.unchecked  # 실패한 검사는 미확인으로 남는다
    assert any("필수 검사 미확인" in reason for reason in result.warn_reasons)
