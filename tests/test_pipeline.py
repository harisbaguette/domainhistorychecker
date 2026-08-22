"""파이프라인 통합 1건 — 모든 클라이언트를 목(mock)으로 대체한다(실 네트워크 호출 없음)."""

import asyncio
import json
import re

import dns.resolver
import httpx
import pytest
import respx
from conftest import put_blacklist

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

    # 4단까지 간 도메인이 받아 온 옛 화면 본문 장수 — 3단의 최신 한 장 +
    # 앞페이지 변경본 10장 + 하위 유형 대표 2장. 3단에서 걸리면 이게 1장이 된다.
    fetched = [c for c in respx.calls if "id_/" in str(c.request.url)]
    assert len(fetched) == len(YEARS) + 3

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
async def test_run_state_survives_a_stop_and_is_cleared_on_success(config, tmp_path):
    """앱을 껐다 켜도 '이어서 검사'가 살아 있어야 한다(심사 C2)."""
    from domainchecker.pipeline import load_run_state, run_state_path

    mock_all()
    base = tmp_path / "data"

    stopped = fast_pipeline(config, [])
    stopped.stop()  # 시작하자마자 중단 — 목록은 남아야 한다
    await stopped.run([DOMAIN], use_cache=False)

    assert run_state_path(base).exists()
    assert load_run_state(base) == [DOMAIN]

    await fast_pipeline(config, []).run([DOMAIN], use_cache=False)

    assert not run_state_path(base).exists()  # 끝까지 갔으면 이어서 할 것이 없다
    assert load_run_state(base) == []


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
async def test_steps_flow_while_one_domain_runs(config):
    """도메인 하나가 도는 동안 step 사건이 순서대로 흘러야 막대가 살아 움직인다."""
    mock_all()
    events = []
    await fast_pipeline(config, events).run([DOMAIN], use_cache=False)

    steps = [e for e in events if e["type"] == "step"]
    assert steps, "step 사건이 하나도 없다"
    labels = [s["label"] for s in steps]
    assert any("살 수 있는" in label for label in labels)
    assert any("웨이백" in label for label in labels)
    fracs = [s["frac"] for s in steps]
    assert all(0 < f <= 1 for f in fracs)
    assert fracs == sorted(fracs)  # 뒤로 가지 않는다


async def test_stop_cancels_the_inflight_domain_quickly(config, monkeypatch):
    """중단을 누르면 돌던 도메인도 그 자리에서 끊겨야 한다 — 몇 분씩 계속 돌면 안 된다."""
    events = []
    pipeline = fast_pipeline(config, events)

    async def hang(domain, http):
        await asyncio.sleep(3600)

    monkeypatch.setattr(pipeline, "check_domain", hang)
    task = asyncio.create_task(pipeline.run([DOMAIN], use_cache=False))
    await asyncio.sleep(0.05)
    pipeline.stop()
    await asyncio.wait_for(task, timeout=2)  # 2초 안에 끝나야 한다
    kinds = [e["type"] for e in events]
    assert kinds[-1] == "finished"
    assert events[-1]["stopped"] is True
    assert "domain_failed" not in kinds  # 끊긴 것은 실패가 아니다


async def test_capture_phase_stays_silent_after_a_stop(config, sample_result):
    """중단한 판에서 "이제 사진을 찍습니다" 문구가 나오면 화면이 거짓말이 된다."""
    events = []
    pipeline = fast_pipeline(config, events)
    pipeline.stop()
    await pipeline.capture_phase([sample_result])
    assert events == []


# ─────────────────────────── 깔때기 4단 ───────────────────────────


class ListedResolver:
    """스팸하우스 조회: 127.0.1.2 = 스팸 도메인으로 등재됨."""

    async def resolve(self, qname, rdtype):
        return ["127.0.1.2"]


