import json

import httpx
import pytest
import respx

from domainchecker.analyze.ai import analyze, build_prompt
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
    return [
        SnapshotContent(timestamp=f"{2015 + i}0101000000", text="가" * size, lang="ko")
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
    assert "매입 후보 판정 불가" in result.check.note


async def test_no_readable_text_is_unchecked(http):
    client = OpenRouterClient(http, "key", list(MODEL_CHAIN))
    result = await analyze("x.com", [SnapshotContent(timestamp="20200101000000")], client)

    assert result.check.status is CheckStatus.UNCHECKED
    assert "과거 본문이 없어" in result.check.note
