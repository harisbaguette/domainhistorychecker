import io
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from domainchecker.clients.offline_lists import OfflineLists
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


@pytest.fixture(autouse=True)
def _offline_lists_stay_offline(monkeypatch):
    """0단 위험 명단을 진짜로 내려받지 않는다 — 시험은 인터넷에 나가지 않는다.

    명단 받기 자체를 시험하는 파일(test_offline_lists.py)은 이 자리를 다시
    덮어써서 가짜 서버로 받아 본다.
    """

    async def no_download(self, http, category):
        return False

    monkeypatch.setattr(OfflineLists, "_download", no_download)


def blacklist_bytes(category: str, domains: list[str]) -> bytes:
    """UT1 명단 뭉치와 똑같은 모양의 가짜 파일 — <갈래>/domains 한 줄에 한 도메인."""
    body = ("\n".join(domains) + "\n").encode("utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        info = tarfile.TarInfo(f"{category}/domains")
        info.size = len(body)
        tar.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def put_blacklist(base: Path | str, category: str, domains: list[str]) -> Path:
    """받아 둔 명단이 이미 있는 상태로 만든다(내려받기 없이 0단을 켜는 길)."""
    from domainchecker.clients.offline_lists import cache_dir

    target = cache_dir(base) / f"{category}.tar.gz"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blacklist_bytes(category, domains))
    return target


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
