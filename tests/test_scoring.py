"""판정 우선순위 검증 — 기계 거부권 → AI 종합 판정 순서를 그대로 시험한다."""

import pytest

from domainchecker.analyze.scoring import availability_of, judge
from domainchecker.models import (
    AVAILABILITY_LABEL,
    AIAnalysis,
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
        ai=AIAnalysis(
            check=OK,
            model="deepseek/deepseek-v4-flash-0731",
            spam=SpamJudgement(verdict="clean", confidence=0.9),
            transition="빵집 블로그 주제를 계속 유지",
            one_liner="꾸준히 운영된 생활 블로그",
            verdict="buy",
            buy_score=90.0,
            verdict_reason="17년간 주제가 한결같고 스팸 흔적이 없음",
        ),
        rules=RuleFindings(check=OK, languages=["ko"]),
    )
    for key, value in overrides.items():
        setattr(result, key, value)
    return result


def test_healthy_domain_gets_buy():
    result = judge(healthy())
    assert result.verdict is Verdict.BUY
    assert result.score == 90.0  # 점수 = AI가 매긴 매입 매력도
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
                # 문턱(0.7) 밑의 확신 — 단독 치명이 아니라 신호 충돌 경고로 남는다.
                check=OK, spam=SpamJudgement(verdict="spam", confidence=0.5, quotes=["인용"])
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


@pytest.mark.parametrize("verdict", ["clean", "unclear", "unknown"])
def test_rules_spam_with_any_non_spam_ai_answer_is_conflict(verdict):
    """AI의 '유보'를 무혐의로 읽어 ✅를 내주던 구멍(심사 A2) — 세 답 모두 충돌이다."""
    result = judge(
        healthy(
            rules=RuleFindings(check=OK, doorway=True),
            ai=AIAnalysis(check=OK, spam=SpamJudgement(verdict=verdict, confidence=0.5)),
        )
    )
    assert any("신호 충돌" in reason for reason in result.warn_reasons)
    assert result.verdict is not Verdict.BUY


def test_ai_reject_verdict_rejects_with_its_reason():
    """AI가 '사면 안 됨'이라고 하면 그 이유가 빨간 도장의 사유로 올라온다."""
    result = judge(
        healthy(
            ai=AIAnalysis(
                check=OK,
                spam=SpamJudgement(verdict="unclear", confidence=0.5),
                verdict="reject",
                buy_score=15.0,
                verdict_reason="2019년부터 도박 홍보로 운영된 흔적이 뚜렷함",
            )
        )
    )
    assert result.verdict is Verdict.REJECT
    assert result.fatal_reasons == ["2019년부터 도박 홍보로 운영된 흔적이 뚜렷함"]
    assert result.score == 15.0


def test_ai_review_verdict_keeps_it_for_a_human():
    result = judge(
        healthy(
            ai=AIAnalysis(
                check=OK,
                spam=SpamJudgement(verdict="clean", confidence=0.8),
                verdict="review",
                buy_score=55.0,
                verdict_reason="깨끗하지만 주제가 여러 번 바뀌어 연속성 가치가 낮음",
            )
        )
    )
    assert result.verdict is Verdict.REVIEW
    assert result.score == 55.0


def test_missing_required_check_forbids_buy():
    result = judge(
        healthy(
            index=IndexInfo(
                check=CheckState(status=CheckStatus.NOT_RUN, note="색인 검사를 돌리지 않았습니다.")
            )
        )
    )
    assert result.verdict is Verdict.REVIEW
    assert any("필수 검사 미확인" in reason for reason in result.warn_reasons)
    assert "웹 색인" in result.not_run[0]


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
    assert result.partial_score is True  # AI 뜻 읽기 없이 나온 결과는 참고치
    assert result.score is None  # 점수는 AI 매력도뿐 — AI가 없으면 없음
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


def test_ai_trademark_risk_forces_warn():
    """상표 판단은 이제 AI 소견 하나로 본다(낱말 목록 검사 제거)."""
    result = judge(
        healthy(
            ai=AIAnalysis(
                check=OK,
                spam=SpamJudgement(verdict="clean", confidence=0.9),
                trademark="나이키 상표와 충돌 소지",
                trademark_risk=True,
            )
        )
    )
    assert result.verdict is Verdict.REVIEW
    assert any("상표 충돌" in reason for reason in result.warn_reasons)


def test_missing_evidence_lands_in_review_not_reject():
    """증거가 비면 ❌가 아니라 ⚠️(검토)로 — 모르는 것을 나쁜 것으로 찍지 않는다."""
    thin = healthy(
        wayback=WaybackHistory(check=CheckState(status=CheckStatus.UNCHECKED, note="접속 실패")),
        ai=AIAnalysis(check=CheckState(status=CheckStatus.UNCHECKED, note="본문 없음")),
        # 흔적 2종까지는 규칙 단독 치명(3종)에 못 미친다.
        rules=RuleFindings(
            check=OK, doorway=True, hidden_text=True, language_shift=True,
        ),
        index=IndexInfo(check=OK, indexed_count=0),
    )
    result = judge(thin)
    assert result.partial_score is True
    assert result.score is None
    assert result.verdict is Verdict.REVIEW  # 필수 검사가 비어 ❌로 내리지 않는다
    assert any("필수 검사 미확인" in reason for reason in result.warn_reasons)


