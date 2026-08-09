import json

from domainchecker.analyze.scoring import judge
from domainchecker.models import Capture, Captures, CheckState, CheckStatus, Verdict
from domainchecker.report.html import (
    DISCLAIMER,
    detail_fragment,
    recheck_links,
    render_index,
    write_report,
)


def test_detail_fragment_holds_every_required_section(sample_result):
    judge(sample_result)
    html = detail_fragment(sample_result)

    for heading in ("왜 이렇게 판정했나", "나이와 등록 정보", "타임라인",
                    "과거에 무엇을 하던 도메인인가", "캡쳐",
                    "신호와 근거", "확인하지 못한 것", "이어가기 좋은 주제", "다시 확인할 곳"):
        assert heading in html
    assert "2009-04-01" in html  # 등록일
    assert "삭제 대기(곧 등록 가능)" in html  # 취득 상태
    assert "빵집 블로그로 시작해" in html  # 주제 역사(줄글)
    assert "홈베이킹" in html  # 추천 주제


def test_detail_speaks_plainly_about_time_and_reasons(sample_result):
    """도구를 쓰는 사람은 비개발자다 — 기계 표기(T)와 한자말은 화면에 남기지 않는다."""
    sample_result.finished_at = "2026-08-05T11:42:07.123456"
    judge(sample_result)
    html = detail_fragment(sample_result)

    assert "2026년 8월 5일 11:42" in html
    assert "2026-08-05T11:42" not in html
    assert "사면 안 되는 이유" in html and "조심할 이유" in html
    assert "치명 사유" not in html


def test_detail_escapes_injected_html(sample_result):
    sample_result.ai.one_liner = '<script>alert("x")</script>'
    judge(sample_result)
    html = detail_fragment(sample_result)

    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_recheck_links_cover_the_planned_sources():
    labels = " ".join(label for label, _ in recheck_links("example.com"))
    urls = " ".join(url for _, url in recheck_links("example.com"))

    assert "site:" in labels and "백링크" in labels and "상표" in labels
    assert "ahrefs.com" in urls and "moz.com" in urls and "whoisology.com" in urls


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
    labels = " ".join(label for label, _ in recheck_links("example.com"))
    assert "다른 사이트가 건 링크" in labels and "주인 바뀐 이력" in labels

    sample_result.ai.spam.verdict = "unclear"
    assert "판단 유보(unclear)" in detail_fragment(sample_result)


def test_index_page_states_the_score_cutoffs(sample_result):
    judge(sample_result)
    assert "75점부터 매입 후보" in render_index([sample_result])


def test_report_survives_a_domain_with_no_evidence():
    from domainchecker.models import DomainResult

    empty = judge(DomainResult(domain="blank.com"))
    html = detail_fragment(empty)

    assert empty.verdict is Verdict.REVIEW
    assert "점수 없음" in html
    assert "저장된 이력이 없습니다" in html
