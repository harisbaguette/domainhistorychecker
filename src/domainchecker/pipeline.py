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
from .clients import freeindex, openpagerank, safebrowsing, serper, spamhaus, virustotal
from .clients.openrouter import OpenRouterClient
from .clients.rdap import RdapClient
from .clients.wayback import WaybackClient
from .config import Config, data_dir
from .models import Authority, CheckState, CheckStatus, DomainResult
from .ratelimit import AdaptiveRateLimiter
from .report import html as report_html

EventCallback = Callable[[dict], None] | None

RUN_STATE_NAME = "run_state.json"

# 검사 방식이 바뀌면 올린다. 저장분에 찍힌 번호가 이보다 낮으면 다시 검사한다 —
# 안 그러면 "키가 없어 못 했습니다"라고 적힌 예전 결과를 일주일 동안 계속 보여 준다.
ENGINE_VERSION = 3


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
        whois_query=None,
    ) -> None:
        self.config = config
        self.on_event = on_event
        self.http = http
        self.resolver = resolver
        self.whois_query = whois_query
        self.base = Path(base_dir) if base_dir else data_dir(config)
        self.wayback_limiter = AdaptiveRateLimiter(rpm=config.start_rpm)
        self.vt_limiter = virustotal.make_limiter()
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

            authority = await self._authority_for(pending, http)
            semaphore = asyncio.Semaphore(max(1, self.config.concurrency))

            async def worker(domain: str) -> DomainResult | None:
                if self.cancel.is_set():
                    return None
                async with semaphore:
                    if self.cancel.is_set():
                        return None
                    await self._emit("domain_start", domain=domain)
                    result = await self.check_domain(domain, http, authority.get(domain))
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

    async def _authority_for(
        self, pending: list[str], http: httpx.AsyncClient
    ) -> dict[str, Authority]:
        """권위 점수 한 번에 조회. 여기서 터지면 검사가 통째로 죽으므로 반드시 삼킨다.

        선택 검사다 — 키가 없으면 그냥 비워 두고, 판정에는 쓰지 않는다.
        """
        try:
            return await openpagerank.fetch_batch(pending, self.config.keys.openpagerank, http)
        except Exception as exc:  # noqa: BLE001 — 어떤 응답이 와도 나머지 검사는 계속한다
            state = CheckState(
                status=CheckStatus.UNCHECKED,
                note=f"권위 점수 조회에 실패했습니다({type(exc).__name__}).",
            )
            return {d: Authority(check=state) for d in pending}

    async def _index_check(self, domain: str, http: httpx.AsyncClient):
        """색인 검사. 키가 있으면 Serper, 없으면 키 없이 도는 무료 공개 자료."""
        if self.config.keys.serper:
            return await serper.check(domain, self.config.keys.serper, http)
        return await freeindex.check(domain, http)

    async def check_domain(
        self, domain: str, http: httpx.AsyncClient, authority: Authority | None = None
    ) -> DomainResult:
        """All checks for one domain. Any single failure degrades to 미확인."""
        result = DomainResult(domain=domain)
        wayback = WaybackClient(http, self.wayback_limiter, self.config.max_snapshots)
        rdap = RdapClient(http, whois_query=self.whois_query)

        collected = await asyncio.gather(
            wayback.collect(domain),
            rdap.fetch(domain),
            spamhaus.check(domain, self.resolver),
            self._index_check(domain, http),
            safebrowsing.check(
                domain,
                self.config.keys.safebrowsing if self.config.enable_safebrowsing else "",
                http,
            ),
            virustotal.check(
                domain,
                self.config.keys.virustotal,
                http,
                self.vt_limiter,
                self.config.enable_virustotal,
            ),
            return_exceptions=True,
        )
        names = ("wayback", "registration", "spamhaus", "index", "safebrowsing", "virustotal")
        for name, value in zip(names, collected, strict=True):
            if isinstance(value, BaseException):
                result.errors.append(f"{name}: {type(value).__name__}: {value}")
                getattr(result, name).check = CheckState(
                    status=CheckStatus.UNCHECKED, note=f"검사 중 오류가 났습니다({type(value).__name__})."
                )
            else:
                setattr(result, name, value)

        if not self.config.enable_safebrowsing and result.safebrowsing.check.status is CheckStatus.NOT_RUN:
            result.safebrowsing.check = CheckState(
                status=CheckStatus.NOT_RUN, note="세이프 브라우징 검사를 껐습니다."
            )

        result.authority = authority or Authority(
            check=CheckState(status=CheckStatus.NOT_RUN, note="권위 점수를 조회하지 않았습니다.")
        )

        snapshots = [
            snapshot_from_page(page, domain, self.config.snapshot_text_limit)
            for page in result.wayback.pages
        ]
        result.rules = rules_analyze.analyze(snapshots, domain, result.index.titles)
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

        client = (
            OpenRouterClient(http, self.config.keys.openrouter, self.config.model_chain())
            if self.config.keys.openrouter
            else None
        )
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
            "rule_hints": result.rules.evidence,
            "sensitive_terms": result.rules.sensitive_terms,
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
