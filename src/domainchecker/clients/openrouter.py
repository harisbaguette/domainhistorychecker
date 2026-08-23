"""OpenRouter chat/completions with a forced JSON schema and a fixed model chain.

한 번 실패했다고 다음 모델로 넘어가지 않는다. 예전에는 잠깐 막힌 것(429·5xx·시간
초과) 하나에 곧바로 대체 모델로 갈아탔고, 세 모델이 같은 이유로 연달아 막히면
그대로 "AI 분석 실패"가 되어 4단 정독이 통째로 날아갔다. 같은 모델을 몇 번
다시 두드려 보고, 그래도 안 될 때만 다음 모델로 간다.

시간 제한도 여기서 따로 잡는다. 검사 전체가 함께 쓰는 통로는 30초 제한인데,
과거 본문 수만 자를 읽히는 이 호출은 실측 2026-08-23 기준 3만 5천 자에 14.5초가
걸렸다 — 조금만 붐벼도 30초를 넘겨 "접속 실패"로 끊긴다.

답이 길이 제한에 잘려 오는 것도 조용히 넘기지 않는다. 잘린 JSON 은 읽을 수 없어
예전에는 "응답 형식이 올바르지 않음"으로만 남았는데, 원인이 다르면 처방도 다르다 —
잘렸으면 쓸 수 있는 길이를 늘려 한 번 더 물어본다.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from . import http_reason

URL = "https://openrouter.ai/api/v1/chat/completions"

# 과거 본문을 수만 자 읽히는 호출이라 넉넉히 잡는다(실측 14.5초, 붐비면 더 걸린다).
REQUEST_TIMEOUT = httpx.Timeout(180.0, connect=15.0)
# 같은 모델을 다시 두드리기 전에 쉬는 시간(초).
RETRY_WAITS = (2.0, 6.0)
DEFAULT_MAX_TOKENS = 2400  # 실측: 한 묶음 답이 806토큰, 합치는 답은 더 길다


class OpenRouterError(RuntimeError):
    """Every model in the chain failed."""


class _Transient(Exception):
    """잠깐 막힌 것 — 같은 모델을 다시 두드려 볼 만하다."""


class _Truncated(Exception):
    """답이 길이 제한에 잘렸다 — 길이를 늘려 다시 물어야 한다."""


class OpenRouterClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str, models: list[str]) -> None:
        self.http = http
        self.api_key = api_key
        self.models = [m for m in models if m]

    async def _once(self, model: str, system: str, user: str, schema: dict,
                    schema_name: str, max_tokens: int) -> dict:
        """한 모델에게 한 번 물어본다. 다시 해 볼 만한 실패는 예외로 알린다."""
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        try:
            response = await self.http.post(
                URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.HTTPError as exc:
            raise _Transient(f"접속 실패({type(exc).__name__})") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise _Transient(http_reason(response.status_code))
        if response.status_code != 200:
            raise OpenRouterError(http_reason(response.status_code))
        try:
            payload = response.json()
            choice = payload["choices"][0]
            content = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError("응답 형식이 올바르지 않음") from exc
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
        except ValueError as exc:
            # 길이 제한에 잘린 답은 반드시 JSON 이 깨진다 — 원인을 갈라 적는다.
            if str(choice.get("finish_reason") or "") == "length":
                raise _Truncated("답이 길이 제한에 잘렸습니다") from exc
            raise OpenRouterError("응답 형식이 올바르지 않음") from exc
        if not isinstance(parsed, dict):
            raise OpenRouterError("JSON 객체가 아님")
        return parsed

    async def complete_json(
        self,
        system: str,
        user: str,
        schema: dict,
        schema_name: str = "analysis",
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> tuple[dict, str, bool]:
        """Return (parsed json, model actually used, fallback_used).

        모델마다 몇 번씩 다시 물어본 뒤에야 다음 모델로 넘어간다. 어느 모델이
        답했는지는 보고서에 값이 바뀌었다는 각주로 쓰이므로 그대로 돌려준다.
        """
        if not self.api_key:
            raise OpenRouterError("OpenRouter 키가 없습니다.")
        if not self.models:
            raise OpenRouterError("사용할 AI 모델이 지정되지 않았습니다.")

        problems: list[str] = []
        for index, model in enumerate(self.models):
            tokens = max_tokens
            for attempt in range(len(RETRY_WAITS) + 1):
                try:
                    parsed = await self._once(
                        model, system, user, schema, schema_name, tokens
                    )
                except _Truncated as exc:
                    problems.append(f"{model}: {exc}")
                    if tokens >= max_tokens * 4:
                        break  # 늘려도 계속 잘리면 이 모델은 포기한다
                    tokens *= 2
                    continue
                except _Transient as exc:
                    problems.append(f"{model}: {exc}")
                    if attempt < len(RETRY_WAITS):
                        await asyncio.sleep(RETRY_WAITS[attempt])
                        continue
                    break
                except OpenRouterError as exc:
                    problems.append(f"{model}: {exc}")
                    break  # 키 오류·스키마 거절 등은 다시 물어도 같은 답이다
                return parsed, model, index > 0
        raise OpenRouterError("AI 호출 실패 — " + " / ".join(problems))
