"""HTML report writer.

`detail_fragment` is the single source of truth for the per-domain view: the
static report pages and the browser UI both render it, so the two can never
drift apart.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
from collections import Counter
from html import escape
from pathlib import Path
from urllib.parse import quote

from ..cache import safe_name
from ..capture import period_label
from ..models import (
    AVAILABILITY_LABEL,
    CHECK_LABEL,
    VERDICT_LABEL,
    CheckStatus,
    DomainResult,
    Verdict,
)

DISCLAIMER = (
    "이 검사 결과는 “무위험 보증”이 아닙니다. 클로킹(검색엔진에만 다른 화면을 보여주는 수법)이나 "
    "해킹으로 몰래 심어진 스팸은 저장된 과거 화면만으로는 구조적으로 놓칠 수 있습니다. "
    "돈을 걸기 전에 상세 근거를 직접 눈으로 확인하세요."
)

LIMITS = [
    "저장된 이력이 없는 것과 열람이 차단된 것은 다릅니다 — 뒤쪽은 이력을 숨겼을 가능성이 있습니다.",
    "백링크 상세는 무료로 받을 수 있는 자료가 없어 근거가 얇습니다(색인 잔존으로 갈음).",
    "상표 검사는 도메인 이름 문자열과 AI 소견 수준의 보조 검사입니다.",
    "OpenRouter 키가 없으면 과거 본문은 AI가 아니라 규칙 검사(기계적 판단)만으로 봅니다 — 문맥을 읽어야 잡히는 것은 놓칠 수 있습니다.",
    "색인 수는 추정치입니다 — 공개 크롤 기록에 남은 주소 수(최대 200)입니다.",
    "구매 가격·경매 시세와 주인이 몇 번 바뀌었는지는 다루지 않습니다.",
]

SCORE_GUIDE = "75점부터 매입 후보(초록) · 50점 밑은 제외(빨강) · 나머지는 검토 필요(노랑)"

# 어려운 낱말은 제목·라벨에서 곧바로 풀어 준다(따로 용어집을 찾아가지 않게).
INDEX_LABEL = "웹에 남아 있는 페이지(색인)"
REDIRECT_LABEL = "다른 곳으로 넘겨보낸 비율(리다이렉트)"
PARKING_LABEL = "‘도메인 팝니다’ 임시 화면이던 비중(파킹)"

SPAM_VERDICT_LABEL = {
    "spam": "스팸으로 봄(spam)",
    "clean": "깨끗함(clean)",
    "unclear": "판단 유보(unclear)",
    "unknown": "확인 안 됨(unknown)",
}

VERDICT_ORDER = [Verdict.BUY, Verdict.REVIEW, Verdict.NO_HISTORY, Verdict.REJECT]
# DW badge 의 tone 이름. 색은 tokens.css 한 곳에서만 나온다.
VERDICT_TONE = {
    Verdict.BUY: "success",
    Verdict.REVIEW: "warning",
    Verdict.NO_HISTORY: "warning",
    Verdict.REJECT: "error",
}
AVAILABILITY_TONE = {
    "free": "success",
    "soon": "warning",
    "auction": "warning",
    "taken": "neutral",
    "unknown": "neutral",
}

# ── 스타일시트 — DW 정본 복사본을 합쳐서 만든다 ────────────────────────────
# 값을 이 파일에 베껴 두지 않는다. static/dw/ 아래 원본을 순서대로 읽어 잇고,
# 이 앱만의 레이아웃(static/app.css)을 맨 뒤에 얹는다. 서버는 이것을 /style.css 로
# 내려주고, 보고서 HTML 은 같은 내용을 <style> 로 끼워 넣는다 — zip 을 풀어 파일로
# 열어도 모양이 나와야 하므로.
STATIC_DIR = Path(__file__).resolve().parents[3] / "static"
DW_DIR = STATIC_DIR / "dw"


def _read_css(path: Path) -> str:
    """없거나 못 읽는 파일은 조용히 건너뛴다 — 스타일이 빠져도 앱은 살아 있어야 한다."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def build_stylesheet() -> str:
    parts = [
        _read_css(DW_DIR / "tokens.css"),
        _read_css(DW_DIR / "base.css"),
        # '읽는 글'(상세 근거·보고서) 조판. DW 가 주는 정본이라 앱이 다시 만들지 않는다.
        _read_css(DW_DIR / "typography.css"),
    ]
    # 블록(화면 뼈대)은 부품을 짜 맞춘 것이라 부품 뒤에 온다.
    for folder in ("ui", "blocks"):
        one_dir = DW_DIR / folder
        if one_dir.is_dir():
            parts += [_read_css(one) for one in sorted(one_dir.glob("*.css"))]
    parts.append(_read_css(STATIC_DIR / "app.css"))
    joined = "\n".join(part for part in parts if part.strip())
    if not joined:
        return "/* static/dw 를 찾지 못해 스타일 없이 보여 줍니다. */"
    # 이 스타일은 보고서 HTML 한 장 한 장에 통째로 들어간다(파일 하나만 떼어
    # 보내도 모양이 나오게). 그래서 설명 주석과 빈 줄은 빼고 담는다 — 원본
    # static/dw/*.css 는 주석까지 그대로 남아 있다.
    joined = re.sub(r"/\*.*?\*/", "", joined, flags=re.S)
    return re.sub(r"[ \t]*\n[ \t\n]*", "\n", joined).strip()


