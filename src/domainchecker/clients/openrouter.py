"""OpenRouter chat/completions with a forced JSON schema and a fixed model chain."""

from __future__ import annotations

import json

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(RuntimeError):
    """Every model in the chain failed."""


class OpenRouterClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str, models: list[str]) -> None:
        self.http = http
        self.api_key = api_key
        self.models = [m for m in models if m]

    async def complete_json(
        self,
        system: str,
        user: str,
        schema: dict,
        schema_name: str = "analysis",
        max_tokens: int = 1600,
    ) -> tuple[dict, str, bool]:
        """Return (parsed json, model actually used, fallback_used).

        Walks the fixed fallback chain; the model name is recorded so the report
        can footnote a price change when a fallback answered.
        """
        if not self.api_key:
            raise OpenRouterError("OpenRouter 키가 없습니다.")
        if not self.models:
            raise OpenRouterError("사용할 AI 모델이 지정되지 않았습니다.")

        problems: list[str] = []
        for index, model in enumerate(self.models):
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
                )
            except httpx.HTTPError as exc:
                problems.append(f"{model}: 접속 실패({type(exc).__name__})")
                continue
            if response.status_code != 200:
                problems.append(f"{model}: 응답 오류({response.status_code})")
                continue
            try:
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                parsed = json.loads(content) if isinstance(content, str) else content
            except (ValueError, KeyError, IndexError, TypeError):
                problems.append(f"{model}: 응답 형식이 올바르지 않음")
                continue
            if not isinstance(parsed, dict):
                problems.append(f"{model}: JSON 객체가 아님")
                continue
            return parsed, model, index > 0
        raise OpenRouterError("AI 호출 실패 — " + " / ".join(problems))
