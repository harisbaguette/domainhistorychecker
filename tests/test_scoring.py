"""판정 우선순위 검증 — PLAN §2의 사례를 그대로 시험한다."""

import pytest

from domainchecker.analyze.scoring import compute_score, judge
from domainchecker.models import (
    AIAnalysis,
    Authority,
    CheckState,
    CheckStatus,
    DomainResult,
    IndexInfo,
    Registration,
    Reputation,
    RuleFindings,
    SpamJudgement,
    Verdict,
    WaybackHistory,
)

OK = CheckState(status=CheckStatus.OK)


def healthy(**overrides) -> DomainResult:
    """필수 검사 전부 확인 + 신호 깨끗한 우량 도메인."""
    result = DomainResult(
        domain="good.com",
        wayback=WaybackHistory(
            check=OK,
            total_captures=400,
            first_seen="20080101000000",
            last_seen="20240101000000",
            year_counts={str(y): 20 for y in range(2008, 2025)},
            redirect_ratio=0.02,
        ),
        registration=Registration(check=OK, created="2008-01-01", acquisition="구매 가능(미등록)"),
        spamhaus=Reputation(check=OK),
        index=IndexInfo(check=OK, indexed_count=25, titles=["동네 빵집 이야기"]),
        authority=Authority(check=OK, page_rank=4.0),
        ai=AIAnalysis(
            check=OK,
            model="deepseek/deepseek-v4-flash-0731",
            spam=SpamJudgement(verdict="clean", confidence=0.9),
            transition="빵집 블로그 주제를 계속 유지",
            one_liner="꾸준히 운영된 생활 블로그",
        ),
        rules=RuleFindings(check=OK, languages=["ko"]),
    )
    for key, value in overrides.items():
        setattr(result, key, value)
    return result


def test_healthy_domain_gets_buy():
    result = judge(healthy())
    assert result.verdict is Verdict.BUY
    assert result.score >= 75
    assert result.partial_score is False
    assert result.fatal_reasons == [] and result.warn_reasons == []


def test_blacklisted_domain_is_rejected_first():
    result = judge(healthy(spamhaus=Reputation(check=OK, listed=True, codes=["피싱 도메인"])))
    assert result.verdict is Verdict.REJECT
    assert "블랙리스트" in result.fatal_reasons[0]


def test_ai_high_confidence_with_quote_alone_is_fatal():
    result = judge(
        healthy(
            ai=AIAnalysis(
                check=OK,
                spam=SpamJudgement(
                    verdict="spam", confidence=0.9, quotes=["온라인 카지노 가입 코드 안내"]
                ),
            )
        )
    )
    assert result.verdict is Verdict.REJECT
    assert "확신도 0.90" in result.fatal_reasons[0]


def test_ai_spam_without_quote_is_conflict_not_fatal():
    result = judge(
        healthy(ai=AIAnalysis(check=OK, spam=SpamJudgement(verdict="spam", confidence=0.95)))
    )
    assert result.verdict is Verdict.REVIEW
    assert any("신호 충돌" in reason for reason in result.warn_reasons)


def test_ai_spam_below_threshold_is_conflict_not_fatal():
    result = judge(
        healthy(
            ai=AIAnalysis(
                check=OK, spam=SpamJudgement(verdict="spam", confidence=0.7, quotes=["인용"])
            )
        )
    )
    assert result.verdict is Verdict.REVIEW
    assert any("신호 충돌" in reason for reason in result.warn_reasons)


def test_rules_and_ai_agreeing_is_fatal():
    result = judge(
        healthy(
            rules=RuleFindings(check=OK, doorway=True),
            ai=AIAnalysis(
                check=OK, spam=SpamJudgement(verdict="spam", confidence=0.6, quotes=["인용"])
            ),
        )
    )
    assert result.verdict is Verdict.REJECT
    assert "규칙 검사와 AI가 모두" in result.fatal_reasons[0]


def test_rules_spam_but_ai_clean_is_conflict():
    result = judge(healthy(rules=RuleFindings(check=OK, hidden_text=True)))
    assert result.verdict is Verdict.REVIEW
    assert any("신호 충돌" in reason for reason in result.warn_reasons)


def test_missing_required_check_forbids_buy():
    result = judge(
        healthy(
            index=IndexInfo(
                check=CheckState(status=CheckStatus.NOT_RUN, note="Serper 키가 없습니다.")
            )
        )
    )
    assert result.verdict is Verdict.REVIEW
    assert any("필수 검사 미확인" in reason for reason in result.warn_reasons)
    assert "구글 색인" in result.not_run[0]