CSS = build_stylesheet()

# 모양 규칙을 앱 켤 때 한 번만 읽어 두면, CSS 를 고쳐도 앱을 껐다 켜기 전에는 화면이 옛 모양
# 그대로다(실제로 새 화면이 안 나온다는 신고가 있었다). 그래서 스타일 파일들이 마지막으로
# 바뀐 시각을 보고, 하나라도 바뀌었으면 그 자리에서 다시 읽는다.
_css_cache: dict[str, object] = {"stamp": None, "text": CSS}


def _style_stamp() -> float:
    """모양 규칙에 쓰이는 파일들이 마지막으로 바뀐 시각 중 가장 늦은 것."""
    latest = 0.0
    for one in [STATIC_DIR / "app.css", *DW_DIR.rglob("*.css")]:
        try:
            latest = max(latest, one.stat().st_mtime)
        except OSError:
            continue
    return latest


def current_css() -> str:
    """지금 파일에 적혀 있는 모양 규칙. 안 바뀌었으면 읽어 둔 것을 그대로 돌려준다."""
    stamp = _style_stamp()
    if _css_cache["stamp"] != stamp:
        _css_cache["stamp"] = stamp
        _css_cache["text"] = build_stylesheet()
    return str(_css_cache["text"])

# lucide 아이콘(ISC) 원본 path 를 그대로 인라인한다 — DW 부품이 아이콘 자리를 전제로
# 만들어졌고, 이 앱에는 번들러가 없어 lucide-react 를 못 쓴다.
def _icon(paths: str, size: int = 20, cls: str = "") -> str:
    return (
        f'<svg class="{cls}" xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{paths}</svg>'
    )


ICON_WARNING = _icon(
    '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
    '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    cls="dw-alert-icon",
)
ICON_CHEVRON = _icon('<path d="m9 18 6-6-6-6"/>')
ICON_INFO = _icon(
    '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    cls="dw-alert-icon",
)
ICON_SUCCESS = _icon(
    '<path d="M21.801 10A10 10 0 1 1 17 3.335"/><path d="m9 11 3 3L22 4"/>',
    cls="dw-alert-icon",
)
# DW alert.jsx 규칙: warning·error 는 급한 소식이라 role="alert", 나머지는 role="status".
# 아이콘도 variant 마다 다르다 — 안내에 경고 삼각형을 그리면 뜻이 뒤집힌다.
ALERT_FACE = {
    "warning": ("alert", ICON_WARNING),
    "error": ("alert", ICON_WARNING),
    "info": ("status", ICON_INFO),
    "success": ("status", ICON_SUCCESS),
}


