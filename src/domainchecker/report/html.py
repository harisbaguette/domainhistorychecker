"""HTML report writer.

`detail_fragment` is the single source of truth for the per-domain view: the
static report pages and the browser UI both render it, so the two can never
drift apart.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from urllib.parse import quote

from ..models import AVAILABILITY_LABEL, CHECK_LABEL, VERDICT_LABEL, DomainResult, Verdict

DISCLAIMER = (
    "이 검사 결과는 “무위험 보증”이 아닙니다. 클로킹(검색엔진에만 다른 화면을 보여주는 수법)이나 "
    "해킹으로 몰래 심어진 스팸은 저장된 과거 화면만으로는 구조적으로 놓칠 수 있습니다. "
    "돈을 걸기 전에 상세 근거를 직접 눈으로 확인하세요."
)

LIMITS = [
    "저장된 이력이 없는 것과 열람이 차단된 것은 다릅니다 — 뒤쪽은 이력을 숨겼을 가능성이 있습니다.",
    "백링크 상세는 무료로 받을 수 있는 자료가 없어 근거가 얇습니다(권위 점수와 색인 잔존으로 갈음).",
    "상표 검사는 도메인 이름 문자열과 AI 소견 수준의 보조 검사입니다.",
    "색인 수는 구글 site: 검색 기준의 추정치입니다.",
    "구매 가격·경매 시세와 주인이 몇 번 바뀌었는지는 다루지 않습니다.",
]

SCORE_GUIDE = "75점부터 매입 후보(초록) · 50점 밑은 제외(빨강) · 나머지는 검토 필요(노랑)"

# 어려운 낱말은 제목·라벨에서 곧바로 풀어 준다(따로 용어집을 찾아가지 않게).
AUTHORITY_LABEL = "권위 점수(다른 사이트들이 링크로 밀어주는 힘, 0~10)"
INDEX_LABEL = "구글에 남아 있는 페이지(색인)"
REDIRECT_LABEL = "다른 곳으로 넘겨보낸 비율(리다이렉트)"
PARKING_LABEL = "‘도메인 팝니다’ 임시 화면이던 비중(파킹)"

SPAM_VERDICT_LABEL = {
    "spam": "스팸으로 봄(spam)",
    "clean": "깨끗함(clean)",
    "unclear": "판단 유보(unclear)",
    "unknown": "확인 안 됨(unknown)",
}

VERDICT_ORDER = [Verdict.BUY, Verdict.REVIEW, Verdict.NO_HISTORY, Verdict.REJECT]
VERDICT_CLASS = {
    Verdict.BUY: "buy",
    Verdict.REVIEW: "review",
    Verdict.NO_HISTORY: "review",
    Verdict.REJECT: "reject",
}

CSS = """
:root{
/* 표면 — 종이 수첩 */
--dw-bg:#F5F2EE; --dw-surface:#FFFFFF; --dw-surface-sunken:#F0ECE6; --dw-surface-raised:#FAF8F5;
/* 경계 */
--dw-border:rgba(36,30,25,0.07); --dw-border-strong:#E4DFD9;
/* 글자 */
--dw-text:#241E19; --dw-text-muted-aa:#6A645D; --dw-text-muted:#88837C; --dw-text-faint:#B6B1AA;
/* 액센트 — 만년필 잉크 파랑 */
--dw-accent:#3D79C0; --dw-accent-strong:#155DA1; --dw-accent-pressed:#004981;
--dw-accent-bg:#ECF5FF; --dw-on-accent:#FFFFFF;
/* 상태 — 원색은 면·테두리, 글자는 -ink */
--dw-success:#4BB86A; --dw-success-bg:#E9FAED; --dw-success-ink:#1A6D3B;
--dw-warning:#EAA13B; --dw-warning-bg:#FFF5E2; --dw-warning-ink:#93550F;
--dw-error:#DF4B46; --dw-error-bg:#FFF1EF; --dw-error-ink:#A42D2B;
/* 타임라인 막대 */
--dw-chart-1:#2D71B7;
/* 그림자 — glass가 이 프로젝트 시그니처 */
--dw-shadow-1:0 1px 2px rgba(36,30,25,0.06);
--dw-shadow-2:0 4px 12px rgba(36,30,25,0.08);
--dw-shadow-glass:0 8px 32px rgba(0,0,0,0.04);
/* 모서리·모션·포커스 */
--dw-radius-control:12px; --dw-radius-card:16px; --dw-radius-pill:999px;
--dw-ease:cubic-bezier(0.23,1,0.32,1);
--dw-focus:0 0 0 3px rgba(61,121,192,0.34);
--dw-font:Pretendard,"Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;
/* 판정 색 별칭 — 판정 도장이 한 곳에서만 색을 받는다 */
--buy:var(--dw-success-ink); --buy-bg:var(--dw-success-bg); --buy-line:var(--dw-success);
--review:var(--dw-warning-ink); --review-bg:var(--dw-warning-bg); --review-line:var(--dw-warning);
--reject:var(--dw-error-ink); --reject-bg:var(--dw-error-bg); --reject-line:var(--dw-error);
}
*{box-sizing:border-box;}
html{-webkit-text-size-adjust:100%;}
body{margin:0;padding:0;color:var(--dw-text);background:var(--dw-bg);font-family:var(--dw-font);
font-size:16px;line-height:1.5;word-break:keep-all;overflow-wrap:break-word;}
.wrap{max-width:1100px;margin:0 auto;padding:24px 16px 48px;}
.wrap.reading{max-width:860px;}
h1{font-size:25px;font-weight:900;line-height:1.25;margin:0 0 8px;letter-spacing:-0.01em;}
h2{font-size:20px;font-weight:700;margin:32px 0 12px;}
h3{font-size:16px;font-weight:700;margin:20px 0 8px;color:var(--dw-text-muted-aa);}
p{margin:8px 0;}
a{color:var(--dw-accent-strong);text-underline-offset:3px;}
a:hover{color:var(--dw-accent-pressed);}
:focus-visible{outline:none;box-shadow:var(--dw-focus);}
.muted{color:var(--dw-text-muted-aa);font-size:14px;}
.num{font-variant-numeric:tabular-nums;}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:14px;
background:var(--dw-surface-sunken);padding:2px 6px;border-radius:4px;}
ul{margin:8px 0;padding-left:20px;} li{margin:4px 0;}
.notice{background:var(--dw-warning-bg);border:1px solid var(--dw-warning);color:var(--dw-warning-ink);
padding:16px;border-radius:var(--dw-radius-card);margin:16px 0;}
.notice a{color:var(--dw-warning-ink);}
/* 표 — 흰 카드 위에 얹은 종이 */
.table-card{background:var(--dw-surface);border:1px solid var(--dw-border);
border-radius:var(--dw-radius-card);box-shadow:var(--dw-shadow-glass);overflow-x:auto;margin:16px 0;}
table{border-collapse:collapse;width:100%;font-size:16px;}
th,td{padding:12px 16px;text-align:left;vertical-align:middle;border-bottom:1px solid var(--dw-border);}
th{background:var(--dw-surface-raised);color:var(--dw-text-muted-aa);font-size:14px;font-weight:700;white-space:nowrap;}
tr:last-child td{border-bottom:none;}
td.num,th.num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;}
/* 판정 도장 — 이 화면의 시그니처 순간 */
.tag{display:inline-block;padding:4px 12px;border-radius:var(--dw-radius-pill);
font-size:14px;font-weight:700;line-height:1.4;white-space:nowrap;border:1px solid transparent;}
.tag.buy{background:var(--buy-bg);color:var(--buy);border-color:var(--buy-line);}
.tag.review{background:var(--review-bg);color:var(--review);border-color:var(--review-line);}
.tag.reject{background:var(--reject-bg);color:var(--reject);border-color:var(--reject-line);}
/* 지금 살 수 있나 — 도장보다 한 단계 조용하게 */
.avail{display:inline-block;padding:4px 12px;border-radius:var(--dw-radius-pill);
font-size:14px;font-weight:500;line-height:1.4;}
.avail.free{background:var(--dw-success-bg);color:var(--dw-success-ink);}
.avail.soon,.avail.auction{background:var(--dw-warning-bg);color:var(--dw-warning-ink);}
.avail.taken{background:var(--dw-surface-sunken);color:var(--dw-text-muted-aa);}
.avail.unknown{color:var(--dw-text-muted-aa);padding-left:0;}
.badges{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 0 8px;}
.years{display:flex;gap:4px;align-items:flex-end;height:72px;margin:12px 0 32px;}
.years div{flex:1 1 0;max-width:56px;background:var(--dw-chart-1);border-radius:4px 4px 0 0;
min-height:3px;position:relative;}
.years div span{position:absolute;bottom:-22px;left:0;right:0;text-align:center;
font-size:14px;color:var(--dw-text-muted-aa);font-variant-numeric:tabular-nums;}
.quote{border-left:3px solid var(--dw-border-strong);background:var(--dw-surface-raised);
padding:8px 16px;margin:8px 0;color:var(--dw-text-muted-aa);
border-radius:0 var(--dw-radius-control) var(--dw-radius-control) 0;}
.shots{display:flex;flex-wrap:wrap;gap:16px;} .shots figure{margin:0;max-width:100%;}
.shots img{max-width:520px;width:100%;display:block;border:1px solid var(--dw-border-strong);
border-radius:var(--dw-radius-control);}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:16px 0;}
.card{background:var(--dw-surface);border:1px solid var(--dw-border);border-radius:var(--dw-radius-card);
box-shadow:var(--dw-shadow-glass);padding:16px;color:var(--dw-text-muted-aa);font-size:14px;}
.card b{display:block;margin-top:4px;color:var(--dw-text);font-size:16px;font-weight:700;}
@media (max-width:640px){
.wrap{padding:16px 12px 40px;}
.table-card{background:none;border:none;box-shadow:none;overflow:visible;}
table,tbody,tr,td{display:block;width:100%;}
thead,th{display:none;}
tr:has(th){display:none;}
tr{background:var(--dw-surface);border:1px solid var(--dw-border);border-radius:var(--dw-radius-card);
box-shadow:var(--dw-shadow-glass);padding:12px 16px;margin-bottom:12px;}
td{border:none;padding:4px 0;}
tr:last-child td{border-bottom:none;}
td.num{text-align:left;}
td::before{content:attr(data-label);display:block;color:var(--dw-text-muted-aa);font-size:14px;}
td:empty{display:none;}
}
"""


def _t(value) -> str:
    return escape(str(value if value is not None else ""))


def _score_text(result: DomainResult) -> str:
    if result.score is None:
        return "점수 없음"
    return f"{result.score:.0f}점" + ("(부분 점수·참고치)" if result.partial_score else "")


def _stamp(result: DomainResult) -> str:
    """판정 도장 — 표·상세 어디서든 같은 모양으로 찍힌다."""
    cls = VERDICT_CLASS.get(result.verdict, "review")
    label = result.verdict_label or VERDICT_LABEL[result.verdict]
    return f'<span class="tag {cls}">{_t(label)}</span>'


def _avail(result: DomainResult) -> str:
    """지금 살 수 있나 배지 — 이 도구의 존재 이유라 판정 바로 옆에 둔다."""
    key = result.availability if result.availability in AVAILABILITY_LABEL else "unknown"
    label = result.availability_label or AVAILABILITY_LABEL[key]
    return f'<span class="avail {key}">{_t(label)}</span>'


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
    bars = "".join(
        f'<div style="height:{max(4, int(counts[y] / top * 56))}px" title="{_t(y)}년 {counts[y]}건">'
        f"<span>{_t(y[2:])}</span></div>"
        for y in sorted(counts)
    )
    gaps = (
        f'<p class="muted">기록이 비어 있는 해: {", ".join(str(g) for g in result.wayback.gap_years)}</p>'
        if result.wayback.gap_years
        else ""
    )
    return f'<div class="years">{bars}</div>{gaps}'


def _captures(result: DomainResult, capture_base: str) -> str:
    if not result.captures.items:
        return f'<p class="muted">{_t(result.captures.check.note or "저장된 캡쳐가 없습니다.")}</p>'
    figures = "".join(
        f"<figure><img src=\"{_t(capture_base.rstrip('/'))}/{_t(Path(shot.file).name)}\" "
        f'alt="{_t(shot.label)} 캡쳐" loading="lazy">'
        f"<figcaption class=\"muted\">{_t(shot.label)} · {_t(shot.timestamp[:8])} "
        f'(<a href="{_t(shot.url)}" target="_blank" rel="noopener">원본 보기</a>)</figcaption></figure>'
        for shot in result.captures.items
    )
    return f'<div class="shots">{figures}</div>'


def detail_fragment(result: DomainResult, capture_base: str = "../captures") -> str:
    """The full evidence view for one domain (no <html> wrapper)."""
    registration = result.registration
    age = result.wayback.age_years
    scoring_rows = "".join(
        f"<tr><td data-label=\"항목\">{_t(item.label)}</td>"
        f'<td class="num" data-label="점수">'
        + (f"{item.earned:.1f} / {item.max_points}" if item.earned is not None else "미확인(분모 제외)")
        + f'</td><td data-label="설명">{_t(item.note)}</td></tr>'
        for item in result.scoring.items
    )
    topics = "".join(
        f"<li><b>{_t(t.get('topic'))}</b> — {_t(t.get('reason'))}</li>"
        for t in result.recommended_topics
    )
    quotes = "".join(f'<blockquote class="quote">{_t(q)}</blockquote>' for q in result.ai.spam.quotes)
    links = "".join(
        f'<li><a href="{_t(url)}" target="_blank" rel="noopener">{_t(label)}</a></li>'
        for label, url in recheck_links(result.domain)
    )
    fallback_note = (
        f'<p class="muted">기본 모델이 실패해 대체 모델 {_t(result.ai.model)}로 분석했습니다 — 단가가 다를 수 있습니다.</p>'
        if result.ai.fallback_used
        else ""
    )
    # 못 잰 값을 0으로 보여 주면 "권위가 0인 나쁜 도메인"으로 오해한다.
    authority_value = (
        f"{result.authority.page_rank:.2f} / 10"
        if result.authority.check.ok
        else "못 쟀음 — " + (result.authority.check.note or "확인하지 못했습니다.")
    )
    index_line = (
        f"색인 {result.index.indexed_count}건"
        + (" · " + _t(result.index.check.note) if result.index.check.note else "")
        if result.index.check.ok
        else "못 쟀음 — " + _t(result.index.check.note or "확인하지 못했습니다.")
    )
    spam_verdict = SPAM_VERDICT_LABEL.get(result.ai.spam.verdict, result.ai.spam.verdict)
    return f"""