def test_no_ai_means_no_green_stamp():
    """뜻 읽기(AI) 없이 ✅를 찍지 않는다 — 키 없이 돌리면 최고 판정은 '검토'다."""
    keyless = judge(
        healthy(
            ai=AIAnalysis(check=CheckState(status=CheckStatus.NOT_RUN, note="키 없음")),
        )
    )

    assert keyless.verdict is Verdict.REVIEW
    assert keyless.score is None
    assert "AI 분석" in keyless.not_run
    assert keyless.one_liner  # 기계적으로 만든 한 줄 평가가 대신 들어간다


def test_three_mechanical_spam_marks_reject_the_domain_without_any_ai():
    """AI가 없다고 규칙이 잡은 흔적 세 종류를 노랑에서 멈추게 두면 안 된다."""
    result = judge(
        healthy(
            ai=AIAnalysis(check=CheckState(status=CheckStatus.NOT_RUN)),
            rules=RuleFindings(
                check=OK, doorway=True, hidden_text=True, link_farm=True, languages=["ko"]
            ),
        )
    )

    assert result.verdict is Verdict.REJECT
    assert any("기계적 검사만으로도 확정" in reason for reason in result.fatal_reasons)

    # AI 를 켰다고 ❌ 가 ⚠️ 로 물러지면 "AI 켜는 게 손해"가 된다 — 같은 흔적이면 같은 판정.
    with_ai = judge(
        healthy(
            ai=AIAnalysis(check=OK, spam=SpamJudgement(verdict="clean", confidence=0.9)),
            rules=RuleFindings(
                check=OK, doorway=True, hidden_text=True, link_farm=True, languages=["ko"]
            ),
        )
    )
    assert with_ai.verdict is Verdict.REJECT


def test_a_rejected_domain_leads_its_one_liner_with_the_reason():
    result = judge(
        healthy(
            ai=AIAnalysis(check=CheckState(status=CheckStatus.NOT_RUN)),
            spamhaus=Reputation(check=OK, listed=True, codes=["스팸 도메인"]),
        )
    )

    assert result.verdict is Verdict.REJECT
    assert result.one_liner.startswith("스팸하우스 블랙리스트")


def test_no_history_does_not_say_the_same_thing_twice():
    result = judge(
        healthy(
            wayback=WaybackHistory(check=OK),
            rules=RuleFindings(check=CheckState(status=CheckStatus.UNCHECKED, note="본문 없음")),
        )
    )

    assert result.verdict is Verdict.NO_HISTORY
    assert not any("운영방식 규칙 검사" in reason for reason in result.warn_reasons)
    assert any("저장된 과거 이력이 아예 없습니다" in reason for reason in result.warn_reasons)


def test_rules_finding_spam_without_ai_still_blocks_a_buy():
    """AI가 없을 때 규칙이 잡은 흔적을 그냥 넘기면 스팸 도메인에 초록이 나간다."""
    result = judge(
        healthy(
            ai=AIAnalysis(check=CheckState(status=CheckStatus.NOT_RUN, note="키 없음")),
            rules=RuleFindings(check=OK, doorway=True, hidden_text=True, languages=["ko"]),
        )
    )

    assert result.verdict is not Verdict.BUY
    assert any("규칙 검사가 스팸 운영 흔적" in reason for reason in result.warn_reasons)


def test_the_plain_one_liner_only_states_what_was_actually_counted():
    result = judge(
        healthy(
            ai=AIAnalysis(check=CheckState(status=CheckStatus.NOT_RUN)),
            index=IndexInfo(check=OK, indexed_count=25, current_parking=True),
        )
    )

    assert "저장된 화면 400장" in result.one_liner
    assert "나쁜 운영 흔적은 안 나옴" in result.one_liner
    assert "판매용 빈 화면" in result.one_liner


@pytest.mark.parametrize(
    ("acquisition", "expected"),
    [
        ("구매 가능(미등록)", "free"),
        ("삭제 대기(곧 등록 가능)", "soon"),
        ("복원 기간(경매·복원 대상)", "auction"),
        ("복원 진행 중", "auction"),
        ("등록 중(이전 잠금)", "taken"),
        ("자동 갱신 기간", "taken"),
        ("알 수 없음", "unknown"),
        ("", "unknown"),
    ],
)
def test_availability_of_covers_every_branch(acquisition, expected):
    assert availability_of(acquisition) == expected


def test_judge_fills_the_buy_now_column():
    result = judge(healthy(registration=Registration(check=OK, acquisition="삭제 대기(곧 등록 가능)")))
    assert result.acquisition == "삭제 대기(곧 등록 가능)"
    assert result.availability == "soon"
    assert result.availability_label == AVAILABILITY_LABEL["soon"]

    taken = judge(healthy(registration=Registration(check=OK, acquisition="등록 중(보류)")))
    assert taken.availability == "taken"
    assert taken.availability_label == AVAILABILITY_LABEL["taken"]


def test_score_is_none_when_nothing_could_be_measured():
    result = judge(DomainResult(domain="x.com"))
    assert result.score is None
    assert result.verdict is Verdict.REVIEW