def _alert(text: str, variant: str = "warning") -> str:
    """DW alert — 아이콘 한 칸 + 설명 한 칸."""
    role, icon = ALERT_FACE.get(variant, ALERT_FACE["warning"])
    return (
        f'<div class="dw-alert" data-variant="{variant}" role="{role}">{icon}'
        f'<p class="dw-alert-description">{text}</p></div>'
    )


def _item(
    title: str,
    lines: list[str],
    *,
    href: str | None = None,
    footer: str = "",
    variant: str = "default",
    title_class: str = "",
) -> str:
    """DW list-item 한 줄. 폰 폭(440px)에 표를 우겨넣지 않는다 — 목록 한 줄이 DW 의 답이다."""
    body = (
        '<span class="dw-list-item-content">'
        f'<span class="dw-list-item-title{" " + title_class if title_class else ""}">{title}</span>'
        + "".join(f'<span class="dw-list-item-description">{line}</span>' for line in lines if line)
        + "</span>"
    )
    if href is not None:
        body += f'<span class="dw-list-item-actions">{ICON_CHEVRON}</span>'
    if footer:
        body += f'<span class="dw-list-item-footer">{footer}</span>'
    tag = "a" if href is not None else "div"
    where = f' href="{href}"' if href is not None else ""
    return (
        f'<{tag} class="dw-list-item" data-variant="{variant}" data-size="default"{where}>'
        f"{body}</{tag}>"
    )


def _item_group(items: list[str]) -> str:
    return f'<div class="dw-list-item-group">{"".join(items)}</div>'


# DW·ECC 공통 규칙: 화면에 이모지를 쓰지 않는다. 그런데 이 앱의 글 가운데 일부는
# 우리가 쓴 것이 아니라 AI 가 써 보낸 것이고(한줄평·소견), 예전에 저장해 둔 결과에도
# 옛 문구가 그대로 남아 있다 — 거기 이모지가 섞여 들어오면 화면에 그대로 찍힌다.
# 그래서 글자를 내보내는 길목 한 곳에서 지운다(그림 문자·이모지 변형 표시).
# 화살표(→)는 지우지 않는다 — "정상→위험" 처럼 뜻을 나르는 자리에 쓰고 있다.
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿⬀-⯿️⃣]")


def strip_emoji(text: str) -> str:
    """이모지를 지우고 그 자리에 생긴 겹공백을 하나로 줄인다."""
    return re.sub(r"[ \t]{2,}", " ", _EMOJI.sub("", text)).strip()


def _t(value) -> str:
    return escape(strip_emoji(str(value if value is not None else "")))


def _when(stamp: str) -> str:
    """2026-08-05T11:42… → 2026년 8월 5일 11:42. 기계 표기를 사람이 읽는 말로."""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})", stamp or "")
    if not match:
        return stamp or "-"
    year, month, day, clock = match.groups()
    return f"{year}년 {int(month)}월 {int(day)}일 {clock}"


def _score_text(result: DomainResult) -> str:
    """점수 = AI가 매긴 매입 매력도(여러 도메인 비교용)."""
    if result.score is None:
        return "점수 없음"
    return f"{result.score:.0f}점" + ("(부분 점수·참고치)" if result.partial_score else "")


def _stamp(result: DomainResult) -> str:
    """판정 도장 — 표·상세 어디서든 같은 모양으로 찍힌다. 채운 배지라 가장 크게 읽힌다."""
    tone = VERDICT_TONE.get(result.verdict, "warning")
    label = result.verdict_label or VERDICT_LABEL[result.verdict]
    return f'<span class="dw-badge" data-tone="{tone}">{_t(label)}</span>'


