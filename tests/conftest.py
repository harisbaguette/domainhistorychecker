from datetime import UTC, datetime, timedelta

import pytest

from domainchecker.models import (
    AIAnalysis,
    CheckState,
    CheckStatus,
    DomainResult,
    IndexInfo,
    Registration,
    Reputation,
    RuleFindings,
    Snapshot,
    SpamJudgement,
    WaybackHistory,
)

OK = CheckState(status=CheckStatus.OK)


@pytest.fixture
def sample_result() -> DomainResult:
    """검사가 모두 끝난 도메인 하나 — 보고서·상세 화면 시험용."""
    return DomainResult(
        domain="example.com",
        wayback=WaybackHistory(
            check=OK,
            total_captures=120,
            first_seen="20100601000000",
            last_seen="20240601000000",
            year_counts={"2010": 5, "2011": 8, "2023": 4, "2024": 3},
            gap_years=[2012],
            redirect_ratio=0.05,
            selected=[
                Snapshot(timestamp="20100601000000", original="http://example.com/"),
                Snapshot(timestamp="20240601000000", original="http://example.com/"),
            ],
        ),
        registration=Registration(
            check=OK,
            source="gabia",
            acquisition="삭제 대기(곧 등록 가능)",
        ),
        spamhaus=Reputation(check=CheckState(status=CheckStatus.OK, note="블랙리스트에 없습니다.")),
        index=IndexInfo(check=OK, indexed_count=12, titles=["동네 빵집 이야기"]),
        ai=AIAnalysis(
            check=OK,
            model="deepseek/deepseek-v4-flash-0731",
            topic_history="빵집 블로그로 시작해 그대로 이어짐",
            spam=SpamJudgement(verdict="clean", confidence=0.9),
            transition="주제 유지",
            content_quality="직접 쓴 글로 보임",
            trademark="문제 없음",
            recommended_topics=[{"topic": "홈베이킹", "reason": "과거 주제와 인접"}],
            one_liner="꾸준히 운영된 생활 블로그",
            verdict="buy",
            buy_score=92.0,
            verdict_reason="주제가 한결같고 위험 흔적이 없어 이어받을 가치가 높음",
        ),
        rules=RuleFindings(check=OK, languages=["ko"], evidence=[]),
        # 달력 날짜를 박아 두면 며칠 지나 "묵은 저장분" 기준(7일)에 걸려 시험이
        # 저절로 깨진다 — 언제 돌려도 어제 검사한 것으로 만든다.
        finished_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(timespec="seconds"),
    )