<h1>{_t(result.domain)}</h1>
<div class="badges">{_stamp(result)}{_avail(result)}</div>
<p class="muted">{_t(_score_text(result))} · 취득 상태: {_t(result.acquisition)} · 검사 완료 {_t(result.finished_at[:16])}</p>
<p>{_t(result.one_liner or "한줄평 없음")}</p>

<h2>1. 왜 이렇게 판정했나</h2>
<h3>치명 사유</h3>{_list(result.fatal_reasons, "없음")}
<h3>주의 사유</h3>{_list(result.warn_reasons, "없음")}
<h3>점수 내역</h3>
<div class="table-card"><table><tr><th>항목</th><th class="num">점수</th><th>설명</th></tr>{scoring_rows}</table></div>
<p class="muted">미확인 항목은 0점도 만점도 아니라 분모에서 빼고 계산합니다(그래서 부분 점수는 참고치입니다).</p>

<h2>2. 나이와 등록 정보</h2>
<div class="grid">
  <div class="card">지금 살 수 있나<br><b>{_t(result.availability_label or AVAILABILITY_LABEL["unknown"])}</b></div>
  <div class="card">등록일<br><b>{_t(registration.created or "알 수 없음")}</b></div>
  <div class="card">만료일<br><b>{_t(registration.expires or "알 수 없음")}</b></div>
  <div class="card">저장 이력 기간<br><b>약 {age:.0f}년</b> ({_t(result.wayback.first_seen[:6])}~{_t(result.wayback.last_seen[:6])})</div>
  <div class="card">재등록(드랍) 이력<br><b>{"있음" if registration.redropped else "확인 안 됨"}</b></div>
  <div class="card">등록 자료 출처<br><b>{_t(registration.source or "없음")}</b></div>
  <div class="card">{AUTHORITY_LABEL}<br><b>{_t(authority_value)}</b></div>