def _avail(result: DomainResult) -> str:
    """지금 살 수 있나 배지 — 이 도구의 존재 이유라 판정 바로 옆에 둔다.
    도장보다 한 단계 조용하도록 면을 걷어낸 outline(모르는 것은 테두리도 없는 ghost)."""
    key = result.availability if result.availability in AVAILABILITY_LABEL else "unknown"
    label = result.availability_label or AVAILABILITY_LABEL[key]
    tone = AVAILABILITY_TONE.get(key, "neutral")
    variant = "ghost" if key == "unknown" else "outline"
    return f'<span class="dw-badge" data-tone="{tone}" data-variant="{variant}">{_t(label)}</span>'


def _check_line(check) -> str:
    """검사 한 줄. 안 돌린 검사를 '-' 로 두면 '깨끗함'으로 읽힌다 — 안 했다고 적는다."""
    if check.note:
        return _t(check.note)
    if check.status is CheckStatus.NOT_RUN:
        return "검사하지 않았습니다(키가 없거나 꺼 둠) — 깨끗하다는 뜻이 아닙니다."
    if check.status is CheckStatus.UNCHECKED:
        return "검사했지만 답을 못 받았습니다 — 깨끗하다는 뜻이 아닙니다."
    return "이상 없음."


def _list(items: list[str], empty: str = "없음") -> str:
    if not items:
        return f'<p class="muted">{escape(empty)}</p>'
    return "<ul>" + "".join(f"<li>{_t(i)}</li>" for i in items) + "</ul>"


def recheck_links(domain: str) -> list[tuple[str, str]]:
    """Places to verify the machine's answer by hand."""
    d = quote(domain)
    name = quote(domain.split(".")[0])
    return [
        ("구글 색인 직접 보기 (site:)", f"https://www.google.com/search?q=site%3A{d}"),
        (
            "백링크(다른 사이트가 건 링크) 무료 검사 — Ahrefs",
            f"https://ahrefs.com/backlink-checker/?input={d}&mode=subdomains",
        ),
        ("도메인 힘 점수 — Moz", f"https://moz.com/domain-analysis?site={d}"),
        ("주인 바뀐 이력 — Whoisology", f"https://whoisology.com/{d}"),
        ("웨이백 전체 타임라인", f"https://web.archive.org/web/*/{d}*"),
        ("키프리스 상표 검색(한국)", "https://www.kipris.or.kr/khome/main.do"),
        ("상표 이름 구글 검색", f"https://www.google.com/search?q=%22{name}%22+%EC%83%81%ED%91%9C"),
    ]


def _timeline(result: DomainResult) -> str:
    counts = result.wayback.year_counts
    if not counts:
        return '<p class="muted">저장된 이력이 없습니다.</p>'
    top = max(counts.values())
    years = sorted(counts)
    # 폰 폭에서는 막대가 20px 아래로 내려간다 — 막대마다 해를 적으면 글자가 서로 겹쳐
    # 읽을 수 없다. 그래서 해는 양 끝에만 적고, 막대 하나하나는 마우스를 올리면 알려 준다.
    bars = "".join(
        f'<div style="height:{max(4, int(counts[y] / top * 56))}px" '
        f'title="{_t(y)}년 {counts[y]}건"></div>'
        for y in years
    )
    scale = (
        f'<p class="app-years-scale muted"><span>{_t(years[0])}년</span>'
        f"<span>{_t(years[-1])}년</span></p>"
    )
    gaps = (
        f'<p class="muted">기록이 비어 있는 해: {", ".join(str(g) for g in result.wayback.gap_years)}</p>'
        if result.wayback.gap_years
        else ""
    )
    # 막대와 해 표시는 한 덩어리다 — 따로 두면 글 리듬이 둘 사이를 벌려 놓는다.
    return f'<div class="app-timeline"><div class="app-years">{bars}</div>{scale}</div>{gaps}'


