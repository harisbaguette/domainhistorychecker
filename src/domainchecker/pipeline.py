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
from .clients.openrouter import OpenRouterClient
from .clients.wayback import WaybackClient, cluster_representatives
from .clients.wayback import short_path as wayback_short_path
from .config import Config, data_dir
from .models import CheckState, CheckStatus, DomainResult
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
ENGINE_VERSION = 12


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
            gathered = await asyncio.gather(
                *(worker(d) for d in pending), return_exceptions=True
            )
            fresh: list[DomainResult] = []
            for domain, item in zip(pending, gathered, strict=True):
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

    def _finish_taken(self, result: DomainResult) -> DomainResult:
        """주인이 있는 도메인 — 살 수 없으니 남은 분석에 시간과 호출량을 쓰지 않는다."""
        note = "주인이 있는 도메인이라 나머지 분석은 하지 않았습니다(살 수 없음)."
        for section in (
            result.wayback,
            result.spamhaus,
            result.index,
            result.safebrowsing,
            result.ai,
            result.rules,
        ):
            section.check = CheckState(status=CheckStatus.NOT_RUN, note=note)
        scoring.judge(result)
        result.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        return result

    async def check_domain(self, domain: str, http: httpx.AsyncClient) -> DomainResult:
        """All checks for one domain. Any single failure degrades to 미확인.

        구매 가능 여부를 맨 먼저 본다 — 판매처(가비아)에 검색창과 같은 요청을
        보내 답을 받는다. 살 수 없는 도메인이면 웨이백·색인·AI 같은 나머지
        분석은 아예 하지 않는다(시간·호출량 절약).
        """
        result = DomainResult(domain=domain)
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

        wayback = WaybackClient(http, self.wayback_limiter)
        collected = await asyncio.gather(
            wayback.collect(domain),
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

        client = (
            OpenRouterClient(http, self.config.keys.openrouter, self.config.model_chain())
            if self.config.keys.openrouter
            else None
        )

        # 하위 페이지 정독 — 내용 단위의 전수. 페이지 수천 장을 통째로 받는 대신,
        # ① 주소를 모양(유형)으로 묶어 존재하는 모든 유형의 대표를 반드시 읽고
        #    (같은 틀로 찍은 페이지 1,000장은 같은 내용이므로 대표가 곧 전수다)
        # ② 그 위에 AI가 전체 주소 목록을 훑어 수상한 주소를 더 고르고
        # ③ 이미 읽은 것과 내용이 같은(digest 동일) 저장분은 두 번 읽지 않는다.
        if result.wayback.subpages:
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

            sub_pages = await wayback.read_subpages(targets)
            result.wayback.pages.extend(sub_pages)
            read = sum(1 for p in sub_pages if p["fetched"])
            result.wayback.coverage_note += (
                f" 하위 주소는 유형 {kinds}가지로 묶어 유형·시대 대표와 AI 선별을 합쳐 "
                f"{len(targets)}곳 중 {read}곳 본문 정독.{triage_fail}"
            )
        result.wayback.subpages = []  # 선별용 목록은 여기서 소진 — 결과에 안 싣는다

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
        if not results or not self.config.enable_capture:
            return
        await self._emit("capture_start", count=len(results))
        for result in results:
            if self.cancel.is_set():
                break
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
