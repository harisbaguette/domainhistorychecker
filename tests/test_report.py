import json

from domainchecker.analyze.scoring import judge
from domainchecker.models import Capture, Captures, CheckState, CheckStatus, Verdict
from domainchecker.report.html import (
    DISCLAIMER,
    detail_fragment,
    render_index,
    write_report,
)


def test_detail_fragment_holds_every_required_section(sample_result):
    judge(sample_result)
    html = detail_fragment(sample_result)

    for heading in ("이렇게 봤다", "과거에 이렇게 생겼었다", "얼마나 오래 굴러갔나",
                    "무엇을 하던 도메인인가", "더 보기"):
        assert heading in html
    # 가비아 확인은 날짜를 주지 않는다 — 등록일 칸은 "알 수 없음"으로 나온다.
    assert "알 수 없음" in html  # 등록일
    assert "삭제 대기(곧 등록 가능)" in html  # 취득 상태
    assert "빵집 블로그로 시작해" in html  # 주제 역사(줄글)
    assert "홈베이킹" in html  # 추천 주제


def test_detail_never_folds_the_evidence_away(sample_result):
    """증거를 접어 숨기고 사람에게 재확인을 떠넘기면 프로그램이 존재할 이유가 없다.

    접히는 자리(<details>)는 딱 하나이고, 그 앞에 판정 도장·근거 뱃지·과거 화면이
    전부 서 있어야 한다(2026-08-23 운영자 지시).
    """
    judge(sample_result)
    html = detail_fragment(sample_result)

    assert html.count("<details") == 1
    open_part, folded = html.split("<details", 1)
    for must_be_open in ("app-call", "app-tags", "과거에 이렇게 생겼었다"):
        assert must_be_open in open_part
    # 사람에게 다시 확인하라고 떠넘기던 링크 목록은 화면에서 사라졌다.
    assert "다시 확인할 곳" not in html
    assert "ahrefs.com" not in html and "whoisology.com" not in html
    assert "검사 항목별 원자료" in folded


def test_detail_speaks_plainly_about_time_and_reasons(sample_result):
    """도구를 쓰는 사람은 비개발자다 — 기계 표기(T)와 한자말은 화면에 남기지 않는다."""
    sample_result.finished_at = "2026-08-05T11:42:07.123456"
    judge(sample_result)
    sample_result.fatal_reasons = ["규칙 검사가 스팸 흔적을 찾았습니다."]
    sample_result.warn_reasons = ["상표에 걸릴 수 있습니다."]
    html = detail_fragment(sample_result)

    assert "2026년 8월 5일 11:42" in html
    assert "2026-08-05T11:42" not in html
    assert "사면 안 됨" in html and "조심" in html
    assert "치명 사유" not in html


def test_reasons_put_the_domain_first_and_the_tools_excuses_last(sample_result):
    """"검사를 못 했다"는 이 도구의 사정이라, 그 도메인 얘기 뒤로 밀려야 한다."""
    judge(sample_result)
    sample_result.fatal_reasons = []
    sample_result.warn_reasons = [
        "필수 검사 미확인 — 스팸하우스 블랙리스트: 조회가 막혔습니다.",
        "규칙 검사가 스팸 흔적을 찾았습니다.",
    ]
    html = detail_fragment(sample_result)

    assert html.index("규칙 검사가 스팸 흔적") < html.index("스팸하우스 블랙리스트: 조회가 막혔습니다")


def test_evidence_becomes_badges_not_paragraphs(sample_result):
    """판정 근거는 긴 문단이 아니라 짧은 뱃지로 선다 — 안 돌린 검사는 점선으로."""
    judge(sample_result)
    sample_result.rules.doorway = True
    sample_result.rules.hidden_text = True
    sample_result.not_run = ["AI 분석"]
    html = detail_fragment(sample_result)

    assert "검색용 유입 페이지" in html and "숨긴 글자" in html
    assert "app-tag-miss" in html and "안 돌림 · AI 분석" in html


def test_the_verdict_stamp_stands_at_the_very_top(sample_result):
    """판정 도장은 화면 맨 위다 — 근거를 읽기 전에 답이 먼저 와야 한다."""
    judge(sample_result)
    html = detail_fragment(sample_result).strip()

    assert html.startswith('<div class="app-call"')
    assert sample_result.verdict_label in html
    # 앱 화면 시트는 제 도장을 이미 찍어 두었으므로 여기서는 뺀다(두 번 서지 않게).
    assert '<div class="app-call"' not in detail_fragment(sample_result, stamp=False)


def test_detail_escapes_injected_html(sample_result):
    sample_result.ai.one_liner = '<script>alert("x")</script>'
    judge(sample_result)
    html = detail_fragment(sample_result)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_captures_are_linked_relative_to_the_report(sample_result):
    sample_result.captures = Captures(
        check=CheckState(status=CheckStatus.OK),
        items=[
            Capture(
                label="말기",
                timestamp="20240601000000",
                url="https://web.archive.org/web/20240601000000/http://example.com/",
                file="captures/example.com_20240601000000.png",
            )
        ],
    )
    judge(sample_result)
    html = detail_fragment(sample_result, capture_base="../captures")
    assert '<img src="../captures/example.com_20240601000000.png"' in html
    assert "말기" in html


