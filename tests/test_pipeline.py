"""파이프라인 통합 1건 — 모든 클라이언트를 목(mock)으로 대체한다(실 네트워크 호출 없음)."""

import json
import re

import dns.resolver
import httpx
import pytest
import respx

from domainchecker.clients import freeindex, gabia
from domainchecker.config import ApiKeys, Config
from domainchecker.models import CheckStatus, Verdict
from domainchecker.pipeline import Pipeline
from domainchecker.ratelimit import AdaptiveRateLimiter

DOMAIN = "example.com"
YEARS = list(range(2015, 2025))
CDX = "https://index.commoncrawl.org/CC-MAIN-2026-30-index"

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


CDX_HEADER = ["timestamp", "original", "statuscode", "mimetype", "digest"]
# 통계·변경본 조회 공용 — 연도마다 내용이 다른(digest가 다른) 앞페이지 1판씩.
CDX_ROWS = [CDX_HEADER] + [
    [f"{year}0601120000", f"http://{DOMAIN}/", "200", "text/html", f"D{year}"] for year in YEARS
]
# 주소 전수 조회 — 연도마다 서로 다른 주소 하나씩 남아 있다.
CDX_PATH_ROWS = [CDX_HEADER] + [
    [f"{year}0601120000", f"http://{DOMAIN}/post/{year}", "200", "text/html", f"P{year}"]
    for year in YEARS
]

# 가비아 검색 답 — 삭제 예정(사전 예약 가능)이라 "곧 등록 가능"으로 읽혀야 하고,
# 살 수 있는 도메인이므로 나머지 분석이 전부 돈다.
GABIA_BACKORDER = {
    "flag": "A",
    "status": "BO",
    "result": "이미 등록된 도메인",
    "backorder_date": "2026-09-01",
}

# 커먼크롤에 남은 주소 24건 — 색인 수는 이 목록의 길이로 센다.
CRAWL_TEXT = "\n".join(
    f'{{"url": "https://{DOMAIN}/post/{i}", "status": "200"}}' for i in range(24)
)

# 세이프 브라우징 공개 조회 응답(키 없음) — 맨 앞 ")]}'" 는 구글이 일부러 붙이는 글자.
SAFEBROWSING_CLEAN = (
    ")]}'\n\n"
    f'[["sb.ssr",1,false,false,false,false,false,1786277573219,"{DOMAIN}"]]'
)

AI_JSON = {
    "topic_history": "2015년 빵집 기록으로 시작해 취미 기록으로 이어짐",
    "spam": {"verdict": "clean", "confidence": 0.9, "quotes": []},
    "transition": "생활 취미 주제를 계속 유지",
    "content_quality": "직접 쓴 기록으로 보임",
    "trademark": "상표 문제 없음",
    "trademark_risk": False,
    "recommended_topics": [{"topic": "홈베이킹 기록", "reason": "과거 주제와 인접"}],
    "one_liner": "꾸준히 운영된 생활 기록 사이트",
    "verdict": "buy",
    "buy_score": 88,
    "verdict_reason": "10년간 주제가 한결같고 스팸 흔적이 없음",
}


class FakeResolver:
    """스팸하우스 조회: NXDOMAIN = 블랙리스트에 없음."""

    async def resolve(self, qname, rdtype):
        raise dns.resolver.NXDOMAIN()