def mock_ai(payload: dict) -> None:
    """OpenRouter 답을 통째로 갈아 끼운다 — 나중에 건 것이 이긴다."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps(payload, ensure_ascii=False)}}
                ],
                "model": "deepseek/deepseek-v4-flash-0731",
            },
        )
    )


def body_calls() -> list[str]:
    """실제로 받아 온 옛 화면 본문 주소만 — 목록 조회는 빼고 센다."""
    return [str(c.request.url) for c in respx.calls if "id_/" in str(c.request.url)]


@respx.mock
async def test_stage1_taken_domain_ends_before_the_blocklist_is_even_checked(config, tmp_path):
    """1단 — 주인이 있으면 명단 대조조차 하지 않는다. 살 수 없는 도메인이다."""
    mock_all()
    put_blacklist(tmp_path / "data", "gambling", [DOMAIN])
    respx.post(gabia.CHECK_URL).mock(
        return_value=httpx.Response(
            200, json={"flag": "A", "status": "10002", "result": "이미 등록된 도메인"}
        )
    )

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.availability == "taken"
    assert result.blocklist.check.status is CheckStatus.NOT_RUN
    assert result.wayback.check.status is CheckStatus.NOT_RUN
    called = [str(call.request.url) for call in respx.calls]
    assert not any("web.archive.org" in url for url in called)
    assert not any("openrouter.ai" in url for url in called)


@respx.mock
async def test_stage2_listed_domain_is_rejected_before_any_wayback_call(config, tmp_path):
    """2단 — 받아 둔 명단에 있으면 판매처 확인 말고는 아무 데도 안 나가고 끝난다."""
    mock_all()
    put_blacklist(tmp_path / "data", "gambling", [DOMAIN])

    results = await fast_pipeline(config, []).run([DOMAIN], use_cache=False)
    result = results[0]

    assert result.verdict is Verdict.REJECT
    assert result.blocklist.listed is True
    assert "도박 명단(UT1 툴루즈" in result.blocklist.codes[0]
    assert any("세계 공개 위험 명단" in reason for reason in result.fatal_reasons)
    # 주인 여부(1단)만 물어봤고 웨이백·AI 어느 쪽으로도 나가지 않았다.
    called = [str(call.request.url) for call in respx.calls]
    assert len(called) == 1
    assert not any("web.archive.org" in url for url in called)
    assert not any("openrouter.ai" in url for url in called)
    assert result.registration.check.status is CheckStatus.OK
    assert result.wayback.check.status is CheckStatus.NOT_RUN
    assert result.ai.check.status is CheckStatus.NOT_RUN


@respx.mock
async def test_stage2_leaves_a_clean_domain_alone(config, tmp_path):
    """명단에 없는 도메인은 2단에서 아무것도 잃지 않고 4단까지 그대로 간다."""
    mock_all()
    put_blacklist(tmp_path / "data", "gambling", ["someone-else.com"])

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.blocklist.check.status is CheckStatus.OK
    assert result.blocklist.listed is False
    assert result.verdict is Verdict.BUY


@respx.mock
async def test_stage3_blacklist_hit_skips_the_expensive_deep_read(config):
    """3단 — 블랙리스트에 걸리면 옛 화면 본문은 한 장도 안 받는다(최신 한 장조차)."""
    mock_all()

    pipeline = fast_pipeline(config, [])
    pipeline.resolver = ListedResolver()
    result = (await pipeline.run([DOMAIN], use_cache=False))[0]

    assert result.verdict is Verdict.REJECT
    assert result.spamhaus.listed is True
    # 웨이백은 목록 조회(싼 것)만 하고, 본문 받기(비싼 것)는 0건이어야 한다.
    called = [str(call.request.url) for call in respx.calls]
    assert any("cdx/search/cdx" in url for url in called)
    assert body_calls() == []
    assert not any("openrouter.ai" in url for url in called)
    assert result.ai.check.status is CheckStatus.NOT_RUN
    assert result.rules.check.status is CheckStatus.NOT_RUN


@respx.mock
async def test_stage3_never_hands_out_a_buy_on_its_own(config):
    """앞 단에서는 '좋아 보인다'로 통과시키지 않는다 — 매입 후보는 4단을 지나야 한다."""
    mock_all()
    # AI를 끄면 4단까지 가더라도 합격 도장은 안 나온다(뜻 읽기 없이 ✅ 금지).
    config.keys.openrouter = ""

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.verdict is not Verdict.BUY
    # 그래도 4단은 실제로 돌았다 — 본문을 읽었다는 증거
    assert result.wayback.versions_read > 0


# ── 3단 신설 — 가장 최근 화면 딱 한 장으로 거르기 ──────────────────────


@respx.mock
async def test_stage3_machine_rules_reject_on_the_latest_page_alone(config):
    """3단 — 최신 화면 한 장에 스팸 흔적이 겹치면 옛 화면은 한 장도 안 읽는다."""
    mock_all()
    # 저장된 모든 화면이 스팸 — 그중 가장 최근 한 장만 읽고 끝나야 한다.
    respx.get(url__regex=r"https://web\.archive\.org/web/\d+id_/.*").mock(
        side_effect=lambda request: httpx.Response(
            200, text=spam_page(int(re.search(r"/web/(\d{4})", str(request.url)).group(1)))
        )
    )

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.verdict is Verdict.REJECT
    assert len(body_calls()) == 1  # 최신 한 장이 전부다
    assert "가장 최근 화면 1장" in result.wayback.coverage_note
    assert result.rules.check.status is CheckStatus.OK
    # 기계 규칙만으로 확정됐으면 돈 드는 AI 호출은 아예 안 한다.
    assert result.ai.check.status is CheckStatus.NOT_RUN
    assert not any("openrouter.ai" in str(c.request.url) for c in respx.calls)


@respx.mock
async def test_stage3_ai_rejects_a_page_the_rules_cannot_see(config):
    """규칙에는 안 걸려도 AI가 인용을 들고 확신하면 3단에서 떨어뜨린다."""
    mock_all()
    mock_ai({"verdict": "spam", "confidence": 0.95, "quotes": ["가입 즉시 첫 충전 보너스 지급"]})

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.verdict is Verdict.REJECT
    assert result.ai.spam.verdict == "spam"
    assert len(body_calls()) == 1
    assert "가장 최근 화면 1장" in result.wayback.coverage_note
    # AI에게도 딱 한 번(최신 한 장)만 물었다 — 전수 판정 호출까지 가지 않았다.
    assert len([c for c in respx.calls if "openrouter.ai" in str(c.request.url)]) == 1


@respx.mock
async def test_stage3_unclear_ai_is_not_a_rejection(config):
    """애매하면 떨어뜨리지 않는다 — 좋은 도메인을 잃지 않는 쪽이 먼저다."""
    mock_all()
    mock_ai({"verdict": "unclear", "confidence": 0.4, "quotes": []})

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.verdict is not Verdict.REJECT
    assert result.wayback.versions_read == len(YEARS)  # 4단 전수 정독까지 갔다
    assert len(body_calls()) > 1


@respx.mock
async def test_stage3_ai_failure_is_not_a_pass(config):
    """AI 호출이 실패하면 '판정 불가'다 — 합격이 아니라 4단으로 내려보낸다."""
    mock_all()
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(500)
    )

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.verdict is not Verdict.REJECT
    assert result.verdict is not Verdict.BUY  # AI 없이 합격 도장도 없다
    assert result.wayback.versions_read == len(YEARS)  # 4단 전수 정독은 그대로 돌았다


@respx.mock
async def test_stage3_without_an_ai_key_still_goes_down_to_stage4(config):
    """키가 없는 것도 '판정 불가'다 — 3단이 조용히 통과시키면 안 된다."""
    mock_all()
    config.keys.openrouter = ""

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.wayback.versions_read == len(YEARS)
    assert result.verdict is not Verdict.BUY


@respx.mock
async def test_no_history_domain_finishes_without_reading_anything(config):
    """기록이 없으면 몇 초 만에 끝난다 — 변경본·하위 주소 조회조차 하지 않는다."""
    respx.get(url__startswith="https://web.archive.org/cdx/search/cdx").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.post(gabia.CHECK_URL).mock(return_value=httpx.Response(200, json=GABIA_BACKORDER))
    respx.get(freeindex.COLLINFO_URL).mock(
        return_value=httpx.Response(200, json=[{"id": "CC-MAIN-2026-30", "cdx-api": CDX}])
    )
    respx.get(url__startswith=CDX).mock(return_value=httpx.Response(200, text=CRAWL_TEXT))
    respx.get(f"https://{DOMAIN}/").mock(return_value=httpx.Response(200, html="<html></html>"))
    respx.get(url__startswith="https://transparencyreport.google.com/").mock(
        return_value=httpx.Response(200, text=SAFEBROWSING_CLEAN)
    )

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.verdict is Verdict.NO_HISTORY
    cdx_calls = [c for c in respx.calls if "cdx/search/cdx" in str(c.request.url)]
    assert len(cdx_calls) == 1  # 목록 조회 딱 한 번
    assert not any("id_/" in str(c.request.url) for c in respx.calls)
    assert result.ai.check.status is CheckStatus.NOT_RUN


def spam_page(year: int) -> str:
    """규칙 검사가 흔적 3종 넘게 잡을 화면 — 숨긴 글자 + 링크 무더기 + 키워드 나열."""
    links = "".join(
        f'<a href="https://out{i}.example.org/go">바로가기</a>' for i in range(60)
    )
    stuffed = ", ".join(["바카라", "슬롯", "룰렛", "블랙잭", "포커", "토토", "환전", "가입", "쿠폰"] * 20)
    return (
        "<html><head><title>추천 순위</title></head><body>"
        '<div style="display:none">숨긴 글자입니다</div>'
        f"<p>{stuffed}</p>{links}</body></html>"
    )


@respx.mock
async def test_machine_veto_stops_reading_the_rest_of_the_pages(config):
    """4단 조기 종료 — 제외가 확정되면 남은 옛 화면은 안 받는다(웨이백 부하 절감).

    최신 화면(2024년)만 멀쩡하게 두어 3단을 통과시킨 뒤, 옛 화면들이 스팸이라
    4단 정독 도중에 멈추는 길을 시험한다.
    """
    mock_all()
    respx.get(url__regex=r"https://web\.archive\.org/web/\d+id_/.*").mock(
        side_effect=lambda request: httpx.Response(
            200,
            text=(
                page_html(2024)
                if str(request.url).startswith("https://web.archive.org/web/2024")
                else spam_page(int(re.search(r"/web/(\d{4})", str(request.url)).group(1)))
            ),
        )
    )
    mock_ai({"verdict": "unclear", "confidence": 0.3, "quotes": []})

    result = (await fast_pipeline(config, []).run([DOMAIN], use_cache=False))[0]

    assert result.verdict is Verdict.REJECT
    assert 1 < len(body_calls()) < len(YEARS)  # 다 읽지 않고 중간에 멈췄다
    assert "제외가 확정" in result.wayback.coverage_note
    # 판정이 안 바뀌는데 돈이 드는 AI 호출을 더 하지 않는다 —
    # 남은 openrouter 호출은 3단의 '최신 한 장' 물음 하나뿐이다.
    assert result.ai.check.status is CheckStatus.NOT_RUN
    assert len([c for c in respx.calls if "openrouter.ai" in str(c.request.url)]) == 1


@respx.mock
async def test_progress_events_say_which_stage_we_are_in(config):
    """화면에 단계가 보여야 '지금 싼 검사인지 비싼 정독인지'를 사람이 안다."""
    mock_all()
    events = []
    await fast_pipeline(config, events).run([DOMAIN], use_cache=False)

    stages = {e.get("stage") for e in events if e["type"] in ("step", "notice")}
    assert "1단" in stages  # 주인 여부
    assert "2단" in stages  # 공개 위험 명단
    assert "3단" in stages  # 가장 최근 화면 한 장 + 싼 조회
    assert "4단" in stages  # 옛 화면 전수 정독

