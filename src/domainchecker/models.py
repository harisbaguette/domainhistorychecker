"""Pydantic models shared across collectors, analyzers and the pipeline."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class CheckStatus(StrEnum):
    """Outcome of a single check.

    OK        = the check ran and produced an answer.
    UNCHECKED = the check was attempted but failed (network error, block, quota).
    NOT_RUN   = the check was never attempted (no API key, or disabled in config).
    """

    OK = "OK"
    UNCHECKED = "UNCHECKED"
    NOT_RUN = "NOT_RUN"


class Verdict(StrEnum):
    """Final judgement. NO_HISTORY is a warn-level verdict shown as 이력 없음(신중)."""

    BUY = "BUY"
    REVIEW = "REVIEW"
    REJECT = "REJECT"
    NO_HISTORY = "NO_HISTORY"


WARN_VERDICTS = (Verdict.REVIEW, Verdict.NO_HISTORY)

# Labels are text-only; the UI shows verdict colour with a tinted badge instead
# of emoji (design system rule: no emoji in rendered UI).
VERDICT_LABEL = {
    Verdict.BUY: "매입 후보",
    Verdict.REVIEW: "검토 필요",
    Verdict.REJECT: "제외",
    Verdict.NO_HISTORY: "이력 없음(신중)",
}

# 취득 상태를 "지금 살 수 있나" 한 축으로 접은 것. 표의 별도 열로 보여 준다.
AVAILABILITY_LABEL = {
    "free": "지금 등록 가능(주인 없음)",
    "soon": "곧 등록 가능(삭제 대기)",
    "auction": "복원·경매 절차 중",
    "taken": "남이 등록 중(못 삼)",
    "unknown": "확인 안 됨",
}

# Names of the checks that must all be OK before a ✅ verdict may be issued.
# 전부 키 없이 도는 것들이다 — 판정의 뼈대는 기계적 검사(파이썬)로 세우고,
# AI는 키(OpenRouter)를 넣었을 때만 도는 보조로 내렸다.
REQUIRED_CHECKS = ("wayback", "registration", "spamhaus", "index", "rules")
OPTIONAL_CHECKS = ("ai", "safebrowsing")

# 미확인·미실시 목록에 이름을 올릴 검사 전부. 여기 빠지면 "검사를 못 했다"는 사실이
# 화면 어디에도 안 나와, 못 한 것이 "깨끗함"으로 읽힌다(rules 가 그랬다).
CHECK_LABEL = {
    "wayback": "과거 이력(웨이백)",
    "registration": "등록 정보",
    "spamhaus": "스팸하우스 블랙리스트",
    "index": "웹 색인·현재 상태",
    "ai": "AI 분석",
    "rules": "운영방식 규칙 검사",
    "safebrowsing": "세이프 브라우징",
}


class CheckState(BaseModel):
    """Status plus a Korean explanation, attached to every collected section."""

    status: CheckStatus = CheckStatus.NOT_RUN
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status is CheckStatus.OK


class Snapshot(BaseModel):
    """One Wayback capture."""

    timestamp: str
    original: str
    status_code: str = ""
    mimetype: str = ""
    digest: str = ""

    @property
    def year(self) -> int:
        return int(self.timestamp[:4])

    @property
    def raw_url(self) -> str:
        # `id_` returns the stored bytes without the Wayback toolbar rewrite.
        return f"https://web.archive.org/web/{self.timestamp}id_/{self.original}"


class WaybackHistory(BaseModel):
    check: CheckState = CheckState()
    excluded: bool = False  # site is blocked from the archive (possible cover-up)
    total_captures: int = 0
    first_seen: str = ""
    last_seen: str = ""
    year_counts: dict[str, int] = Field(default_factory=dict)
    gap_years: list[int] = Field(default_factory=list)
    redirect_ratio: float = 0.0
    selected: list[Snapshot] = Field(default_factory=list)
    pages: list[dict] = Field(default_factory=list)  # extracted snapshot contents

    @property
    def has_history(self) -> bool:
        return self.total_captures > 0

    @property
    def age_years(self) -> float:
        if not self.first_seen or not self.last_seen:
            return 0.0
        return max(0.0, (int(self.last_seen[:4]) - int(self.first_seen[:4])) + 1)


class Registration(BaseModel):
    check: CheckState = CheckState()
    source: str = ""  # "gabia" (예전 저장분에는 "rdap" | "whois")
    created: str = ""
    expires: str = ""
    registrar: str = ""
    statuses: list[str] = Field(default_factory=list)
    acquisition: str = "알 수 없음"  # 취득 상태 (사용자 표시용)
    redropped: bool = False  # re-registered after a drop


class Reputation(BaseModel):
    check: CheckState = CheckState()
    listed: bool = False
    codes: list[str] = Field(default_factory=list)


class IndexInfo(BaseModel):
    check: CheckState = CheckState()
    indexed_count: int = 0
    titles: list[str] = Field(default_factory=list)
    current_parking: bool = False  # today's parking page, not a past-history signal
    sample_paths: list[str] = Field(default_factory=list)  # 색인에 남은 주소 흔적(AI 입력용)


class SpamJudgement(BaseModel):
    verdict: str = "unknown"  # "spam" | "clean" | "unclear" | "unknown"
    confidence: float = 0.0
    quotes: list[str] = Field(default_factory=list)


class AIAnalysis(BaseModel):
    check: CheckState = CheckState()
    model: str = ""
    fallback_used: bool = False
    topic_history: str = ""
    spam: SpamJudgement = SpamJudgement()
    transition: str = ""
    transition_risk: bool = False  # 정상→위험 업종·언어 급변 전환이 있었나
    content_quality: str = ""
    trademark: str = ""
    trademark_risk: bool = False
    recommended_topics: list[dict] = Field(default_factory=list)  # {topic, reason}
    # 주제가 바뀐 시기별 역사 — {start, end, topic}. 시기마다 캡쳐 한 장을 찍는다.
    topic_periods: list[dict] = Field(default_factory=list)
    one_liner: str = ""


class Capture(BaseModel):
    """One Playwright screenshot of an archived page."""

    label: str  # "말기" 또는 "위험 신호 시기"
    timestamp: str
    url: str
    file: str  # data/ 기준 상대 경로


class Captures(BaseModel):
    check: CheckState = CheckState()
    items: list[Capture] = Field(default_factory=list)


class RuleFindings(BaseModel):
    check: CheckState = CheckState()
    doorway: bool = False
    hidden_text: bool = False
    link_farm: bool = False
    autogenerated: bool = False
    parking_ratio: float = 0.0
    languages: list[str] = Field(default_factory=list)
    language_shift: bool = False
    evidence: list[str] = Field(default_factory=list)
    risk_timestamps: list[str] = Field(default_factory=list)  # 캡쳐할 위험 신호 시기

    @property
    def spam_operation(self) -> bool:
        return self.doorway or self.hidden_text or self.link_farm or self.autogenerated


class ScoreItem(BaseModel):
    name: str
    label: str
    max_points: int
    earned: float | None = None  # None = 미확인 → 분모에서 제외
    note: str = ""


class Score(BaseModel):
    items: list[ScoreItem] = Field(default_factory=list)
    total: float | None = None  # 0~100, 분모 정규화 결과
    partial: bool = False  # True면 부분 점수(참고치)
    computable: bool = False


class DomainResult(BaseModel):
    domain: str
    verdict: Verdict = Verdict.REVIEW
    verdict_label: str = ""
    score: float | None = None
    partial_score: bool = False
    one_liner: str = ""
    recommended_topics: list[dict] = Field(default_factory=list)
    acquisition: str = "알 수 없음"
    availability: str = "unknown"  # free | soon | auction | taken | unknown
    availability_label: str = AVAILABILITY_LABEL["unknown"]

    wayback: WaybackHistory = WaybackHistory()
    registration: Registration = Registration()
    spamhaus: Reputation = Reputation()
    safebrowsing: Reputation = Reputation()
    index: IndexInfo = IndexInfo()
    ai: AIAnalysis = AIAnalysis()
    rules: RuleFindings = RuleFindings()
    captures: Captures = Captures()
    scoring: Score = Score()

    fatal_reasons: list[str] = Field(default_factory=list)
    warn_reasons: list[str] = Field(default_factory=list)
    unchecked: list[str] = Field(default_factory=list)  # 시도했으나 실패
    not_run: list[str] = Field(default_factory=list)  # 키 없음·꺼짐
    errors: list[str] = Field(default_factory=list)
    finished_at: str = ""
