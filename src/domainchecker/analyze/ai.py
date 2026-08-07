"""AI reading of the archived history — one call per domain, JSON schema forced."""

from __future__ import annotations

from ..clients.openrouter import OpenRouterClient, OpenRouterError
from ..models import AIAnalysis, CheckState, CheckStatus, SpamJudgement
from .extract import LANG_LABEL, SnapshotContent

INPUT_LIMIT = 40_000  # 도메인당 입력 문자 상한 (≈1.5만 토큰)

SYSTEM_PROMPT = (
    "당신은 만료 도메인을 매입 전에 검수하는 SEO 리스크 심사관이다. "
    "웨이백 머신에 저장된 과거 페이지 본문만 근거로 삼는다. "
    "판단 축은 '무슨 업종이었나'가 아니라 '어떻게 운영했나'이다 — "
    "합법 업종(주류·금융·성인용품 판매 등)이라는 이유만으로 스팸으로 보지 마라. "
    "도어웨이 페이지, 숨긴 글자·링크, 링크 판매, 자동 생성 대량 페이지, 해킹 삽입 같은 "
    "운영 방식의 증거가 있을 때만 스팸으로 판정한다. "
    "스팸 판정에는 반드시 본문에서 그대로 따온 인용을 근거로 넣어라. "
    "증거가 부족하면 unclear로 답하고 확신도를 낮춰라. 지어내지 마라. "
    "모든 출력 문장은 한국어로 쓴다."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "topic_history": {"type": "string", "description": "연도별 주제 변천을 한국어로 서술"},
        "spam": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "verdict": {"type": "string", "enum": ["spam", "clean", "unclear"]},
                "confidence": {"type": "number"},
                "quotes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["verdict", "confidence", "quotes"],
        },
        "transition": {"type": "string", "description": "전환 방향(정상→정상, 정상→위험 등)"},
        "transition_risk": {
            "type": "boolean",
            "description": "정상→위험 업종·언어 급변 전환이 있었으면 true",
        },
        "content_quality": {"type": "string"},
        "trademark": {"type": "string"},
        "trademark_risk": {"type": "boolean"},
        "recommended_topics": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "topic": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["topic", "reason"],
            },
        },
        "one_liner": {"type": "string", "description": "한 줄 평가"},
    },
    "required": [
        "topic_history",
        "spam",
        "transition",
        "transition_risk",
        "content_quality",
        "trademark",
        "trademark_risk",
        "recommended_topics",
        "one_liner",
    ],
}


def build_prompt(
    domain: str,
    snapshots: list[SnapshotContent],
    context: dict | None = None,
    input_limit: int = INPUT_LIMIT,
) -> str:
    """Assemble the user message, hard-capped at `input_limit` characters."""
    context = context or {}
    header = [f"# 검사 대상 도메인\n{domain}", ""]
    if context.get("registration"):
        header.append(f"# 등록 정보\n{context['registration']}")
    if context.get("timeline"):
        header.append(f"# 저장 이력 요약\n{context['timeline']}")
    if context.get("index_titles"):
        titles = "\n".join(f"- {t}" for t in context["index_titles"][:10])
        header.append(f"# 현재 구글 색인 제목\n{titles}")
    if context.get("rule_hints"):
        hints = "\n".join(f"- {h}" for h in context["rule_hints"][:10])
        header.append(f"# 규칙 검사가 잡은 흔적(참고)\n{hints}")
    if context.get("sensitive_terms"):
        terms = ", ".join(context["sensitive_terms"][:20])
        header.append(
            "# 주의 표지(업종 낱말 — 그 자체로는 스팸 근거가 아님)\n" + terms
        )
    header.append(
        "# 지시\n아래 과거 페이지 본문을 읽고 스키마대로 한국어 JSON을 채워라. "
        "추천 주제는 과거 주제와 인접하면서 사람에게 도움이 되는 것으로 3개 이상 제시하고 사유를 붙여라."
    )

    prefix = "\n\n".join(header) + "\n\n# 과거 페이지 본문\n"
    budget = max(0, input_limit - len(prefix))
    if not snapshots:
        return prefix + "(저장된 본문이 없습니다.)"

    per_snapshot = max(300, budget // max(1, len(snapshots)))
    blocks = []
    used = 0
    for snap in snapshots:
        if not snap.text:
            continue
        label = (
            f"## {snap.timestamp[:4]}-{snap.timestamp[4:6]} "
            f"[{LANG_LABEL.get(snap.lang, snap.lang)}]"
            f"{' (파킹 페이지)' if snap.parking else ''}\n"
        )
        if snap.title:
            label += f"제목: {snap.title}\n"
        block = label + snap.text[:per_snapshot]
        if used + len(block) > budget:
            block = block[: max(0, budget - used)]
            if block:
                blocks.append(block)
            break
        blocks.append(block)
        used += len(block) + 2
    body = "\n\n".join(blocks) if blocks else "(저장된 본문이 없습니다.)"
    return prefix + body


async def analyze(
    domain: str,
    snapshots: list[SnapshotContent],
    client: OpenRouterClient | None,
    context: dict | None = None,
    input_limit: int = INPUT_LIMIT,
) -> AIAnalysis:
    """One AI call; any failure becomes UNCHECKED so the pipeline keeps going."""
    result = AIAnalysis()
    if client is None or not client.api_key:
        result.check = CheckState(
            status=CheckStatus.NOT_RUN,
            note="OpenRouter 키가 없어 AI 분석은 건너뛰었습니다(규칙 검사만으로 판정합니다).",
        )
        return result
    readable = [s for s in snapshots if s.text]
    if not readable:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note="읽을 수 있는 과거 본문이 없어 AI 분석을 못 했습니다."
        )
        return result

    prompt = build_prompt(domain, readable, context, input_limit)
    try:
        data, model, fallback = await client.complete_json(
            SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA, schema_name="domain_history"
        )
    except OpenRouterError as exc:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note=str(exc))
        return result

    result.model = model
    result.fallback_used = fallback
    result.topic_history = str(data.get("topic_history", ""))
    result.transition = str(data.get("transition", ""))
    result.transition_risk = bool(data.get("transition_risk", False))
    result.content_quality = str(data.get("content_quality", ""))
    result.trademark = str(data.get("trademark", ""))
    result.trademark_risk = bool(data.get("trademark_risk", False))
    result.one_liner = str(data.get("one_liner", ""))
    result.recommended_topics = [
        {"topic": str(item.get("topic", "")), "reason": str(item.get("reason", ""))}
        for item in (data.get("recommended_topics") or [])
        if isinstance(item, dict) and item.get("topic")
    ]
    result.spam = _parse_spam(data.get("spam"))
    note = "" if fallback is False else f"기본 모델이 실패해 대체 모델({model})로 분석했습니다."
    result.check = CheckState(status=CheckStatus.OK, note=note)
    return result


def _parse_spam(raw: object) -> SpamJudgement:
    if not isinstance(raw, dict):
        return SpamJudgement()
    verdict = str(raw.get("verdict", "unknown")).lower()
    if verdict not in ("spam", "clean", "unclear"):
        verdict = "unknown"
    try:
        confidence = float(raw.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = min(1.0, max(0.0, confidence))
    quotes = [str(q).strip() for q in (raw.get("quotes") or []) if str(q).strip()]
    return SpamJudgement(verdict=verdict, confidence=confidence, quotes=quotes[:5])
