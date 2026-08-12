"""AI reading of the archived history — 전수 읽기.

변경본이 한 번에 못 읽을 만큼 많으면 시간 순서대로 여러 묶음으로 나눠 전부
읽힌 뒤(묶음마다 한 번), 묶음별 결과를 합쳐 최종 판정을 한 번 더 받는다.
어느 묶음 하나라도 실패하면 "전수 확인 실패"로 정직하게 미확인 처리한다.
"""

from __future__ import annotations

import json

from ..clients.openrouter import OpenRouterClient, OpenRouterError
from ..models import AIAnalysis, CheckState, CheckStatus, SpamJudgement
from .extract import LANG_LABEL, SnapshotContent

INPUT_LIMIT = 40_000  # AI 호출 한 번의 입력 문자 상한 (≈1.5만 토큰)
_HEADER_ALLOWANCE = 6_000  # 프롬프트 머리(등록 정보·주소 목록 등)에 남겨 두는 몫

SYSTEM_PROMPT = (
    "당신은 만료 도메인을 매입 전에 검수하는 SEO 리스크 심사관이다. "
    "웨이백 머신에 저장된 과거 페이지 본문만 근거로 삼는다. "
    "판단 축은 '무슨 업종이었나'가 아니라 '어떻게 운영했나'이다 — "
    "합법 업종(주류·금융·성인용품 판매 등)이라는 이유만으로 스팸으로 보지 마라. "
    "도어웨이 페이지, 숨긴 글자·링크, 링크 판매, 자동 생성 대량 페이지, 해킹 삽입 같은 "
    "운영 방식의 증거가 있을 때만 스팸으로 판정한다. "
    "스팸 판정에는 반드시 본문에서 그대로 따온 인용을 근거로 넣어라. "
    "본문을 읽지 못한 해는 '과거 주소 흔적'의 경로 목록이 보조 증거다 — 특정 "
    "연도의 경로에 도박·성인·약품 판매성 문구가 몰려 있으면 그 시기의 운영 방식 "
    "증거로 쓰고, 인용에 그 경로를 그대로 적어라. "
    "증거가 부족하면 unclear로 답하고 확신도를 낮춰라. 지어내지 마라. "
    "모든 출력 문장은 한국어로 쓴다."
)

RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "topic_history": {"type": "string", "description": "연도별 주제 변천을 한국어로 서술"},
        "topic_periods": {
            "type": "array",
            "description": "주제가 바뀐 시기별 목록(오래된 것부터). 주제가 하나뿐이면 한 칸만",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "start": {"type": "string", "description": "시작 연도(YYYY)"},
                    "end": {"type": "string", "description": "끝 연도(YYYY)"},
                    "topic": {"type": "string", "description": "그 시기의 주제 한 줄"},
                },
                "required": ["start", "end", "topic"],
            },
        },
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
        "topic_periods",
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
    if context.get("index_paths"):
        paths = "\n".join(f"- {p}" for p in context["index_paths"][:15])
        header.append(
            "# 웹 색인에 남아 있는 주소 흔적(경로만 — 위험 업종·상표 판단에 참고)\n" + paths
        )
    if context.get("history_paths"):
        # 이 묶음이 다루는 연도의 주소 흔적 — 머리 몫 예산 안에서 담고,
        # 못 담은 수는 있는 그대로 적는다(거짓 안내 금지).
        listed: list[str] = []
        used_chars = 0
        for p in context["history_paths"]:
            line = f"- {p}"
            if used_chars + len(line) > _HEADER_ALLOWANCE // 2:
                break
            listed.append(line)
            used_chars += len(line) + 1
        skipped = len(context["history_paths"]) - len(listed)
        note = (
            f"\n(전체 {len(context['history_paths'])}개 중 {len(listed)}개만 여기 담음 — "
            "나머지 주소의 본문은 본문 묶음에서 따로 읽는다)"
            if skipped > 0
            else ""
        )
        header.append(
            "# 과거 저장 기록의 주소 흔적(연도 + 경로 — 본문이 없는 해의 보조 증거)\n"
            + "\n".join(listed) + note
        )
    if context.get("segment"):
        header.append(
            f"# 구간\n이 메시지에는 전체 역사 중 {context['segment']} 부분의 본문만 담겨 있다. "
            "이 구간에서 보이는 것만 근거로 판정하라."
        )
    header.append(
        "# 지시\n아래 과거 페이지 본문을 읽고 스키마대로 한국어 JSON을 채워라. "
        "topic_periods 에는 주제가 바뀐 시기를 오래된 것부터 연도 구간으로 나눠 적어라"
        "(주제가 하나로 이어졌으면 한 칸만). "
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


# ── 수상 주소 선별 — 사람이 주소 목록을 훑다가 이상한 것만 클릭해 보는 단계 ──
TRIAGE_SYSTEM_PROMPT = (
    "당신은 만료 도메인의 과거 주소 목록을 훑는 SEO 리스크 심사관이다. "
    "주소(경로)만 보고, 스팸 운영의 흔적일 가능성이 있어 본문을 직접 읽어 봐야 할 "
    "주소를 고른다. 도박·성인·약품·복제품 판매 냄새가 나는 경로, 해킹으로 심긴 듯 "
    "사이트 주제와 동떨어진 경로, 뜻 없는 문자열로 대량 생성된 패턴, 언어가 갑자기 "
    "바뀐 경로를 우선하라. 평범한 글·회사 페이지 경로는 고르지 마라. "
    "확실하지 않아도 의심스러우면 고른다 — 놓치는 쪽이 훨씬 비싸다."
)

TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "suspicious": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "entry": {"type": "string", "description": "목록의 항목을 그대로"},
                    "reason": {"type": "string", "description": "의심 이유 한 줄"},
                },
                "required": ["entry", "reason"],
            },
        }
    },
    "required": ["suspicious"],
}