def test_unchecked_required_check_is_listed_as_unchecked():
    result = judge(
        healthy(
            spamhaus=Reputation(
                check=CheckState(status=CheckStatus.UNCHECKED, note="조회가 차단되었습니다.")
            )
        )
    )
    assert result.verdict is Verdict.REVIEW
    assert result.unchecked and "스팸하우스" in result.unchecked[0]


def test_no_history_is_warn_not_reject():
    result = judge(
        healthy(
            wayback=WaybackHistory(check=OK, total_captures=0),
            ai=AIAnalysis(
                check=CheckState(status=CheckStatus.UNCHECKED, note="읽을 본문이 없습니다.")
            ),
            rules=RuleFindings(check=CheckState(status=CheckStatus.UNCHECKED)),
        )
    )
    assert result.verdict is Verdict.NO_HISTORY
    assert result.partial_score is True  # 수집된 항목만의 부분 점수
    assert result.score is not None
    assert any("이력이 아예 없" in reason for reason in result.warn_reasons)


def test_excluded_snapshot_is_warn():
    result = judge(
        healthy(
            wayback=WaybackHistory(
                check=CheckState(status=CheckStatus.UNCHECKED, note="열람 차단"),
                excluded=True,
            )
        )
    )
    assert result.verdict is Verdict.REVIEW
    assert any("차단" in reason for reason in result.warn_reasons)


def test_trademark_and_sensitive_industry_force_warn():
    result = judge(healthy(rules=RuleFindings(check=OK, brand_hits=["nike"])))
    assert result.verdict is Verdict.REVIEW
    assert any("상표 충돌" in reason for reason in result.warn_reasons)

    result2 = judge(healthy(rules=RuleFindings(check=OK, sensitive_terms=["도박:카지노"])))
    assert result2.verdict is Verdict.REVIEW
    assert any("민감 업종" in reason for reason in result2.warn_reasons)


def test_low_score_rejects_only_when_everything_was_checked():
    weak = healthy(
        wayback=WaybackHistory(
            check=OK,
            total_captures=10,
            first_seen="20230101000000",
            last_seen="20230601000000",
            redirect_ratio=0.9,
        ),
        authority=Authority(check=OK, page_rank=0.0),
        index=IndexInfo(check=OK, indexed_count=0),
        ai=AIAnalysis(
            check=OK,
            spam=SpamJudgement(verdict="unclear", confidence=1.0),
            transition="정상 주제에서 도박 관련으로 위험하게 바뀜",
        ),
        rules=RuleFindings(check=OK, parking_ratio=0.9, language_shift=True),
    )
    result = judge(weak)
    assert result.score < 50
    assert result.verdict is Verdict.REJECT
    assert "기준(50점)" in result.fatal_reasons[0]


def test_low_partial_score_is_not_rejected_when_evidence_is_missing():
    """증거가 비어 점수가 낮은 도메인은 ❌가 아니라 ⚠️로 내려가야 한다."""
    thin = healthy(
        wayback=WaybackHistory(check=CheckState(status=CheckStatus.UNCHECKED, note="접속 실패")),
        ai=AIAnalysis(check=CheckState(status=CheckStatus.UNCHECKED, note="본문 없음")),
        rules=RuleFindings(check=OK, doorway=True, hidden_text=True, link_farm=True),
        authority=Authority(check=OK, page_rank=0.0),
        index=IndexInfo(check=OK, indexed_count=0),
    )
    result = judge(thin)
    assert result.partial_score is True
    assert result.score < 50  # 부분 점수는 낮지만
    assert result.verdict is Verdict.REVIEW  # 필수 검사가 비어 ❌로 내리지 않는다
    assert any("필수 검사 미확인" in reason for reason in result.warn_reasons)


def test_partial_score_normalises_over_confirmed_items_only():
    thin = healthy(
        wayback=WaybackHistory(check=CheckState(status=CheckStatus.UNCHECKED)),
        ai=AIAnalysis(check=CheckState(status=CheckStatus.UNCHECKED)),
        rules=RuleFindings(check=CheckState(status=CheckStatus.UNCHECKED)),
        authority=Authority(check=OK, page_rank=5.0),
        index=IndexInfo(check=OK, indexed_count=50),
    )
    score = compute_score(thin)
    counted = [i for i in score.items if i.earned is not None]

    assert [i.name for i in counted] == ["inheritance"]  # 승계 자산만 확인됨
    assert score.partial is True
    # 10(중립) + 7(권위 상한) + 3(색인) = 20 / 20 → 100
    assert score.total == pytest.approx(100.0)


def test_score_is_none_when_nothing_could_be_measured():
    empty = DomainResult(domain="x.com")
    score = compute_score(empty)
    assert score.computable is False
    assert score.total is None
    result = judge(empty)
    assert result.verdict is Verdict.REVIEW
