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
                    "reason": {"type": "string", "description": "과거의 어떤 시기·주제를 이어받아 상위노출에 유리한지"},
                },
                "required": ["topic", "reason"],
            },
        },
        "one_liner": {"type": "string", "description": "한 줄 평가"},
        "verdict": {
            "type": "string",
            "enum": ["buy", "review", "reject"],
            "description": "최종 매입 권고 — buy(사도 됨)/review(사람 검토 필요)/reject(사면 안 됨)",
        },
        "buy_score": {
            "type": "number",
            "description": "매입 매력도 0~100 — 여러 도메인을 줄 세우는 비교용. 과거 이력의 깨끗함과 이어받을 연속성 가치(주제 일관성·운영 기간·검색에 남은 자산)를 종합",
        },
        "verdict_reason": {"type": "string", "description": "판정 이유 한 줄(본문·주소에서 본 증거 기반)"},
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
        "verdict",
        "buy_score",
        "verdict_reason",
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
        "추천 주제(recommended_topics)는 이 도메인을 산 사람이 검색 상위노출을 노리고 "
        "운영할 주제다 — 과거 주제·언어의 연속성을 이어받는 것 3개 이상, 이유에는 "
        "과거의 어떤 시기·주제를 잇는지 적어라. "
        "verdict 는 최종 매입 권고(buy/review/reject), buy_score 는 여러 도메인을 "
        "줄 세우는 매입 매력도(0~100)다 — 깨끗함과 연속성 가치를 종합해 매겨라."
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


# ── 3단 — 가장 최근 화면 딱 한 장만 보고 "지금 이게 스팸인가"를 묻는다 ──
SCREEN_SYSTEM_PROMPT = (
    "당신은 만료 도메인을 매입 전에 검수하는 SEO 리스크 심사관이다. "
    "지금 보여 주는 것은 이 도메인의 **가장 최근에 저장된 화면 한 장**뿐이다. "
    "이 한 장만 보고 '지금 이 도메인이 스팸으로 굴러가고 있는가'만 답하라. "
    "spam으로 답해도 되는 경우는 이 한 장에 스팸 운영의 증거가 그대로 보일 때뿐이다 — "
    "도어웨이 페이지, 숨긴 글자·링크, 링크 판매, 자동 생성 대량 페이지, 해킹 삽입 문구, "
    "불법 도박·성인물·의약품·복제품 판매 같은 위험 업종 영업 화면. "
    "판매용 빈 화면(파킹)이나 접속 오류 화면은 그 자체로는 스팸이 아니다 — "
    "만료를 앞둔 도메인은 대부분 파킹 화면이므로 파킹만 보이면 unclear로 답하라. "
    "합법 업종이라는 이유만으로 스팸으로 보지도 마라. "
    "spam 판정에는 반드시 화면 본문에서 그대로 따온 인용을 근거로 넣어라. "
    "조금이라도 애매하면 unclear로 답하라 — 여기서 잘못 떨어뜨리면 멀쩡한 도메인을 잃는다. "
    "모든 출력 문장은 한국어로 쓴다."
)

SCREEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["spam", "clean", "unclear"]},
        "confidence": {"type": "number", "description": "0~1"},
        "quotes": {"type": "array", "items": {"type": "string"}, "description": "본문에서 그대로 따온 근거"},
    },
    "required": ["verdict", "confidence", "quotes"],
}

SCREEN_TEXT_LIMIT = 12_000  # 한 장짜리 검사라 통째로 넣어도 한 호출 예산 안이다


async def screen_latest(
    domain: str, snapshot: SnapshotContent, client: OpenRouterClient | None
) -> SpamJudgement | None:
    """가장 최근 화면 한 장만 보고 스팸인지 묻는다. None = 못 물어봤다.

    None은 "합격"이 아니라 "판정 불가"다 — 부르는 쪽은 이걸 통과로 읽으면 안 되고
    다음 단(옛 화면 전수 정독)으로 내려보내야 한다.
    """
    if client is None or not client.api_key or not snapshot.text:
        return None
    prompt = (
        f"# 검사 대상 도메인\n{domain}\n\n"
        f"# 가장 최근 저장 화면 ({snapshot.timestamp[:4]}-{snapshot.timestamp[4:6]}"
        f", {LANG_LABEL.get(snapshot.lang, snapshot.lang)}"
        f"{', 파킹 페이지로 보임' if snapshot.parking else ''})\n"
        + (f"제목: {snapshot.title}\n" if snapshot.title else "")
        + snapshot.text[:SCREEN_TEXT_LIMIT]
        + "\n\n# 지시\n이 한 장만 보고 스키마대로 한국어 JSON을 채워라. "
        "확정적인 스팸 증거가 이 화면에 보일 때만 spam으로 답하고, "
        "그 근거를 quotes에 본문 그대로 옮겨라. 애매하면 unclear."
    )
    try:
        data, _, _ = await client.complete_json(
            SCREEN_SYSTEM_PROMPT, prompt, SCREEN_SCHEMA, schema_name="latest_screen", max_tokens=600
        )
    except OpenRouterError:
        return None
    return _parse_spam(data)


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
    "verdict", "buy_score", "verdict_reason",
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
    verdict = str(data.get("verdict", "")).lower()
    result.verdict = verdict if verdict in ("buy", "review", "reject") else ""
    try:
        raw_score = data.get("buy_score")
        result.buy_score = min(100.0, max(0.0, float(raw_score))) if raw_score is not None else None
    except (TypeError, ValueError):
        result.buy_score = None
    result.verdict_reason = str(data.get("verdict_reason", ""))
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
