"""Per-domain orchestration. Emits progress events for the (later) SSE layer."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from . import cache, capture
from .analyze import ai as ai_analyze
from .analyze import rules as rules_analyze
from .analyze import scoring
from .analyze.extract import snapshot_from_page
from .clients import freeindex, gabia, safebrowsing, spamhaus
from .clients.offline_lists import OfflineLists
from .clients.openrouter import OpenRouterClient
from .clients.wayback import WaybackClient, cluster_representatives
from .clients.wayback import short_path as wayback_short_path
from .config import Config, data_dir
from .models import (
    CheckState,
    CheckStatus,
    DomainResult,
    Reputation,
    RuleFindings,
    SpamJudgement,
    WaybackHistory,
)
from .ratelimit import AdaptiveRateLimiter
from .report import html as report_html

EventCallback = Callable[[dict], None] | None

RUN_STATE_NAME = "run_state.json"

# 검사 방식이 바뀌면 올린다. 저장분에 찍힌 번호가 이보다 낮으면 다시 검사한다 —
# 안 그러면 "키가 없어 못 했습니다"라고 적힌 예전 결과를 일주일 동안 계속 보여 준다.
# 4: 주제 시기별 역사(topic_periods)와 시기별 캡쳐가 추가됨(2026-08-09)
# 5: 유료 키 검사(Serper·권위 점수·바이러스토탈)를 없애고, 세이프 브라우징을
#    키 없는 공개 조회로 바꿈(2026-08-09)
# 6: 세이프 브라우징 상태값 오독 수정 — 안전(4)·자료 없음(6)까지 위험으로 읽어
#    모든 도메인이 탈락 판정을 받던 결과를 다시 검사하게 함(2026-08-10)
# 7: 구매 가능 판정을 RDAP·whois 해석에서 가비아 검색 확인으로 바꿈(2026-08-10)
# 8: 낱말 목록으로 뜻을 넘겨짚던 검사(민감 업종·상표·색인 오염어)를 걷어내고
#    그 판단을 AI에게 몰아줌 — 색인 주소 흔적을 AI 입력에 추가(2026-08-10)
# 9: 재현율 보강 — 공백 직후 해 우선 캡쳐(8장으로 확대) + 웨이백의 연도별 주소
#    흔적 표본을 AI 입력에 추가(본문 안 읽은 해도 훑게 함)(2026-08-10)
# 10: 표본 폐기 → 전수 확인 — 앞페이지 변경본 전부 + 하위 페이지 전부의 본문
#     읽기, AI 묶음 분할 전수 읽기, 확인 범위 숫자 보고. 진상 검증 지적 반영:
#     주소 전용 묶음, AI 단독 치명 문턱 0.7, 리다이렉트 시대 포함(2026-08-11)
# 11: 하위 페이지 "전부 정독"을 사람 방식으로 교체 — 주소 목록은 전부 훑되,
#     본문 정독은 AI가 수상하다고 고른 곳만(검사 시간 몇 분 수준으로 복귀)(2026-08-12)
# 12: 내용 단위 전수로 재설계 — 주소를 유형으로 묶어 모든 유형×시대 대표를 반드시
#     읽고(종류 단위 전수), 같은 내용(digest)은 두 번 안 읽음. AI 선별은 그 위에
#     얹는 보강으로 유지(2026-08-12)
# 13: 앞페이지 변경본에도 시대 접기 적용 — 해마다 처음·중간·끝 대표(실측: 이력 많은
#     도메인이 9분 초과 → 이 접기로 수 분 이내)(2026-08-12)
# 14: 판정 개편 — 수제 감점표 폐지, AI 종합 판정(buy/review/reject + 매입 매력도)
#     + 기계 거부권. 예전 점수로 찍힌 결과는 다시 검사해야 한다(2026-08-12)
# 15: 깔때기 3단으로 재배치 — 0단(공짜 공개 위험 명단) → 1단(싼 조회 한 번)
#     → 2단(살아남은 것만 본문 정독). 걸러진 도메인은 옛 화면을 아예 안 읽으므로
#     예전 결과와 확인 범위가 달라진다 — 다시 검사해야 한다(2026-08-22)
# 16: 깔때기 4단으로 순서 재배치 — 1단(주인 여부) → 2단(공개 위험 명단) →
#     3단(가장 최근 화면 한 장 + 싼 조회) → 4단(옛 화면 전수 정독). 3단에서
#     걸린 도메인은 옛 화면을 한 장도 안 읽으므로 확인 범위가 달라진다 —
#     저장분을 다시 검사해야 한다(2026-08-22)
ENGINE_VERSION = 16

# 화면 진행 문구 앞에 붙는 단계 이름 — 지금 어느 단에서 걸러지는 중인지 보이게 한다.
STAGE_OWNER = "1단"  # 주인 여부 — 계산으로 금방 끝난다(주인이 있으면 살 수가 없다)
STAGE_LIST = "2단"  # 공짜 — 받아 둔 명단만 본다(바깥 요청 0회)
STAGE_RECENT = "3단"  # 가장 최근 화면 한 장 + 싼 조회(블랙리스트·색인·웨이백 목록)
STAGE_DEEP = "4단"  # 비싼 정독 — 살아남은 것만 옛 본문을 전수로 읽는다

# 4단 조기 종료 — 읽는 도중 "이건 확정 제외"가 되면 남은 장을 안 읽는다.
# 흔적 3종이 겹쳐야 확정이라 몇 장은 모여야 하고, 장마다 따지면 그 계산이 더 비싸다.
VETO_MIN_PAGES = 4  # 이만큼은 읽고 나서 따진다
VETO_EVERY = 3  # 새 장이 이만큼 쌓일 때마다 한 번씩 따진다


def run_state_path(base: Path | str) -> Path:
    return Path(base) / RUN_STATE_NAME


def load_run_state(base: Path | str) -> list[str]:
    """Domains of a run that never finished, so 이어서 검사 survives a restart."""
    path = run_state_path(base)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    return [str(d) for d in (data.get("domains") or []) if str(d).strip()]


def save_run_state(base: Path | str, domains: list[str]) -> None:
    # 진행 기록을 못 남겨도 검사 자체는 계속한다.
    with contextlib.suppress(OSError):
        run_state_path(base).write_text(
            json.dumps({"domains": domains}, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def clear_run_state(base: Path | str) -> None:
    with contextlib.suppress(OSError):
        run_state_path(base).unlink(missing_ok=True)


def is_stale(payload: dict, max_days: int, *, engine_counts: bool = True) -> bool:
    """오래된 저장분은 다시 검사한다.

    "지금 등록 가능(주인 없음)" 같은 답은 하루만 지나도 뒤집힌다 — 묵은 답을
    오늘 것처럼 보여 주면 이미 남이 사 간 도메인을 사러 가게 만든다.
    검사 방식이 바뀐 뒤의 저장분도 마찬가지로 믿지 않는다.
    """
    # 검사 방식이 옛날 것인지는 "다시 검사할까"를 정할 때만 본다. 목록에 보여 줄지를
    # 정하는 쪽(server.stored_results)은 날짜만 본다 — 방식이 옛날 것이어도 결과를
    # 숨기면 사람이 이미 검사한 것을 잃어버린다.
    if engine_counts:
        # 과거 이력(웨이백)을 아예 못 읽은 저장분은 잠깐의 접속 장애가 낳은 반쪽
        # 결과다 — 7일 동안 믿고 보여 주면 안 되고, 다음 분석 때 다시 검사한다.
        wayback_status = str(((payload.get("wayback") or {}).get("check") or {}).get("status") or "")
        if wayback_status == "UNCHECKED":
            return True
        try:
            engine = int(payload.get("engine") or 0)
        except (TypeError, ValueError):
            engine = 0
        if engine < ENGINE_VERSION:
            return True
    if max_days <= 0:
        return False
    stamp = str(payload.get("finished_at") or "")
    try:
        finished = datetime.fromisoformat(stamp)
    except ValueError:
        return True  # 언제 검사했는지 모르는 저장분은 믿지 않는다
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    return (datetime.now(UTC) - finished).days >= max_days


class Pipeline:
    """Runs every check for a list of domains and writes results.json."""

    def __init__(
        self,
        config: Config,
        on_event: EventCallback = None,
        http: httpx.AsyncClient | None = None,
        resolver=None,
        base_dir: Path | str | None = None,
    ) -> None:
        self.config = config
        self.on_event = on_event
        self.http = http
        self.resolver = resolver
        self.base = Path(base_dir) if base_dir else data_dir(config)
        self.wayback_limiter = AdaptiveRateLimiter(rpm=config.start_rpm)
        self.cancel = asyncio.Event()
        # 2단 — 공개 위험 명단. 판이 시작할 때 한 번만 받아 두고, 도메인마다는
        # 받아 둔 파일만 본다(도메인당 바깥 요청 0회).
        self.lists = OfflineLists(self.base)
        self._blocked: dict[str, list[str]] = {}
        self._done = 0
        self._total = 0
        self._failed: list[str] = []

    async def _emit(self, event_type: str, **payload) -> None:
        if self.on_event is None:
            return
        event = {"type": event_type, "done": self._done, "total": self._total, **payload}
        outcome = self.on_event(event)
        if inspect.isawaitable(outcome):
            await outcome

    def _cache(self, result: DomainResult) -> None:
        """저장분에 검사 방식 번호를 함께 찍는다(옛 방식 결과를 재사용하지 않게)."""
        payload = result.model_dump(mode="json")
        payload["engine"] = ENGINE_VERSION
        cache.save(result.domain, payload, self.base)

    def stop(self) -> None:
        """Ask the run to stop; finished domains stay in the cache for resume."""
        self.cancel.set()

    async def run(self, domains: list[str], use_cache: bool = True) -> list[DomainResult]:
        self._total = len(domains)
        self._done = 0
        results: list[DomainResult] = []
        if not domains:
            return results

        # 중단되거나 앱이 꺼져도 "이어서 검사"가 살아 있도록 목록을 남긴다.
        self.base.mkdir(parents=True, exist_ok=True)
        save_run_state(self.base, domains)

        own_http = self.http is None
        http = self.http or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "domainchecker/0.1 (+local tool)"},
            follow_redirects=True,
        )
        try:
            await self._emit("start", domains=domains)
            pending = []
            reused: list[DomainResult] = []
            for domain in domains:
                cached = cache.load(domain, self.base) if use_cache else None
                if cached and not is_stale(cached, self.config.cache_days):
                    try:
                        found = DomainResult.model_validate(cached)
                    except ValueError:
                        found = None  # 캐시가 깨졌으면 그냥 다시 검사한다
                    if found is not None:
                        results.append(found)
                        reused.append(found)
                        self._done += 1
                        await self._emit("domain_done", domain=domain, cached=True, result=cached)
                        continue
                pending.append(domain)

            # 2단 — 공개 위험 명단을 한 번만 받아 두고, 이번 판의 도메인을 한꺼번에
            # 걸러 둔다. 명단 훑기는 파일 읽기라 딴 일을 멈추게 하지 않도록 옆으로 뺀다.
            if pending:
                await self._emit(
                    "notice", stage=STAGE_LIST, label="세계 위험 명단을 받아 맞춰 보는 중"
                )
                await self.lists.refresh(http)
                if self.lists.available:
                    self._blocked = await asyncio.to_thread(self.lists.screen, pending)

            semaphore = asyncio.Semaphore(max(1, self.config.concurrency))

            async def worker(domain: str) -> DomainResult | None:
                if self.cancel.is_set():
                    return None
                async with semaphore:
                    if self.cancel.is_set():
                        return None
                    await self._emit("domain_start", domain=domain)
                    result = await self.check_domain(domain, http)
                    self._cache(result)
                    self._done += 1
                    await self._emit(
                        "domain_done",
                        domain=domain,
                        cached=False,
                        result=result.model_dump(mode="json"),
                    )
                    return result

            # 한 도메인이 터져도 나머지 결과와 보고서까지 잃지 않는다.
            # 중단(cancel)이 오면 돌던 도메인도 그 자리에서 끊는다 — 안 끊으면 중단을
            # 눌러도 몇 분씩 계속 도는 것처럼 보인다. 끊긴 도메인은 결과가 없으니
            # run_state에 남아 '이어서 분석'이 다시 한다.
            tasks = [asyncio.create_task(worker(d)) for d in pending]
            gather_task = asyncio.gather(*tasks, return_exceptions=True)
            cancel_watch = asyncio.create_task(self.cancel.wait())
            await asyncio.wait(
                {asyncio.ensure_future(gather_task), cancel_watch},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if self.cancel.is_set():
                for task in tasks:
                    task.cancel()
            cancel_watch.cancel()
            gathered = await gather_task
            fresh: list[DomainResult] = []
            for domain, item in zip(pending, gathered, strict=True):
                if isinstance(item, asyncio.CancelledError):
                    continue  # 중단으로 끊김 — 실패가 아니라 '아직 안 한 것'
                if isinstance(item, BaseException):
                    message = f"{domain}: {type(item).__name__}: {item}"
                    self._failed.append(message)
                    self._done += 1
                    await self._emit("domain_failed", domain=domain, message=message)
                elif item is not None:
                    fresh.append(item)
            results.extend(fresh)
            # 표 먼저, 사진 나중 — 캡쳐는 모든 검사가 끝난 뒤 후행으로 돈다.
            # 이어서 검사로 되살린 결과에 사진이 없으면 그것도 함께 찍는다.
            await self.capture_phase(fresh + [r for r in reused if not r.captures.items])
        finally:
            if own_http:
                await http.aclose()

        order = {d: i for i, d in enumerate(domains)}
        results.sort(key=lambda r: order.get(r.domain, 0))
        self.write_results(results)
        if results:
            # 보고서는 사람이 단추를 누르지 않아도 항상 최신으로 준비해 둔다.
            try:
                report_html.write_report(results, self.base)
                await self._emit("report_ready", url="/report/index.html")
            except OSError as exc:
                await self._emit("report_error", message=f"{type(exc).__name__}: {exc}")
        stopped = self.cancel.is_set()
        if not stopped:
            clear_run_state(self.base)  # 끝까지 갔으면 이어서 할 것이 없다
        await self._emit("finished", stopped=stopped, failed=list(self._failed))
        return results

    def _finish(self, result: DomainResult, note: str, sections: tuple) -> DomainResult:
        """여기서 끝 — 안 돌린 검사를 '안 함'으로 정직하게 적고 판정을 낸다."""
        for section in sections:
            section.check = CheckState(status=CheckStatus.NOT_RUN, note=note)
        scoring.judge(result)
        result.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        return result

    def _finish_taken(self, result: DomainResult) -> DomainResult:
        """주인이 있는 도메인 — 살 수 없으니 남은 분석에 시간과 호출량을 쓰지 않는다."""
        return self._finish(
            result,
            "주인이 있는 도메인이라 나머지 분석은 하지 않았습니다(살 수 없음).",
            (
                result.blocklist,
                result.wayback,
                result.spamhaus,
                result.index,
                result.safebrowsing,
                result.ai,
                result.rules,
            ),
        )

    def _blocklist_state(self, domain: str) -> Reputation:
        """2단 결과 — 명단에 걸렸는지, 아니면 명단 자체를 못 봤는지."""
        state = Reputation()
        if not self.lists.available:
            state.check = CheckState(status=CheckStatus.NOT_RUN, note=self.lists.note)
            return state
        codes = self._blocked.get(domain) or []
        if codes:
            state.listed = True
            state.codes = codes
            state.check = CheckState(
                status=CheckStatus.OK, note="위험 명단에 올라 있습니다: " + ", ".join(codes)
            )
        else:
            state.check = CheckState(status=CheckStatus.OK, note=self.lists.note)
        return state

    async def screen_recent(
        self,
        domain: str,
        wayback: WaybackClient,
        history: WaybackHistory,
        client: OpenRouterClient | None,
    ) -> tuple[RuleFindings | None, SpamJudgement | None, bool]:
        """3단 — 가장 최근 화면 **딱 한 장**만 받아 보고 "지금 스팸인가"만 따진다.

        (규칙 검사 결과, AI의 스팸 판정, 확정 스팸인가)를 돌려준다. 확정일 때만
        마지막 값이 참이다. 못 받았거나 AI가 답을 못 주면 그건 "판정 불가"이지
        "합격"이 아니다 — 거짓으로 돌려보내 4단(전수 정독)에서 다시 보게 한다.

        기계 규칙만으로 확정되면 AI에게는 아예 묻지 않는다 — 판정이 안 바뀌는데
        도메인마다 돈이 드는 호출이다.
        """
        snapshot = history.latest
        if snapshot is None:
            return None, None, False
        html = await wayback.fetch_snapshot(snapshot)
        if html is None:
            return None, None, False  # 못 읽음 = 판정 불가 → 4단으로

        content = snapshot_from_page(
            {
                "timestamp": snapshot.timestamp,
                "url": snapshot.raw_url,
                "html": html,
                "fetched": True,
            },
            domain,
            self.config.snapshot_text_limit,
        )
        # ① 기계 규칙 — 이미 있는 계산 규칙(숨긴 글자·링크 무더기·키워드 나열)을
        #    그 한 장에 그대로 돌린다. 흔적이 3종 겹쳐야 확정이다(4단과 같은 잣대).
        findings = rules_analyze.analyze([content])
        probe = DomainResult(domain=domain, rules=findings)
        if scoring.fatal_reasons(probe):
            return findings, None, True
        # ② AI — 뜻을 읽어야 잡히는 것(위험 업종 영업 화면 등)은 AI가 본다.
        spam = await ai_analyze.screen_latest(domain, content, client)
        confirmed = bool(
            spam is not None
            and spam.verdict == "spam"
            and spam.confidence >= scoring.AI_FATAL_CONFIDENCE
            and spam.quotes
        )
        return findings, spam, confirmed

    async def check_domain(self, domain: str, http: httpx.AsyncClient) -> DomainResult:
        """도메인 하나의 검사 — 4단 깔때기. 어느 검사가 터져도 미확인으로만 내려앉는다.

        1단(주인 여부) 살 수 있나 — 주인이 있으면 볼 것도 없이 여기서 끝.
        2단(공짜) 받아 둔 세계 공개 위험 명단에 이름이 있나 → 있으면 여기서 끝.
        3단(최근 모습) 가장 최근 화면 **한 장**만 보고, 같은 묶음에서 블랙리스트·
           색인·웨이백 목록(싼 조회)도 함께 돌린다. 스팸이 확정되면 여기서 끝.
        4단(비싼 정독) 살아남은 것만 옛 화면 본문을 전수로 읽고 AI가 판정한다.

        앞 세 단은 **"나쁘다"만 판정할 수 있다.** "깨끗해 보인다"로는 어느 단도
        매입 후보를 만들지 못하고, 매입 후보는 반드시 4단을 지난다.
        """
        result = DomainResult(domain=domain)
        current = {"stage": STAGE_OWNER}

        async def step(label: str, frac: float, stage: str = "") -> None:
            # 한 도메인 안에서 지금 무엇을 하는지 — 화면 막대가 이 토막만큼 움직인다
            await self._emit(
                "step", domain=domain, label=label, frac=frac, stage=stage or current["stage"]
            )

        # ── 1단 — 주인 여부. 계산으로 금방 끝나고 AI가 필요 없는 검사라 맨 앞이다.
        await step("살 수 있는 도메인인지 확인 중", 0.05)
        try:
            result.registration = await gabia.check(domain)
        except Exception as exc:  # noqa: BLE001 — 등록 조회가 터져도 나머지 검사는 계속한다
            result.errors.append(f"registration: {type(exc).__name__}: {exc}")
            result.registration.check = CheckState(
                status=CheckStatus.UNCHECKED,
                note=f"검사 중 오류가 났습니다({type(exc).__name__}).",
            )
        if scoring.availability_of(result.registration.acquisition) == "taken":
            return self._finish_taken(result)

        # ── 2단 — 바깥으로 나가지 않는다. 판 시작 때 받아 둔 명단만 본다.
        current["stage"] = STAGE_LIST
        result.blocklist = self._blocklist_state(domain)
        if result.blocklist.listed:
            await step("위험 명단에 올라 있어 여기서 끝", 1.0)
            return self._finish(
                result,
                "세계 공개 위험 명단에 올라 있어 나머지 분석은 하지 않았습니다.",
                (
                    result.wayback,
                    result.spamhaus,
                    result.index,
                    result.safebrowsing,
                    result.ai,
                    result.rules,
                ),
            )

        # ── 3단 — 가장 최근 모습 한 장 + 싼 조회 묶음. 웨이백은 여기서 **목록 조회
        # 한 번**만 한다 — 기록이 얼마나 되는지, 빈 해가 있는지, 넘겨보내기만 하던
        # 시절이 있는지, 그리고 가장 최근 저장분 주소가 그 한 번에 다 나온다.
        current["stage"] = STAGE_RECENT
        await step("가장 최근 모습과 옛날 기록(웨이백) 규모 보는 중", 0.15)
        wayback = WaybackClient(
            http,
            self.wayback_limiter,
            on_wait=lambda: step(
                f"웨이백이 잠깐 막아 {round(self.wayback_limiter.backoff / 2)}초 쉬는 중", 0.3
            ),
        )

        collected = await asyncio.gather(
            wayback.timeline(domain),
            spamhaus.check(domain, self.resolver),
            freeindex.check(domain, http),
            safebrowsing.check(domain, http),
            return_exceptions=True,
        )
        names = ("wayback", "spamhaus", "index", "safebrowsing")
        for name, value in zip(names, collected, strict=True):
            if isinstance(value, BaseException):
                result.errors.append(f"{name}: {type(value).__name__}: {value}")
                getattr(result, name).check = CheckState(
                    status=CheckStatus.UNCHECKED, note=f"검사 중 오류가 났습니다({type(value).__name__})."
                )
            else:
                setattr(result, name, value)

        # 3단 즉시 제외 ① — **위험이 눈에 보일 때만**이다(블랙리스트 등재 같은 사실).
        if scoring.fatal_reasons(result):
            await step("블랙리스트에 걸려 여기서 끝", 1.0)
            return self._finish(
                result,
                "블랙리스트에 올라 있어 옛 화면 정독은 하지 않았습니다.",
                (result.ai, result.rules),
            )
        # 옛 기록이 아예 없으면 읽을 본문도 없다 — 몇 초 만에 끝난다.
        if not (result.wayback.check.ok and result.wayback.has_history):
            note = (
                "저장된 옛 화면이 없어 본문 정독은 하지 않았습니다."
                if result.wayback.check.ok
                else "과거 이력을 확인하지 못해 본문 정독까지 가지 못했습니다."
            )
            return self._finish(result, note, (result.ai, result.rules))

        client = (
            OpenRouterClient(http, self.config.keys.openrouter, self.config.model_chain())
            if self.config.keys.openrouter
            else None
        )

        # 3단 즉시 제외 ② — 가장 최근 화면 한 장이 확정 스팸이면 옛 화면은 안 읽는다.
        await step("가장 최근 화면 한 장 보는 중", 0.2)
        latest_rules, latest_spam, recent_spam = await self.screen_recent(
            domain, wayback, result.wayback, client
        )
        if recent_spam:
            result.rules = latest_rules
            if latest_spam is not None:
                result.ai.spam = latest_spam
                result.ai.verdict = "reject"
                result.ai.verdict_reason = "가장 최근 화면이 스팸으로 판정됐습니다."
                result.ai.check = CheckState(
                    status=CheckStatus.OK,
                    note="가장 최근 화면 한 장만 AI가 보고 판정했습니다(옛 화면 전수 정독은 하지 않았습니다).",
                )
            else:
                result.ai.check = CheckState(
                    status=CheckStatus.NOT_RUN,
                    note="가장 최근 화면만으로 제외가 확정되어 AI 판정은 건너뛰었습니다.",
                )
            result.wayback.coverage_note = (
                "가장 최근 화면 1장을 읽은 자리에서 제외가 확정되어 옛 화면 전수 정독은 하지 않았습니다."
            )
            result.wayback.subpages = []
            await step("가장 최근 화면이 스팸이라 여기서 끝", 1.0)
            return self._finish(result, "", ())

        # ── 4단 — 여기부터가 비싼 정독이다. 살아남은 도메인만 들어온다.
        current["stage"] = STAGE_DEEP
        # 기계 거부권이 확정되면 남은 장을 더 읽지 않는다 — 판정이 안 바뀌는 읽기다.
        # 읽은 화면은 한 번만 뜯어 두고(같은 장을 다시 뜯으면 장수의 제곱만큼 느려진다),
        # 새 장이 몇 개 쌓였을 때만 다시 따져 본다.
        veto = {"hit": False, "seen": 0}
        parsed: list = []

        def machine_veto() -> bool:
            if veto["hit"] or self.cancel.is_set():
                return True
            pages = result.wayback.pages
            if len(pages) < VETO_MIN_PAGES or len(pages) - veto["seen"] < VETO_EVERY:
                return False
            veto["seen"] = len(pages)
            while len(parsed) < len(pages):
                parsed.append(
                    snapshot_from_page(pages[len(parsed)], domain, self.config.snapshot_text_limit)
                )
            probe = DomainResult(domain=domain, rules=rules_analyze.analyze(parsed))
            veto["hit"] = bool(scoring.fatal_reasons(probe))
            return veto["hit"]

        async def page_progress(i: int, n: int) -> None:
            await step(f"옛 화면 {i}/{n}장 읽는 중", 0.2 + 0.3 * i / max(n, 1), STAGE_DEEP)

        try:
            result.wayback = await wayback.deep_read(
                domain, result.wayback, on_progress=page_progress, should_stop=machine_veto
            )
        except Exception as exc:  # noqa: BLE001 — 정독이 터져도 3단까지의 사실은 지킨다
            result.errors.append(f"wayback: {type(exc).__name__}: {exc}")
            result.wayback.check = CheckState(
                status=CheckStatus.UNCHECKED, note=f"옛 화면 정독 중 오류가 났습니다({type(exc).__name__})."
            )

        # 하위 페이지 정독 — 내용 단위의 전수. 페이지 수천 장을 통째로 받는 대신,
        # ① 주소를 모양(유형)으로 묶어 존재하는 모든 유형의 대표를 반드시 읽고
        #    (같은 틀로 찍은 페이지 1,000장은 같은 내용이므로 대표가 곧 전수다)
        # ② 그 위에 AI가 전체 주소 목록을 훑어 수상한 주소를 더 고르고
        # ③ 이미 읽은 것과 내용이 같은(digest 동일) 저장분은 두 번 읽지 않는다.
        # 이미 기계 거부권이 확정됐으면 하위 페이지는 아예 손대지 않는다 —
        # AI 선별(돈이 드는 호출)도, 본문 받기도 판정을 바꾸지 못한다.
        if result.wayback.subpages and not machine_veto():
            await step("하위 페이지 골라 읽는 중", 0.55, STAGE_DEEP)
            reps, kinds = cluster_representatives(
                result.wayback.subpages, result.wayback.gap_years
            )
            triage_fail = ""
            chosen = await ai_analyze.pick_paths(
                domain, result.wayback.path_samples, client
            ) if client else []
            if chosen is None:
                triage_fail = " AI의 수상 주소 선별은 실패해 유형 대표만 정독했습니다."
                chosen = []
            by_entry = {
                f"{snap.year} {wayback_short_path(snap.original)}": snap
                for snap in result.wayback.subpages
            }
            picked = reps + [by_entry[entry] for entry in chosen if entry in by_entry]

            seen_digests = {s.digest for s in result.wayback.selected if s.digest}
            seen_keys: set[str] = set()
            targets = []
            for snap in picked:
                key = snap.timestamp + snap.original
                if key in seen_keys or (snap.digest and snap.digest in seen_digests):
                    continue
                seen_keys.add(key)
                if snap.digest:
                    seen_digests.add(snap.digest)
                targets.append(snap)

            sub_pages = await wayback.read_subpages(targets, should_stop=machine_veto)
            result.wayback.pages.extend(sub_pages)
            read = sum(1 for p in sub_pages if p["fetched"])
            cut = (
                " 제외가 확정돼 남은 곳은 읽지 않았습니다."
                if len(sub_pages) < len(targets)
                else ""
            )
            result.wayback.coverage_note += (
                f" 하위 주소는 유형 {kinds}가지로 묶어 유형·시대 대표와 AI 선별을 합쳐 "
                f"{len(targets)}곳 중 {read}곳 본문 정독.{triage_fail}{cut}"
            )
        result.wayback.subpages = []  # 선별용 목록은 여기서 소진 — 결과에 안 싣는다

        await step("읽은 화면에서 위험한 흔적 찾는 중", 0.65, STAGE_DEEP)
        snapshots = [
            snapshot_from_page(page, domain, self.config.snapshot_text_limit)
            for page in result.wayback.pages
        ]
        result.rules = rules_analyze.analyze(snapshots)
        # 원본 HTML은 저장하지 않는다(캐시·결과 파일이 수십 MB로 불어남).
        result.wayback.pages = [
            {
                "timestamp": s.timestamp,
                "url": s.url,
                "fetched": s.fetched,
                "title": s.title,
                "lang": s.lang,
                "parking": s.parking,
                "text": s.text,
            }
            for s in snapshots
        ]

        # 규칙 검사만으로 제외가 확정됐으면 AI에게 묻지 않는다 — 판정이 안 바뀌는데
        # 도메인마다 돈이 드는 호출이라 그대로 두면 걸러 낸 만큼이 그냥 나간다.
        if scoring.fatal_reasons(result):
            result.ai.check = CheckState(
                status=CheckStatus.NOT_RUN,
                note="기계적 검사만으로 제외가 확정되어 AI 판정은 건너뛰었습니다.",
            )
        else:
            if client is not None:
                await step("AI가 과거 내용 읽고 판정 쓰는 중", 0.7, STAGE_DEEP)
            result.ai = await ai_analyze.analyze(
                domain,
                snapshots,
                client,
                context=self._ai_context(result),
                input_limit=self.config.ai_input_limit,
            )

        scoring.judge(result)
        result.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        return result

    async def capture_phase(self, results: list[DomainResult]) -> None:
        """Screenshots for every finished domain, one after another (shared limiter)."""
        # 중단된 판이면 사진 단계를 아예 열지 않는다 — "이제 사진을 찍습니다" 문구가
        # 방금 누른 "중단했습니다"를 덮으면 화면이 거짓말이 된다(진상 검증 지적).
        if not results or not self.config.enable_capture or self.cancel.is_set():
            return
        await self._emit("capture_start", count=len(results))
        for i, result in enumerate(results, start=1):
            if self.cancel.is_set():
                break
            # 사진 한 판이 수십 초 걸린다 — 몇 번째인지와 초 시계가 붙게 step 으로 알린다
            await self._emit(
                "step",
                domain=result.domain,
                label=f"옛 화면 사진 찍는 중 ({i}/{len(results)}번째)",
                frac=1.0,
            )
            result.captures = await capture.capture_domain(
                result, self.base, self.wayback_limiter, self.config.enable_capture
            )
            self._cache(result)
            await self._emit(
                "capture_done",
                domain=result.domain,
                shots=len(result.captures.items),
                note=result.captures.check.note,
                # 사진이 붙은 최신 결과를 함께 실어 보낸다. 이게 없으면 화면·메모리에
                # 캡쳐 이전 결과만 남아 찍은 사진이 끝내 보이지 않는다.
                result=result.model_dump(mode="json"),
            )

    def _ai_context(self, result: DomainResult) -> dict:
        history = result.wayback
        timeline = (
            f"첫 저장 {history.first_seen[:8] or '없음'}, 마지막 저장 {history.last_seen[:8] or '없음'}, "
            f"총 {history.total_captures}건, 연도별 {history.year_counts}, "
            f"기록 공백 연도 {history.gap_years or '없음'}, 리다이렉트 비율 {history.redirect_ratio}"
        )
        registration = (
            f"등록일 {result.registration.created or '알 수 없음'}, "
            f"만료일 {result.registration.expires or '알 수 없음'}, "
            f"상태 {result.registration.acquisition}"
        )
        return {
            "timeline": timeline,
            "registration": registration,
            "index_titles": result.index.titles,
            "index_paths": result.index.sample_paths,
            "history_paths": result.wayback.path_samples,
            "rule_hints": result.rules.evidence,
        }

    def write_results(self, results: list[DomainResult]) -> Path:
        path = self.base / "results.json"
        # 지난 회차 결과 위에 덮어쓰지 않고 합친다 — 묶음을 나눠 검사해도 목록이
        # 쌓여야 한다. 같은 도메인은 이번 결과가 이긴다(다시 검사한 쪽이 최신).
        fresh = {r.domain: r.model_dump(mode="json") for r in results}
        kept: list[dict] = []
        if path.exists():
            try:
                for row in json.loads(path.read_text(encoding="utf-8")).get("results", []):
                    domain = str(row.get("domain", ""))
                    if domain and domain not in fresh:
                        kept.append(row)
            except (OSError, ValueError):
                kept = []  # 깨진 파일이면 이번 결과로 새로 만든다
        merged = kept + list(fresh.values())
        payload = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "count": len(merged),
            "results": merged,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