def _figure(shot, capture_base: str) -> str:
    return (
        f"<figure><img src=\"{_t(capture_base.rstrip('/'))}/{_t(Path(shot.file).name)}\" "
        f'alt="{_t(shot.label)} 캡쳐" loading="lazy">'
        f"<figcaption class=\"muted\">{_t(shot.label)} · {_t(shot.timestamp[:8])} "
        f'(<a href="{_t(shot.url)}" target="_blank" rel="noopener">원본 보기</a>)</figcaption></figure>'
    )


def _captures(result: DomainResult, capture_base: str, skip: set[str] | None = None) -> str:
    items = [s for s in result.captures.items if s.timestamp not in (skip or set())]
    if not items:
        if skip:
            return '<p class="muted">남은 캡쳐는 위 주제 역사에 다 실었습니다.</p>'
        return f'<p class="muted">{_t(result.captures.check.note or "저장된 캡쳐가 없습니다.")}</p>'
    figures = "".join(_figure(shot, capture_base) for shot in items)
    return f'<div class="app-shots">{figures}</div>'


def _topic_story(result: DomainResult, capture_base: str) -> tuple[str, set[str]]:
    """주제 시기별 역사 — 시기 한 줄 + 그 시기에 찍은 캡쳐 한 장씩.

    반환하는 timestamp 묶음은 여기 이미 실은 사진들이다 — 캡쳐 칸이 같은 사진을
    두 번 보여 주지 않도록 건네준다.
    """
    periods = result.ai.topic_periods
    if not periods:
        text = result.ai.topic_history or "AI 분석을 하지 않아 주제 역사를 못 읽었습니다."
        return f"<p>{_t(text)}</p>", set()
    used: set[str] = set()
    rows = []
    for period in periods:
        label = period_label(period)
        shot = next((s for s in result.captures.items if s.label == label), None)
        figure = ""
        if shot is not None:
            used.add(shot.timestamp)
            figure = f'<div class="app-shots">{_figure(shot, capture_base)}</div>'
        start = str(period.get("start", ""))[:4]
        end = str(period.get("end", ""))[:4]
        span = f"{start}~{end}" if start and end and start != end else (start or end)
        rows.append(
            f"<li><b>{_t(span)}</b> — {_t(period.get('topic', ''))}{figure}</li>"
        )
    summary = (
        f"<p>{_t(result.ai.topic_history)}</p>" if result.ai.topic_history else ""
    )
    return f"<ol>{''.join(rows)}</ol>{summary}", used