MAX_SUSPICIOUS = 20  # 정독할 하위 페이지 상한(선별 후)
_TRIAGE_BATCH_LINES = 1200  # 주소 목록이 아주 길면 이만큼씩 나눠 전부 훑는다


async def pick_paths(
    domain: str,
    path_samples: list[str],
    client: OpenRouterClient | None,
    limit: int = MAX_SUSPICIOUS,
) -> list[str] | None:
    """주소 목록 전체를 AI가 훑어 본문을 정독할 후보를 고른다. None = 선별 실패."""
    if client is None or not client.api_key or not path_samples:
        return []
    chosen: list[str] = []
    for start in range(0, len(path_samples), _TRIAGE_BATCH_LINES):
        batch = path_samples[start : start + _TRIAGE_BATCH_LINES]
        prompt = (
            f"# 검사 대상 도메인\n{domain}\n\n"
            "# 과거 저장 기록의 주소 목록(연도 + 경로)\n"
            + "\n".join(f"- {p}" for p in batch)
            + f"\n\n# 지시\n본문을 직접 읽어 봐야 할 수상한 주소를 최대 {limit}개 골라라. "
            "entry에는 목록의 항목 문자열을 연도까지 그대로 옮겨 적어라. 없으면 빈 목록으로 답하라."
        )
        try:
            data, _, _ = await client.complete_json(
                TRIAGE_SYSTEM_PROMPT, prompt, TRIAGE_SCHEMA, schema_name="suspicious_paths"
            )
        except OpenRouterError:
            return None
        for item in data.get("suspicious") or []:
            if isinstance(item, dict) and str(item.get("entry", "")).strip():
                chosen.append(str(item["entry"]).strip())
    # 목록에 실제로 있는 항목만 남긴다(순서 유지·중복 제거·상한).
    valid = set(path_samples)
    picked: list[str] = []
    for entry in chosen:
        if entry in valid and entry not in picked:
            picked.append(entry)
    return picked[:limit]


def dedupe_texts(snapshots: list[SnapshotContent]) -> list[SnapshotContent]:
    """본문이 완전히 같은 저장분은 한 번만 읽는다 — 같은 글을 두 번 읽는 건 낭비다."""
    seen: set[str] = set()
    out = []
    for snap in snapshots:
        key = snap.text.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(snap)
    return out


def pack_chunks(
    snapshots: list[SnapshotContent], input_limit: int = INPUT_LIMIT
) -> list[list[SnapshotContent]]:
    """시간 순서를 지키며, 한 호출 예산에 들어가는 만큼씩 묶는다. 버리는 장은 없다."""
    budget = max(2_000, input_limit - _HEADER_ALLOWANCE)
    chunks: list[list[SnapshotContent]] = []
    current: list[SnapshotContent] = []
    size = 0
    for snap in snapshots:
        cost = len(snap.text) + 120
        if current and size + cost > budget:
            chunks.append(current)
            current, size = [], 0
        current.append(snap)
        size += cost
    if current:
        chunks.append(current)
    return chunks


def _chunk_context(context: dict, chunk: list[SnapshotContent], i: int, total: int) -> dict:
    """묶음 하나에 붙일 문맥 — 주소 흔적은 그 묶음이 다루는 연도 것만."""
    years = {s.timestamp[:4] for s in chunk}
    scoped = dict(context)
    scoped["history_paths"] = [
        p for p in (context.get("history_paths") or []) if p[:4] in years
    ]
    scoped["segment"] = f"{i + 1}/{total} ({min(years)}~{max(years)}년)"
    scoped.pop("index_paths", None)  # 현재 색인은 통합 판정에서만 본다
    return scoped


_PARTIAL_KEYS = (
    "topic_history", "topic_periods", "spam", "transition", "transition_risk",
    "content_quality", "trademark", "trademark_risk", "one_liner",
)


