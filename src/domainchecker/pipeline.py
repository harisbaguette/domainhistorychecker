"""Per-domain orchestration. Emits progress events for the (later) SSE layer."""

from __future__ import annotations

import asyncio
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
from .clients import openpagerank, safebrowsing, serper, spamhaus, virustotal
from .clients.openrouter import OpenRouterClient
from .clients.rdap import RdapClient
from .clients.wayback import WaybackClient
from .config import Config, data_dir
from .models import Authority, CheckState, CheckStatus, DomainResult
from .ratelimit import AdaptiveRateLimiter

EventCallback = Callable[[dict], None] | None


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

    async def _emit(self, event_type: str, **payload) -> None:
        if self.on_event is None:
            return
        event = {"type": event_type, "done": self._done, "total": self._total, **payload}
        outcome = self.on_event(event)
        if inspect.isawaitable(outcome):
            await outcome

    def stop(self) -> None:
        """Ask the run to stop; finished domains stay in the cache for resume."""
        self.cancel.set()

    async def run(self, domains: list[str], use_cache: bool = True) -> list[DomainResult]:
        self._total = len(domains)
        self._done = 0
        results: list[DomainResult] = []
        if not domains:
            return results

        own_http = self.http is None
        http = self.http or httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"User-Agent": "domainchecker/0.1 (+local tool)"},
            follow_redirects=True,
        )
        try:
            await self._emit("start", domains=domains)
            pending = []
            for domain in domains:
                cached = cache.load(domain, self.base) if use_cache else None
                if cached:
                    try:
                        results.append(DomainResult.model_validate(cached))
                        self._done += 1
                        await self._emit("domain_done", domain=domain, cached=True, result=cached)
                        continue
                    except ValueError:
                        pass  # 캐시가 깨졌으면 그냥 다시 검사한다
                pending.append(domain)

            authority = await openpagerank.fetch_batch(
                pending, self.config.keys.openpagerank, http
            )
            semaphore = asyncio.Semaphore(max(1, self.config.concurrency))

            async def worker(domain: str) -> DomainResult | None:
                if self.cancel.is_set():
                    return None
                async with semaphore:
                    if self.cancel.is_set():
                        return None
                    await self._emit("domain_start", domain=domain)
                    result = await self.check_domain(domain, http, authority.get(domain))
                    cache.save(domain, result.model_dump(mode="json"), self.base)
                    self._done += 1
                    await self._emit(
                        "domain_done",
                        domain=domain,
                        cached=False,
                        result=result.model_dump(mode="json"),
                    )
                    return result

            gathered = await asyncio.gather(*(worker(d) for d in pending))
            fresh = [r for r in gathered if r is not None]
            results.extend(fresh)
            # 표 먼저, 사진 나중 — 캡쳐는 모든 검사가 끝난 뒤 후행으로 돈다.
            await self.capture_phase(fresh)
        finally:
            if own_http:
                await http.aclose()

        order = {d: i for i, d in enumerate(domains)}
        results.sort(key=lambda r: order.get(r.domain, 0))
        self.write_results(results)
        await self._emit("finished", stopped=self.cancel.is_set())
        return results

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
            serper.check(domain, self.config.keys.serper, http),
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
            cache.save(result.domain, result.model_dump(mode="json"), self.base)
            await self._emit(
                "capture_done",
                domain=result.domain,
                shots=len(result.captures.items),
                note=result.captures.check.note,
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
        payload = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "count": len(results),
            "results": [r.model_dump(mode="json") for r in results],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