def detail_fragment(result: DomainResult, capture_base: str = "../captures") -> str:
    """The full evidence view for one domain (no <html> wrapper)."""
    registration = result.registration
    age = result.wayback.age_years
    # 수제 감점표는 폐지 — 판정 근거는 AI 종합 소견 한 줄로 보여 준다.
    ai_opinion = (
        f"<p><b>AI 종합 소견:</b> {_t(result.ai.verdict_reason)}"
        + (
            f' <span class="muted">(매입 매력도 {result.ai.buy_score:.0f}점 — 여러 도메인 비교용)</span>'
            if result.ai.buy_score is not None
            else ""
        )
        + "</p>"
        if result.ai.verdict_reason or result.ai.buy_score is not None
        else '<p class="muted">AI 종합 소견 없음(AI 분석이 돌지 않음)</p>'
    )
    topics = "".join(
        f"<li><b>{_t(t.get('topic'))}</b> — {_t(t.get('reason'))}</li>"
        for t in result.recommended_topics
    )
    # 인용은 민무늬 blockquote — 모양은 DW 프로즈 정본(typography.css)이 낸다.
    quotes = "".join(f"<blockquote>{_t(q)}</blockquote>" for q in result.ai.spam.quotes)
    links = "".join(
        f'<li><a href="{_t(url)}" target="_blank" rel="noopener">{_t(label)}</a></li>'
        for label, url in recheck_links(result.domain)
    )
    fallback_note = (
        f'<p class="muted">기본 모델이 실패해 대체 모델 {_t(result.ai.model)}로 분석했습니다 — 단가가 다를 수 있습니다.</p>'
        if result.ai.fallback_used
        else ""
    )
    index_line = (
        f"색인 {result.index.indexed_count}건"
        + (" · " + _t(result.index.check.note) if result.index.check.note else "")
        if result.index.check.ok
        else "못 쟀음 — " + _t(result.index.check.note or "확인하지 못했습니다.")
    )
    spam_verdict = SPAM_VERDICT_LABEL.get(result.ai.spam.verdict, result.ai.spam.verdict)
    # 규칙 검사를 못 했는데 "특별한 흔적 없음"이라고 쓰면 못 한 것이 깨끗함으로 읽힌다.
    rules_evidence = (
        _list(result.rules.evidence, "특별한 흔적 없음")
        if result.rules.check.ok
        else '<p class="muted">'
        + _t(result.rules.check.note or "규칙 검사를 못 했습니다.")
        + " (흔적이 없다는 뜻이 아닙니다)</p>"
    )
    facts = [
        ("지금 살 수 있나", _t(result.availability_label or AVAILABILITY_LABEL["unknown"])),
        ("등록일", _t(registration.created or "알 수 없음")),
        ("만료일", _t(registration.expires or "알 수 없음")),
        (
            "저장 이력 기간",
            f"약 {age:.0f}년 ({_t(result.wayback.first_seen[:6])}~{_t(result.wayback.last_seen[:6])})",
        ),
        ("재등록(드랍) 이력", "있음" if registration.redropped else "확인 안 됨"),
        ("등록 자료 출처", _t(registration.source or "없음")),
    ]
    fact_cards = "".join(
        '<div class="dw-card" data-elevation="float" data-size="sm">'
        f'<p class="dw-card-description">{label}</p>'
        f'<p class="dw-card-title">{value}</p></div>'
        for label, value in facts
    )
    story, shown_shots = _topic_story(result, capture_base)
    # 도메인 이름은 제목 줄(dw-block-shell-title)이 든다 — 붙어 있어서 굴러도 안 사라진다.
    # 여기서 한 번 더 적으면 한 페이지에 h1 이 둘이 되고 첫 화면을 한 줄 낭비한다.
    return f"""
<div class="app-badges">{_stamp(result)}{_avail(result)}</div>
<p class="muted">{_t(_score_text(result))} · 취득 상태: {_t(result.acquisition)} · 검사 끝난 때 {_t(_when(result.finished_at))}</p>
<p>{_t(result.one_liner or "한줄평 없음")}</p>

<h2>1. 왜 이렇게 판정했나</h2>
{ai_opinion}
<h3>사면 안 되는 이유</h3>{_list(result.fatal_reasons, "없음")}
<h3>조심할 이유</h3>{_list(result.warn_reasons, "없음")}

<h2>2. 나이와 등록 정보</h2>
<div class="app-grid">{fact_cards}</div>

<h2>3. 저장 이력 타임라인</h2>
{_timeline(result)}
<p class="muted">총 {result.wayback.total_captures}건 · {REDIRECT_LABEL} {result.wayback.redirect_ratio:.0%} ·
{PARKING_LABEL} {result.rules.parking_ratio:.0%}</p>
{f'<p class="muted">확인 범위: {_t(result.wayback.coverage_note)}</p>' if result.wayback.coverage_note else ""}

<h2>4. 과거에 무엇을 하던 도메인인가</h2>
{story}
<p><b>전환 방향:</b> {_t(result.ai.transition or "확인 안 됨")}</p>
<p><b>콘텐츠 품질:</b> {_t(result.ai.content_quality or "확인 안 됨")}</p>
<p><b>상표 소견:</b> {_t(result.ai.trademark or "확인 안 됨")}</p>
{fallback_note}

<h2>5. 사면 이어가기 좋은 주제 (AI 추천)</h2>
{f"<ul>{topics}</ul>" if topics else '<p class="muted">추천 주제 없음</p>'}

<h2>6. 그 밖의 캡쳐</h2>
{_captures(result, capture_base, shown_shots)}

<h2>7. 신호와 근거</h2>
<h3>운영방식 규칙이 찾은 흔적</h3>{rules_evidence}
<h3>AI 스팸성 판정</h3>
<p>{_t(spam_verdict)} · 확신도 {result.ai.spam.confidence:.2f}</p>
{quotes or '<p class="muted">인용된 근거 없음</p>'}
<h3>{INDEX_LABEL}</h3>
<p>{index_line}</p>
{_list(result.index.titles[:10], "색인된 제목 없음")}
<h3>블랙리스트</h3>
<p>스팸하우스: {_check_line(result.spamhaus.check)}<br>
세이프 브라우징: {_check_line(result.safebrowsing.check)}</p>

<h2>8. 확인하지 못한 것</h2>
<h3>미확인(검사했지만 답을 못 받음)</h3>{_list(result.unchecked, "없음")}
<h3>미실시(키가 없거나 꺼 둠)</h3>{_list(result.not_run, "없음")}

<h2>9. 사람이 직접 다시 확인할 곳</h2>
<ul>{links}</ul>
"""