def build_merge_prompt(domain: str, partials: list[dict], context: dict | None = None) -> str:
    """묶음별 결과(JSON)를 합쳐 최종 판정을 받는 프롬프트."""
    context = context or {}
    header = [f"# 검사 대상 도메인\n{domain}", ""]
    if context.get("registration"):
        header.append(f"# 등록 정보\n{context['registration']}")
    if context.get("timeline"):
        header.append(f"# 저장 이력 요약\n{context['timeline']}")
    if context.get("index_titles"):
        titles = "\n".join(f"- {t}" for t in context["index_titles"][:10])
        header.append(f"# 현재 구글 색인 제목\n{titles}")
    if context.get("index_paths"):
        paths = "\n".join(f"- {p}" for p in context["index_paths"][:15])
        header.append("# 웹 색인에 남아 있는 주소 흔적\n" + paths)
    header.append(
        "# 지시\n아래는 이 도메인의 전체 역사를 시간 순서 구간으로 나눠 각각 분석한 "
        "결과(JSON 목록)다. 전부 합쳐 전체 역사에 대한 최종 판정을 같은 스키마로 내려라. "
        "spam은 가장 심한 구간의 판정을 따르고 quotes에는 그 구간의 인용을 그대로 옮겨라. "
        "topic_periods는 구간들을 이어 붙여 정리하라. 어느 한 구간에라도 위험 운영 증거가 "
        "있으면 전체 판정에 반드시 반영하라(좋은 시기가 나쁜 시기를 덮지 못한다)."
    )
    body = json.dumps(partials, ensure_ascii=False)
    return "\n\n".join(header) + "\n\n# 구간별 분석 결과\n" + body


async def analyze(
    domain: str,
    snapshots: list[SnapshotContent],
    client: OpenRouterClient | None,
    context: dict | None = None,
    input_limit: int = INPUT_LIMIT,
) -> AIAnalysis:
    """전수 읽기 — 묶음별 분석 + 통합 판정. 실패는 전부 UNCHECKED로 정직하게 남긴다."""
    result = AIAnalysis()
    if client is None or not client.api_key:
        result.check = CheckState(
            status=CheckStatus.NOT_RUN,
            note="OpenRouter 키가 없어 AI 분석은 건너뛰었습니다(규칙 검사만으로 판정합니다).",
        )
        return result
    readable = dedupe_texts([s for s in snapshots if s.text])
    if not readable:
        result.check = CheckState(
            status=CheckStatus.UNCHECKED, note="읽을 수 있는 과거 본문이 없어 AI 분석을 못 했습니다."
        )
        return result

    chunks = pack_chunks(readable, input_limit)
    context = context or {}
    fallback = False
    merged_note = ""
    try:
        if len(chunks) == 1:
            prompt = build_prompt(domain, readable, context, input_limit)
            data, model, fallback = await client.complete_json(
                SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA, schema_name="domain_history"
            )
        else:
            # 본문을 한 장도 못 읽은 해의 주소 흔적은 어느 본문 묶음에도 못 실린다 —
            # 그 해들만 모아 "주소 전용 묶음"을 하나 더 돌린다(증거 증발 방지).
            prompts = [
                build_prompt(domain, chunk, _chunk_context(context, chunk, i, len(chunks)), input_limit)
                for i, chunk in enumerate(chunks)
            ]
            covered_years = {s.timestamp[:4] for chunk in chunks for s in chunk}
            orphan_paths = [
                p for p in (context.get("history_paths") or []) if p[:4] not in covered_years
            ]
            if orphan_paths:
                orphan_context = dict(context)
                orphan_context["history_paths"] = orphan_paths
                orphan_context["segment"] = "본문이 남지 않은 해(주소 흔적만 있음)"
                orphan_context.pop("index_paths", None)
                prompts.append(build_prompt(domain, [], orphan_context, input_limit))

            partials = []
            for i, prompt in enumerate(prompts):
                try:
                    part, _, fb = await client.complete_json(
                        SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA, schema_name="domain_history"
                    )
                except OpenRouterError as exc:
                    result.check = CheckState(
                        status=CheckStatus.UNCHECKED,
                        note=f"{len(prompts)}묶음 중 {i + 1}번째 분석이 실패해 전수 확인을 못 했습니다: {exc}",
                    )
                    return result
                fallback = fallback or fb
                partials.append({k: part.get(k) for k in _PARTIAL_KEYS})
            data, model, fb = await client.complete_json(
                SYSTEM_PROMPT,
                build_merge_prompt(domain, partials, context),
                RESPONSE_SCHEMA,
                schema_name="domain_history",
            )
            fallback = fallback or fb
            merged_note = f"본문이 많아 {len(chunks)}묶음으로 나눠 전부 읽고 합쳤습니다."
    except OpenRouterError as exc:
        result.check = CheckState(status=CheckStatus.UNCHECKED, note=str(exc))
        return result

    result.model = model
    result.fallback_used = fallback
    result.topic_history = str(data.get("topic_history", ""))
    result.topic_periods = [
        {
            "start": str(item.get("start", "")).strip()[:4],
            "end": str(item.get("end", "")).strip()[:4],
            "topic": str(item.get("topic", "")).strip(),
        }
        for item in (data.get("topic_periods") or [])
        if isinstance(item, dict) and str(item.get("topic", "")).strip()
    ][:6]
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
    notes = [merged_note] if merged_note else []
    if fallback:
        notes.append(f"기본 모델이 실패해 대체 모델({model})로 분석했습니다.")
    result.check = CheckState(status=CheckStatus.OK, note=" ".join(notes))
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