def test_capture_failures_are_told_not_swallowed(sample_result):
    """몇 장 못 찍었다는 말이 없으면, 남은 사진 몇 장이 '과거 전부'로 읽힌다."""
    sample_result.captures = Captures(
        check=CheckState(status=CheckStatus.OK, note="2장 저장, 실패 2장: 말기(202305): Error"),
        items=[
            Capture(
                label="말기",
                timestamp="20240601000000",
                url="https://web.archive.org/web/20240601000000/http://example.com/",
                file="captures/example.com_20240601000000.png",
            )
        ],
    )
    judge(sample_result)
    html = detail_fragment(sample_result)

    assert "실패 2장" in html


def test_missing_captures_say_which_kind_of_missing(sample_result):
    """찍을 것 자체가 없던 것과, 있는데 못 찍은 것은 다른 이야기다 —
    낱말이 아니라 웨이백 이력이 있느냐로 가른다."""
    sample_result.captures = Captures(check=CheckState(status=CheckStatus.UNCHECKED), items=[])
    sample_result.wayback.total_captures = 2000
    sample_result.wayback.first_seen = "20090101000000"
    sample_result.wayback.last_seen = "20240101000000"
    judge(sample_result)
    assert "과거 화면 사진을 못 남겼습니다" in detail_fragment(sample_result)

    sample_result.wayback.total_captures = 0
    sample_result.wayback.first_seen = ""
    sample_result.wayback.last_seen = ""
    judge(sample_result)
    assert "찍을 것이 없었습니다" in detail_fragment(sample_result)


def test_index_leads_with_the_count_not_with_an_explanation(sample_result):
    """첫 화면이 글자 나열이면 아무도 안 읽는다 — 개수 판이 설명보다 먼저 선다."""
    judge(sample_result)
    html = render_index([sample_result])

    assert html.index('class="app-tally"') < html.index("에 검사함")
    # 점수 읽는 법은 첫 화면에서 걷어내 '더 보기' 안으로 내렸다.
    assert html.index("점수 읽는 법") > html.index("<details")


def test_index_groups_by_verdict_and_warns(sample_result):
    judge(sample_result)
    html = render_index([sample_result])

    assert DISCLAIMER[:20] in html
    assert "무위험 보증" in html
    assert sample_result.verdict_label in html
    assert "이 검사의 한계" in html


def test_index_marks_partial_misses_with_a_count(sample_result):
    """한 도메인만 실패한 검사를 전부 실패처럼 적으면 옆 표의 결과와 모순으로 읽힌다."""
    judge(sample_result)
    other = sample_result.model_copy(deep=True)
    other.domain = "second.com"
    other.unchecked = []
    other.not_run = []
    sample_result.unchecked = ["AI 분석"]
    sample_result.not_run = ["세이프 브라우징"]

    html = render_index([sample_result, other])

    assert "AI 분석 — 도메인 2개 중 1개에서" in html
    assert "세이프 브라우징 — 도메인 2개 중 1개에서" in html


def test_index_lists_an_all_domain_miss_without_a_count(sample_result):
    judge(sample_result)
    sample_result.unchecked = ["AI 분석"]
    sample_result.not_run = []

    html = render_index([sample_result])

    assert "AI 분석" in html
    assert "AI 분석 — 도메인" not in html


def test_write_report_creates_index_and_detail_pages(sample_result, tmp_path):
    judge(sample_result)
    index = write_report([sample_result], tmp_path)

    assert index.exists() and index.name == "index.html"
    detail = tmp_path / "report" / "example.com.html"
    assert detail.exists()
    assert "example.com" in index.read_text(encoding="utf-8")
    assert "전체 목록으로" in detail.read_text(encoding="utf-8")
    saved = json.loads((tmp_path / "report" / "results.json").read_text(encoding="utf-8"))
    assert saved[0]["domain"] == "example.com"


def test_unmeasured_numbers_say_so_instead_of_showing_zero(sample_result):
    """못 잰 값을 0으로 보여 주면 '기록이 0인 나쁜 도메인'으로 오해한다(심사 C4)."""
    from domainchecker.models import IndexInfo

    sample_result.index = IndexInfo(
        check=CheckState(status=CheckStatus.UNCHECKED, note="색인 검사를 못 했습니다.")
    )
    judge(sample_result)
    html = detail_fragment(sample_result)

    assert "색인 0건" not in html
    assert "못 쟀음" in html
    assert "색인 검사를 못 했습니다." in html


def test_plain_words_are_spelled_out_for_the_reader(sample_result):
    """어려운 낱말은 제목·라벨에서 바로 풀어 준다(심사 C10)."""
    judge(sample_result)
    html = detail_fragment(sample_result)

    assert "웹에 남아 있는 페이지(색인)" in html
    assert "다른 곳으로 넘겨보낸 비율(리다이렉트)" in html
    assert "임시 화면이던 비중(파킹)" in html

    sample_result.ai.spam.verdict = "unclear"
    assert "판단 유보(unclear)" in detail_fragment(sample_result)


def test_index_page_says_what_the_score_actually_is(sample_result):
    """점수 문턱으로 도장을 찍던 감점표는 폐지됐다 — 안내문이 옛 규칙을 말하면 안 된다."""
    judge(sample_result)
    html = render_index([sample_result])

    assert "매입 매력도" in html
    assert "75점부터" not in html and "50점 밑" not in html


def test_report_survives_a_domain_with_no_evidence():
    from domainchecker.models import DomainResult

    empty = judge(DomainResult(domain="blank.com"))
    html = detail_fragment(empty)

    assert empty.verdict is Verdict.REVIEW
    assert "점수 없음" in html
    assert "저장된 이력이 없습니다" in html