</div>

<h2>3. 저장 이력 타임라인</h2>
{_timeline(result)}
<p class="muted">총 {result.wayback.total_captures}건 · {REDIRECT_LABEL} {result.wayback.redirect_ratio:.0%} ·
{PARKING_LABEL} {result.rules.parking_ratio:.0%}</p>

<h2>4. 주제 변천과 전환 방향</h2>
<p>{_t(result.ai.topic_history or "AI 분석 결과가 없습니다.")}</p>
<p><b>전환 방향:</b> {_t(result.ai.transition or "확인 안 됨")}</p>
<p><b>콘텐츠 품질:</b> {_t(result.ai.content_quality or "확인 안 됨")}</p>
<p><b>상표 소견:</b> {_t(result.ai.trademark or "확인 안 됨")}</p>
{fallback_note}

<h2>5. 캡쳐</h2>
{_captures(result, capture_base)}

<h2>6. 신호와 근거</h2>
<h3>운영방식 규칙이 찾은 흔적</h3>{_list(result.rules.evidence, "특별한 흔적 없음")}
<h3>AI 스팸성 판정</h3>
<p>{_t(spam_verdict)} · 확신도 {result.ai.spam.confidence:.2f}</p>
{quotes or '<p class="muted">인용된 근거 없음</p>'}
<h3>주의 표지(업종 낱말 — 그 자체로 스팸이라는 뜻은 아님)</h3>
{_list(result.rules.sensitive_terms, "없음")}
<h3>{INDEX_LABEL}</h3>
<p>{index_line}</p>
{_list(result.index.titles[:10], "색인된 제목 없음")}
<h3>블랙리스트</h3>
<p>스팸하우스: {_t(result.spamhaus.check.note or "-")}<br>
세이프 브라우징: {_t(result.safebrowsing.check.note or "-")}<br>
바이러스토탈: {_t(result.virustotal.check.note or "-")}</p>