def mock_all() -> None:
    def cdx_response(request):
        # 주소 전수 조회만 경로 목록으로 답하고, 통계·변경본 조회는 같은 판 목록을 쓴다.
        if request.url.params.get("collapse", "") == "urlkey":
            return httpx.Response(200, json=CDX_PATH_ROWS)
        return httpx.Response(200, json=CDX_ROWS)

    respx.get(url__startswith="https://web.archive.org/cdx/search/cdx").mock(
        side_effect=cdx_response
    )

    def snapshot_response(request):
        year = int(re.search(r"/web/(\d{4})", str(request.url)).group(1))
        return httpx.Response(200, text=page_html(year))

    respx.get(url__regex=r"https://web\.archive\.org/web/\d+id_/.*").mock(
        side_effect=snapshot_response
    )
    respx.post(gabia.CHECK_URL).mock(
        return_value=httpx.Response(200, json=GABIA_BACKORDER)
    )
    # 색인 검사(키 없음): 커먼크롤 회차 목록 → 크롤 기록 → 현재 페이지
    respx.get(freeindex.COLLINFO_URL).mock(
        return_value=httpx.Response(200, json=[{"id": "CC-MAIN-2026-30", "cdx-api": CDX}])
    )
    respx.get(url__startswith=CDX).mock(return_value=httpx.Response(200, text=CRAWL_TEXT))
    respx.get(f"https://{DOMAIN}/").mock(
        return_value=httpx.Response(
            200,
            html="<html><head><title>빵집 이야기</title></head><body>동네 빵집의 기록</body></html>",
        )
    )
    # 세이프 브라우징(키 없음)
    respx.get(url__startswith="https://transparencyreport.google.com/").mock(
        return_value=httpx.Response(200, text=SAFEBROWSING_CLEAN)
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
    return Config(keys=ApiKeys(openrouter="r-key"), data_dir=str(tmp_path / "data"))


@pytest.fixture(autouse=True)
def _fresh_collection():
    # 커먼크롤 회차 주소는 모듈에 하루 동안 기억된다 — 시험끼리 새지 않게 비운다.
    freeindex.reset_collection_cache()
    yield
    freeindex.reset_collection_cache()


def fast_pipeline(config, events) -> Pipeline:
    pipeline = Pipeline(config, on_event=events.append, resolver=FakeResolver())
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

    # 수집 내용
    assert result.wayback.total_captures == len(YEARS)
    # 앞페이지 변경본은 전부 읽고, 하위 주소는 유형으로 묶어 대표를 정독한다.
    assert result.wayback.versions_total == len(YEARS)
    assert result.wayback.versions_read == len(YEARS)
    # /post/2015 … /post/2024 는 전부 한 유형(/post/N) — 대표는 처음·마지막 해 2장.
    assert len(result.wayback.pages) == len(YEARS) + 2
    assert f"변경본 {len(YEARS)}장 전부 읽음" in result.wayback.coverage_note
    assert f"하위 주소 {len(YEARS)}개" in result.wayback.coverage_note
    assert "유형 1가지" in result.wayback.coverage_note
    assert "2곳 중 2곳 본문 정독" in result.wayback.coverage_note
    assert result.wayback.path_samples == [f"{y} /post/{y}" for y in YEARS]
    assert result.wayback.subpages == []  # 선별용 목록은 결과에 남기지 않는다
    assert result.wayback.selected[-1].timestamp.startswith("2024")  # 말기 포함
    assert result.registration.source == "gabia"
    assert result.registration.acquisition == "삭제 대기(곧 등록 가능)"
    assert result.index.indexed_count == 24
    assert result.ai.model == "deepseek/deepseek-v4-flash-0731"
    assert result.ai.fallback_used is False

    # 세이프 브라우징도 키 없이 돌아 확인으로 남는다
    assert result.safebrowsing.check.status is CheckStatus.OK
    assert result.safebrowsing.listed is False

    assert result.fatal_reasons == []
    assert result.warn_reasons == []
    assert result.verdict is Verdict.BUY
    assert result.score == 88  # AI가 매긴 매입 매력도가 곧 점수
    assert result.ai.verdict == "buy"
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
async def test_domain_done_counts_include_the_domain_just_finished(config):
    """화면 진행률이 이 숫자를 그대로 쓴다 — 여기서 하나 어긋나면 막대가 100%를 넘는다."""
    mock_all()
    events = []
    await fast_pipeline(config, events).run([DOMAIN])

    done_events = [e for e in events if e["type"] == "domain_done"]
    assert [e["done"] for e in done_events] == [1]
    assert done_events[-1]["done"] == done_events[-1]["total"]


@respx.mock
async def test_report_is_ready_before_the_run_reports_finished(config):
    """보고서 단추가 '끝났습니다' 뒤에 늦게 켜지면 사용자는 없는 줄 알고 나간다."""
    mock_all()
    events = []
    await fast_pipeline(config, events).run([DOMAIN])

    kinds = [e["type"] for e in events]
    assert "report_ready" in kinds
    assert kinds.index("report_ready") < kinds.index("finished")
    assert kinds[-1] == "finished"


@respx.mock
async def test_capture_done_carries_the_updated_result(config, monkeypatch, tmp_path):
    """캡쳐 결과를 이벤트에 안 실으면 화면·메모리에 사진 이전 결과만 남는다(심사 C1)."""
    from domainchecker import capture as capture_module
    from domainchecker import pipeline as pipeline_module
    from domainchecker.models import Capture, Captures, CheckState, CheckStatus, DomainResult

    async def fake_capture(result, base, limiter=None, enabled=True):
        return Captures(
            check=CheckState(status=CheckStatus.OK, note="1장 저장."),
            items=[
                Capture(
                    label="말기",
                    timestamp="20240601000000",
                    url="https://web.archive.org/web/20240601000000/http://example.com/",
                    file="captures/example.com_20240601000000.png",
                )
            ],
        )

    monkeypatch.setattr(capture_module, "capture_domain", fake_capture)
    monkeypatch.setattr(pipeline_module.capture, "capture_domain", fake_capture)

    events = []
    config.enable_capture = True
    pipeline = fast_pipeline(config, events)
    await pipeline.capture_phase([DomainResult(domain=DOMAIN)])

    done = [e for e in events if e["type"] == "capture_done"]
    assert len(done) == 1
    assert done[0]["shots"] == 1
    assert done[0]["result"]["domain"] == DOMAIN
    assert done[0]["result"]["captures"]["items"][0]["label"] == "말기"


@respx.mock
async def test_taken_domain_skips_the_rest_of_the_analysis(config):
    """주인이 있으면 웨이백·색인·AI 같은 나머지 분석을 아예 안 한다 — 살 수 없는 도메인이다."""
    mock_all()
    # 같은 주소를 다시 걸면 나중 것이 이긴다 — 주인 있는 가비아 답으로 갈아 끼운다.
    respx.post(gabia.CHECK_URL).mock(
        return_value=httpx.Response(
            200,
            json={"flag": "A", "status": "10002", "result": "이미 등록된 도메인"},
        )
    )

    results = await fast_pipeline(config, []).run([DOMAIN], use_cache=False)
    result = results[0]

    assert result.availability == "taken"
    assert result.wayback.check.status is CheckStatus.NOT_RUN
    assert result.index.check.status is CheckStatus.NOT_RUN
    assert result.ai.check.status is CheckStatus.NOT_RUN
    assert "주인이 있는 도메인" in result.wayback.check.note
    # 네트워크로도 안 나갔는지 — 웨이백·AI 호출 0건
    called = [str(call.request.url) for call in respx.calls]
    assert not any("web.archive.org" in url for url in called)
    assert not any("openrouter.ai" in url for url in called)


@respx.mock
async def test_failed_checks_degrade_to_unchecked_and_block_buy(config):
    respx.get(url__startswith="https://web.archive.org/cdx/search/cdx").mock(
        side_effect=httpx.ConnectError("boom")
    )
    respx.post(gabia.CHECK_URL).mock(return_value=httpx.Response(500))
    respx.get(freeindex.COLLINFO_URL).mock(return_value=httpx.Response(500))
    respx.get(f"https://{DOMAIN}/").mock(side_effect=httpx.ConnectError("boom"))
    respx.get(f"http://{DOMAIN}/").mock(side_effect=httpx.ConnectError("boom"))
    respx.get(url__startswith="https://transparencyreport.google.com/").mock(
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


@respx.mock
async def test_check_one_streams_steps_and_finishes_with_the_result(config):
    """화면이 한 개씩 부르는 길 — 진행 토막(step)이 먼저, 결과(domain_done)가 마지막."""
    mock_all()
    events = []
    result = await fast_pipeline(config, events).check_one(DOMAIN, use_cache=False)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "step"
    assert kinds[-1] == "domain_done"
    assert events[-1]["result"]["domain"] == DOMAIN
    assert result.domain == DOMAIN
    labels = [e["label"] for e in events if e["type"] == "step"]
    assert any("살 수 있는" in label for label in labels)
    assert any("웨이백" in label for label in labels)
    fracs = [e["frac"] for e in events if e["type"] == "step"]
    assert fracs == sorted(fracs) and 0 < fracs[0] < 1

    # 두 번째는 저장분을 쓴다 — 진행 토막 없이 바로 끝난다
    again = []
    await fast_pipeline(config, again).check_one(DOMAIN, use_cache=True)
    assert [e["type"] for e in again] == ["domain_done"]
    assert again[0]["cached"] is True

