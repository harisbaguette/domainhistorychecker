"""AI 호출 통로 — 잠깐 막힌 것 때문에 4단 정독이 통째로 날아가지 않아야 한다.

2026-08-23 회귀 시험. 예전에는 429·5xx·시간 초과 하나에 곧바로 다음 모델로
갈아탔고, 세 모델이 같은 이유로 연달아 막히면 "AI 분석 실패"로 끝났다.
"""

import httpx
import pytest
import respx

from domainchecker.clients.openrouter import OpenRouterClient, OpenRouterError

URL = "https://openrouter.ai/api/v1/chat/completions"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def good(text: str = '{"answer": "네"}', finish: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": finish, "message": {"content": text}}]},
    )


async def ask(client: OpenRouterClient):
    return await client.complete_json("시스템", "사용자", SCHEMA, schema_name="t")


@respx.mock
async def test_a_busy_moment_is_retried_on_the_same_model(http):
    """429 한 번에 대체 모델로 도망가지 않는다 — 같은 모델을 다시 두드린다."""
    route = respx.post(URL).mock(side_effect=[httpx.Response(429), good()])
    data, model, fallback = await ask(OpenRouterClient(http, "key", ["첫째", "둘째"]))

    assert route.call_count == 2
    assert data == {"answer": "네"}
    assert model == "첫째"
    assert fallback is False  # 대체 모델을 쓰지 않았다


@respx.mock
async def test_a_timeout_is_retried_too(http):
    route = respx.post(URL).mock(
        side_effect=[httpx.ConnectError("끊김"), httpx.ReadTimeout("느림"), good()]
    )
    _, model, _ = await ask(OpenRouterClient(http, "key", ["첫째"]))

    assert route.call_count == 3
    assert model == "첫째"


@respx.mock
async def test_it_moves_on_only_after_the_retries_are_spent(http):
    """계속 막히면 그때 대체 모델로 넘어간다."""
    route = respx.post(URL).mock(
        side_effect=[httpx.Response(503)] * 3 + [good()]
    )
    _, model, fallback = await ask(OpenRouterClient(http, "key", ["첫째", "둘째"]))

    assert route.call_count == 4
    assert model == "둘째"
    assert fallback is True


@respx.mock
async def test_a_truncated_answer_is_asked_again_with_more_room(http):
    """길이 제한에 잘린 답은 JSON 이 깨진다 — 길이를 늘려 다시 묻는다."""
    seen: list[int] = []

    def record(request):
        import json as _json

        seen.append(_json.loads(request.content)["max_tokens"])
        return good('{"answer": "잘린', finish="length") if len(seen) == 1 else good()

    respx.post(URL).mock(side_effect=record)
    data, _, _ = await ask(OpenRouterClient(http, "key", ["첫째"]))

    assert len(seen) == 2
    assert seen[1] > seen[0]  # 두 번째는 더 넉넉하게 물었다
    assert data == {"answer": "네"}


@respx.mock
async def test_a_key_problem_is_not_retried(http):
    """키가 틀린 것은 몇 번을 다시 물어도 같은 답이다 — 바로 다음 모델로."""
    route = respx.post(URL).mock(return_value=httpx.Response(401))
    with pytest.raises(OpenRouterError) as caught:
        await ask(OpenRouterClient(http, "key", ["첫째", "둘째"]))

    assert route.call_count == 2  # 모델마다 딱 한 번씩
    assert "키가 틀렸습니다" in str(caught.value)


@respx.mock
async def test_every_model_failing_still_raises(http):
    respx.post(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(OpenRouterError):
        await ask(OpenRouterClient(http, "key", ["첫째", "둘째"]))