<h2>7. 확인하지 못한 것</h2>
<h3>미확인(검사했지만 답을 못 받음)</h3>{_list(result.unchecked, "없음")}
<h3>미실시(키가 없거나 꺼 둠)</h3>{_list(result.not_run, "없음")}

<h2>8. 이어가면 좋을 주제</h2>
{f"<ul>{topics}</ul>" if topics else '<p class="muted">추천 주제 없음</p>'}

<h2>9. 사람이 직접 다시 확인할 곳</h2>
<ul>{links}</ul>
"""


def _page(title: str, body: str, wrap_class: str = "wrap") -> str:
    return (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{_t(title)}</title><style>{CSS}</style></head>"
        f'<body><div class="{wrap_class}">{body}</div></body></html>'
    )


def render_detail_page(result: DomainResult, capture_base: str = "../captures") -> str:
    body = (
        '<p><a href="index.html">← 전체 목록으로</a></p>'
        + f'<div class="notice">{escape(DISCLAIMER)}</div>'
        + detail_fragment(result, capture_base)
    )
    # 글 중심 화면이라 읽기 폭(860px)으로 좁힌다.
    return _page(f"{result.domain} 상세 — 낙장도메인 품질 체커", body, "wrap reading")


def _safe_name(domain: str) -> str:
    return re.sub(r"[^a-z0-9.\-]", "_", domain.lower())[:120] or "unknown"


def render_index(results: list[DomainResult]) -> str:
    groups = []
    for verdict in VERDICT_ORDER:
        rows = [r for r in results if r.verdict is verdict]
        if not rows:
            continue
        rows.sort(key=lambda r: (r.score is None, -(r.score or 0)))
        body = "".join(
            f'<tr><td data-label="도메인"><a href="{_t(_safe_name(r.domain))}.html">{_t(r.domain)}</a></td>'
            f'<td data-label="판정">{_stamp(r)}</td>'
            f'<td data-label="지금 살 수 있나">{_avail(r)}</td>'
            f'<td class="num" data-label="점수">{_t(_score_text(r))}</td>'
            f'<td data-label="한줄평">{_t(r.one_liner)}</td>'
            f'<td data-label="추천 주제">{_t(", ".join(t.get("topic", "") for t in r.recommended_topics[:3]))}</td></tr>'
            for r in rows
        )
        groups.append(
            f"<h2>{_t(VERDICT_LABEL[verdict])} — {len(rows)}개</h2>"
            '<div class="table-card"><table><tr><th>도메인</th><th>판정</th><th>지금 살 수 있나</th>'
            f'<th class="num">점수</th><th>한줄평</th><th>추천 주제</th></tr>{body}</table></div>'
        )

    missing = sorted({label for r in results for label in r.unchecked + r.not_run})
    body = (
        "<h1>낙장도메인 품질 체커 결과</h1>"
        f'<p class="muted">도메인 {len(results)}개 · {escape(SCORE_GUIDE)}</p>'
        f'<div class="notice">{escape(DISCLAIMER)}</div>'
        + "".join(groups)
        + "<h2>이 검사의 한계</h2>"
        + _list(LIMITS)
        + "<h2>이번 실행에서 확인하지 못한 검사</h2>"
        + _list(missing, "없음")
        + '<p class="muted">원자료: <a href="../results.json">results.json</a> · '
        f"검사 항목 이름: {_t(', '.join(CHECK_LABEL.values()))}</p>"
    )
    return _page("낙장도메인 품질 체커 결과", body)


def write_report(results: list[DomainResult], base: Path | str) -> Path:
    """Write data/report/index.html plus one page per domain; returns the index."""
    out_dir = Path(base) / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    for result in results:
        (out_dir / f"{_safe_name(result.domain)}.html").write_text(
            render_detail_page(result), encoding="utf-8"
        )
    index = out_dir / "index.html"
    index.write_text(render_index(results), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps([r.model_dump(mode="json") for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return index