def _page(title: str, bar: str, body: str, action: str = "") -> str:
    """DW 블록 app-shell-mobile 그대로 — 붙어 있는 제목 줄 + 가운데만 구르는 본문.
    보고서에는 화면을 옮길 곳이 없으므로 탭 줄만 없다.

    모양 규칙은 같은 폴더의 style.css 를 가리킨다. 예전에는 페이지마다 통째로
    끼워 넣었는데, 도메인 1,000개면 페이지 1,001장 × 50KB = 약 78MB 가 되어
    메일에 붙지 않았다. 보고서는 언제나 폴더 한 벌로 나가므로 한 장만 두면 된다.
    """
    slot = f'<div class="dw-block-shell-action">{action}</div>' if action else ""
    return (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        f'<title>{_t(title)}</title><link rel="stylesheet" href="style.css"></head>'
        '<body><div class="dw-block-shell"><header class="dw-block-shell-bar">'
        f'<h1 class="dw-block-shell-title">{_t(bar)}</h1>{slot}</header>'
        f'<main class="dw-block-shell-body"><div class="dw-prose">{body}</div>'
        "</main></div></body></html>"
    )


def render_detail_page(result: DomainResult, capture_base: str = "../captures") -> str:
    body = _alert(escape(DISCLAIMER)) + detail_fragment(result, capture_base)
    back = (
        '<a class="dw-button" data-variant="ghost" data-size="sm" href="index.html">전체 목록으로</a>'
    )
    return _page(f"{result.domain} 상세 — 낙장도메인 품질 체커", result.domain, body, action=back)


# 지금 살 수 있는 것 → 곧 살 수 있는 것 → 경매 → 모름 → 남이 쓰는 중 순서.
_BUY_RANK = {"free": 0, "soon": 1, "auction": 2, "unknown": 3, "taken": 4}


def _buy_rank(result: DomainResult) -> int:
    return _BUY_RANK.get(result.availability, 3)


