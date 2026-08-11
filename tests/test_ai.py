import json

import httpx
import pytest
import respx

from domainchecker.analyze.ai import analyze, build_prompt, dedupe_texts, pack_chunks
from domainchecker.analyze.extract import SnapshotContent
from domainchecker.clients.openrouter import OpenRouterClient
from domainchecker.config import MODEL_CHAIN
from domainchecker.models import CheckStatus

ANSWER = {
    "topic_history": "빵집 기록",
    "spam": {"verdict": "spam", "confidence": 0.91, "quotes": ["카지노 가입 코드"]},
    "transition": "정상에서 도박으로 전환",
    "content_quality": "낮음",
    "trademark": "문제 없음",
    "trademark_risk": False,
    "recommended_topics": [{"topic": "홈베이킹", "reason": "과거 주제와 인접"}],
    "one_liner": "도박 사이트로 바뀐 이력이 있음",
}


def snaps(count=6, size=20000):
    # 장마다 글자를 다르게 — 본문이 똑같으면 전수 읽기가 한 장으로 접는 게 맞다.
    return [
        SnapshotContent(timestamp=f"{2015 + i}0101000000", text=chr(0xAC00 + i) * size, lang="ko")
        for i in range(count)
    ]


def openrouter_response(model="deepseek/deepseek-v4-flash-0731"):
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(ANSWER, ensure_ascii=False)}}], "model": model},
    )


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def test_prompt_respects_the_input_cap():
    prompt = build_prompt("x.com", snaps(), {"timeline": "요약"}, input_limit=40_000)
    assert len(prompt) <= 40_000
    assert "x.com" in prompt


def test_prompt_marks_parking_and_language():
    parked = SnapshotContent(timestamp="20200101000000", text="본문", lang="ko", parking=True)
    prompt = build_prompt("x.com", [parked])
    assert "파킹 페이지" in prompt
    assert "한국어" in prompt


@respx.mock
async def test_analysis_parses_the_schema(http):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=openrouter_response()
    )
    client = OpenRouterClient(http, "key", list(MODEL_CHAIN))
    result = await analyze("x.com", snaps(1, 100), client)

    assert result.check.status is CheckStatus.OK
    assert result.spam.verdict == "spam"
    assert result.spam.confidence == pytest.approx(0.91)
    assert result.spam.quotes == ["카지노 가입 코드"]
    assert result.model == MODEL_CHAIN[0]
    assert result.fallback_used is False
    assert result.recommended_topics[0]["topic"] == "홈베이킹"


@respx.mock
async def test_model_fallback_chain_is_recorded(http):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(404, text="model not found"),
            openrouter_response(MODEL_CHAIN[1]),
        ]
    )
    client = OpenRouterClient(http, "key", list(MODEL_CHAIN))
    result = await analyze("x.com", snaps(1, 100), client)

    assert result.check.status is CheckStatus.OK
    assert result.model == MODEL_CHAIN[1]
    assert result.fallback_used is True
    assert "대체 모델" in result.check.note


@respx.mock
async def test_every_model_failing_is_unchecked(http):
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(500)
    )
    client = OpenRouterClient(http, "key", list(MODEL_CHAIN))
    result = await analyze("x.com", snaps(1, 100), client)

    assert result.check.status is CheckStatus.UNCHECKED
    assert result.spam.verdict == "unknown"


async def test_no_key_means_not_run():
    result = await analyze("x.com", snaps(1, 100), None)
    assert result.check.status is CheckStatus.NOT_RUN
    # 키가 없다고 판정이 막히지는 않는다 — 규칙 검사(기계적 판단)가 대신 든다.
    assert "규칙 검사만으로 판정" in result.check.note


async def test_no_readable_text_is_unchecked(http):
    client = OpenRouterClient(http, "key", list(MODEL_CHAIN))
    result = await analyze("x.com", [SnapshotContent(timestamp="20200101000000")], client)

    assert result.check.status is CheckStatus.UNCHECKED
    assert "과거 본문이 없어" in result.check.note


def test_pack_chunks_keep_every_page_in_time_order():
    """묶음으로 나눠도 버려지는 장은 없다 — 전수 읽기의 뼈대."""
    pages = snaps(6, 20000)  # 6장 × 2만 자 — 한 호출 예산(4만)에 다 못 들어간다
    chunks = pack_chunks(pages, input_limit=40_000)

    assert len(chunks) > 1
    flattened = [s.timestamp for chunk in chunks for s in chunk]
    assert flattened == [s.timestamp for s in pages]  # 전부, 순서대로


def test_identical_bodies_are_read_once():
    same = "가" * 500
    pages = [
        SnapshotContent(timestamp="20150101000000", text=same),
        SnapshotContent(timestamp="20160101000000", text=same),
        SnapshotContent(timestamp="20170101000000", text="나" * 500),
    ]
    kept = dedupe_texts(pages)
    assert [s.timestamp[:4] for s in kept] == ["2015", "2017"]


@respx.mock
async def test_large_history_is_split_read_fully_and_merged(http):
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=openrouter_response()
    )
    client = OpenRouterClient(http, "key", list(MODEL_CHAIN))
    result = await analyze("x.com", snaps(6, 20000), client)

    assert result.check.status is CheckStatus.OK
    assert route.call_count == 7  # 묶음 6번 + 통합 판정 1번
    assert "묶음으로 나눠 전부 읽고 합쳤습니다" in result.check.note
    assert result.spam.verdict == "spam"


@respx.mock
async def test_years_without_bodies_still_reach_the_ai_as_paths(http):
    """본문이 하나도 안 남은 해의 주소 흔적은 주소 전용 묶음으로 반드시 AI에 간다."""
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=openrouter_response()
    )
    client = OpenRouterClient(http, "key", list(MODEL_CHAIN))
    context = {"history_paths": ["2013 /카지노/join", "2016 /blog/hello"]}
    result = await analyze("x.com", snaps(2, 20000), client, context)  # 본문은 2015·2016뿐

    assert result.check.status is CheckStatus.OK
    # 본문 묶음 2 + 주소 전용 묶음 1(2013) + 통합 판정 1 = 4번
    assert route.call_count == 4


@respx.mock
async def test_one_failed_chunk_means_no_full_check_claim(http):
    """한 묶음이라도 못 읽었으면 '전수 확인했다'고 말하지 않는다."""
    respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        side_effect=[openrouter_response()] + [httpx.Response(500)] * 12
    )
    client = OpenRouterClient(http, "key", list(MODEL_CHAIN))
    result = await analyze("x.com", snaps(6, 20000), client)

    assert result.check.status is CheckStatus.UNCHECKED
    assert "전수 확인을 못 했습니다" in result.check.note
