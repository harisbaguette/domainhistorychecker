"""판정 우선순위 검증 — 치명 → 점수 미달 → 경고 → 구간 순서를 그대로 시험한다."""

import pytest

from domainchecker.analyze.scoring import availability_of, compute_score, judge
from domainchecker.models import (
    AVAILABILITY_LABEL,
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


def test_transition_risk_flag_costs_points_but_prose_alone_does_not():
    """'위험 전환은 없었습니다' 같은 문장이 감점되던 오탐(심사 B1)."""
    safe = judge(
        healthy(
            ai=AIAnalysis(
                check=OK,
                spam=SpamJudgement(verdict="clean", confidence=0.9),
                transition="도박·성인 같은 위험 업종으로의 전환은 없었습니다.",
                transition_risk=False,
            )
        )
    )
    risky = judge(
        healthy(
            ai=AIAnalysis(
                check=OK,
                spam=SpamJudgement(verdict="clean", confidence=0.9),
                transition="생활 블로그에서 도박 홍보로 넘어감",
                transition_risk=True,
            )
        )
    )
    safe_item = next(i for i in safe.scoring.items if i.name == "transition")
    risky_item = next(i for i in risky.scoring.items if i.name == "transition")

    # 서술문에 "전환"이 들어간 것만으로는 소폭(-2)에 그치고, 위험 판정(-9)은 붙지 않는다.
    assert safe_item.earned == pytest.approx(13.0)
    assert "정상→위험" not in safe_item.note
    assert risky_item.earned == pytest.approx(6.0)
    assert "정상→위험" in risky_item.note


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
            transition_risk=True,  # 위험 전환은 이제 AI가 스키마 값으로 직접 답한다
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
        # 흔적 2종까지는 규칙 단독 치명(3종)에 못 미친다 — 여기서 보려는 것은
        # "증거가 비어 점수가 낮은 경우"이지 "규칙이 스팸을 확정한 경우"가 아니다.
        rules=RuleFindings(
            check=OK, doorway=True, hidden_text=True, language_shift=True,
            sensitive_terms=["도박:카지노"],
        ),
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


def test_a_domain_can_be_bought_with_no_api_keys_at_all():
    """키가 하나도 없어도(=AI·권위 점수 없이) 초록 판정이 나와야 한다."""
    keyless = judge(
        healthy(
            ai=AIAnalysis(check=CheckState(status=CheckStatus.NOT_RUN, note="키 없음")),
            authority=Authority(check=CheckState(status=CheckStatus.NOT_RUN, note="키 없음")),
        )
    )

    assert keyless.verdict is Verdict.BUY
    assert keyless.warn_reasons == []
    assert "AI 분석" in keyless.not_run and "권위 점수" in keyless.not_run
    assert keyless.one_liner  # 기계적으로 만든 한 줄 평가가 대신 들어간다


def test_turning_the_ai_on_never_costs_points_by_itself():
    """AI를 켰다는 이유만으로 점수가 깎이면 'AI 안 쓰는 게 이득'이 된다."""
    without = judge(healthy(ai=AIAnalysis(check=CheckState(status=CheckStatus.NOT_RUN))))
    with_ai = judge(healthy())  # AI 가 clean 이라고 답한 경우

    assert with_ai.score >= without.score
    # 안 돈 AI 몫은 만점으로 채우는 게 아니라 분모에서 빠진다.
    safety = next(i for i in without.scoring.items if i.name == "safety")
    assert safety.max_points == 25
    assert safety.earned == pytest.approx(25.0)


def test_the_ai_penalty_keeps_its_weight_when_both_sources_ran():
    """분모를 나눴다고 감점이 물러지면 안 된다 — 예전 비율 그대로여야 한다."""
    unclear = judge(
        healthy(ai=AIAnalysis(check=OK, spam=SpamJudgement(verdict="unclear", confidence=0.9)))
    )
    item = next(i for i in unclear.scoring.items if i.name == "safety")

    assert item.max_points == 45
    assert item.earned == pytest.approx(45 - 45 * 0.3 * 0.9)


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


def test_no_authority_data_is_neutral_not_a_zero_score():
    """자료가 없는 것을 0.00 으로 적으면 '권위가 바닥인 도메인'으로 잘못 읽힌다."""
    thin = healthy(
        authority=Authority(check=OK, has_data=False, page_rank=0.0),
        index=IndexInfo(check=OK, indexed_count=0),
    )
    item = next(i for i in compute_score(thin).items if i.name == "inheritance")

    assert "권위 자료 없음(중립)" in item.note
    assert "0.00" not in item.note
    assert item.earned == pytest.approx(10.0)  # 중립 그대로


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
    empty = DomainResult(domain="x.com")
    score = compute_score(empty)
    assert score.computable is False
    assert score.total is None
    result = judge(empty)
    assert result.verdict is Verdict.REVIEW