def render_index(results: list[DomainResult]) -> str:
    groups = []
    for verdict in VERDICT_ORDER:
        rows = [r for r in results if r.verdict is verdict]
        if not rows:
            continue
        # 점수만으로 줄을 세우면 "매입 후보" 맨 위에 못 사는 도메인이 온다.
        # 이 도구는 살 것을 고르라고 있는 물건이라, 살 수 있는 것을 먼저 올린다.
        rows.sort(key=lambda r: (_buy_rank(r), r.score is None, -(r.score or 0)))
        body = _item_group(
            [
                _item(
                    _t(r.domain),
                    [
                        _t(r.one_liner),
                        (
                            "추천 주제: "
                            + _t(", ".join(t.get("topic", "") for t in r.recommended_topics[:3]))
                            if r.recommended_topics
                            else ""
                        ),
                    ],
                    href=f"{_t(safe_name(r.domain))}.html",
                    title_class="app-domain",
                    footer=f'<span class="app-badges">{_stamp(r)}{_avail(r)}</span>'
                    f'<span class="app-score">{_t(_score_text(r))}</span>',
                )
                for r in rows
            ]
        )
        groups.append(f"<h2>{_t(VERDICT_LABEL[verdict])} — {len(rows)}개</h2>{body}")

    # 합집합만 적으면 "AI 분석 못 함"이라 써 놓고 옆 표에는 AI 한줄평이 보여 모순으로 읽힌다.
    # 몇 개 도메인에서 못 했는지 함께 적어 준다.
    counts = Counter(label for r in results for label in set(r.unchecked + r.not_run))
    missing = [
        label if hit == len(results) else f"{label} — 도메인 {len(results)}개 중 {hit}개에서"
        for label, hit in sorted(counts.items())
    ]
    # 언제 검사한 결과인지 없으면 묵은 답을 오늘 것으로 읽는다.
    checked_at = max((r.finished_at for r in results if r.finished_at), default="")
    body = (
        f'<p class="muted">{_t(_when(checked_at))}에 검사함 · 도메인 {len(results)}개 · '
        f"{escape(SCORE_GUIDE)}</p>"
        + _alert(escape(DISCLAIMER))
        + "".join(groups)
        + "<h2>이 검사의 한계</h2>"
        + _list(LIMITS)
        + "<h2>이번 실행에서 확인하지 못한 검사</h2>"
        + _list(missing, "없음")
        + '<p class="muted">원자료: <a href="results.json">results.json</a> · '
        f"검사 항목 이름: {_t(', '.join(CHECK_LABEL.values()))}</p>"
    )
    return _page("낙장도메인 품질 체커 결과", "검사 결과", body)


def _copy_fonts(out_dir: Path) -> None:
    """손글씨 글꼴을 보고서 폴더에도 한 벌 둔다.

    보고서는 폴더째 메일로 나간다. `style.css` 가 글꼴을 `fonts/…` 로 부르므로,
    글꼴이 옆에 없으면 받는 사람 화면에서는 제목이 손글씨로 나오지 않는다.
    글꼴이 없어도 보고서 자체는 나와야 하니 실패는 조용히 넘긴다.
    """
    src_dir = STATIC_DIR / "fonts"
    if not src_dir.is_dir():
        return
    dest_dir = out_dir / "fonts"
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in ("MemomentKkukkukk.woff2",):
        one = src_dir / name
        if one.is_file():
            with contextlib.suppress(OSError):
                shutil.copyfile(one, dest_dir / name)


def write_report(results: list[DomainResult], base: Path | str) -> Path:
    """Write data/report/index.html plus one page per domain; returns the index."""
    out_dir = Path(base) / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    # 모양 규칙 한 장. 페이지마다 통째로 끼워 넣으면 도메인 1,000개에 약 78MB 가 된다.
    (out_dir / "style.css").write_text(current_css(), encoding="utf-8")
    _copy_fonts(out_dir)
    keep = {"index.html"}
    for result in results:
        name = f"{safe_name(result.domain)}.html"
        keep.add(name)
        (out_dir / name).write_text(render_detail_page(result), encoding="utf-8")
    # 지난 실행에서 남은 도메인 페이지는 지운다 — 목록에 없는데 파일만 남으면
    # 옛 결과를 오늘 것으로 착각한다.
    for stale in out_dir.glob("*.html"):
        if stale.name not in keep:
            with contextlib.suppress(OSError):
                stale.unlink()
    index = out_dir / "index.html"
    index.write_text(render_index(results), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return index
